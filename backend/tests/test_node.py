"""节点与设备上线测试。契约 2.8。"""
import pytest

from core.gm_crypto import sign as sm2_sign


def test_节点列表字段与前端mock对齐(client, login):
    data = client.get("/api/v1/nodes", headers=login("admin")).json()["data"]
    assert data["total"] == 4
    node = next(n for n in data["items"] if n["id"] == "Node-A")
    # 这四个字段名一个字母都不能改，乙那边七个存量页面直接依赖
    assert set(node["metrics"]) == {"pvOutput", "storageOutput", "load", "soc"}
    assert node["metrics"]["pvOutput"] == 45.3
    assert node["metrics"]["storageOutput"] == -12.0
    assert node["metrics"]["load"] == 120.0
    assert node["metrics"]["soc"] == 65.0
    assert node["model"] == "VPP-2000"
    assert node["status"] == "online"


def test_节点状态枚举符合契约(client, login):
    data = client.get("/api/v1/nodes", headers=login("admin")).json()["data"]
    assert {n["status"] for n in data["items"]} <= {"online", "warning", "offline"}
    assert next(n for n in data["items"] if n["id"] == "Node-C")["status"] == "warning"


def test_节点详情(client, login):
    data = client.get("/api/v1/nodes/Node-B", headers=login("admin")).json()["data"]
    assert data["name"] == "虚拟电厂节点B"
    assert data["latestMetricAt"] is not None


def test_不存在的节点返回1005(client, login):
    r = client.get("/api/v1/nodes/Node-X", headers=login("admin"))
    assert r.status_code == 404 and r.json()["code"] == 1005


def test_历史指标可降采样(client, login):
    admin = login("admin")
    raw = client.get("/api/v1/nodes/Node-A/metrics?interval=raw&from=2026-08-01T00:00:00",
                     headers=admin).json()["data"]
    day = client.get("/api/v1/nodes/Node-A/metrics?interval=day&from=2026-08-01T00:00:00",
                     headers=admin).json()["data"]
    assert raw["total"] == 48
    assert day["total"] == 2          # 48 小时按天降采样
    assert set(raw["items"][0]) == {"ts", "pvOutput", "storageOutput", "load", "soc", "price"}


def test_指标接口有硬上限(client, login):
    """树莓派上禁止一次性拉几万个点。"""
    from modules.node.service import _MAX_POINTS

    assert _MAX_POINTS <= 2000
    data = client.get("/api/v1/nodes/Node-A/metrics?from=2026-08-01T00:00:00",
                      headers=login("admin")).json()["data"]
    assert data["truncated"] is False


def test_非法interval返回1001(client, login):
    r = client.get("/api/v1/nodes/Node-A/metrics?interval=minute", headers=login("admin"))
    assert r.status_code == 400 and r.json()["code"] == 1001


# ---------------------------------------------------------------- 设备上线

@pytest.fixture
def edge_identity(client, login):
    r = client.post("/api/v1/did/register", headers=login("admin"), json={
        "subjectType": "edge", "subjectName": "上线测试边缘节点", "custody": True,
    })
    return r.json()["data"]


def test_合法签名可上线(client, login, edge_identity):
    nonce = "nonce-online-001"
    signature = sm2_sign(nonce, edge_identity["privateKey"], edge_identity["publicKey"])
    r = client.post("/api/v1/nodes/Node-A/online", headers=login("admin"), json={
        "did": edge_identity["did"], "nonce": nonce, "signature": signature,
    })
    data = r.json()["data"]
    assert data["accepted"] is True
    assert data["nodeId"] == "Node-A"
    assert data["evidenceId"].startswith("ev-")


def test_签名无效直接拒绝接入(client, login, edge_identity):
    """无合法 DID 签名的节点一律拒绝——这是需求文档的硬要求。"""
    r = client.post("/api/v1/nodes/Node-B/online", headers=login("admin"), json={
        "did": edge_identity["did"], "nonce": "nonce-bad", "signature": "ab" * 64,
    })
    assert r.status_code == 403 and r.json()["code"] == 1004


def test_换个身份的签名也过不去(client, login, edge_identity):
    other = client.post("/api/v1/did/register", headers=login("admin"), json={
        "subjectType": "edge", "subjectName": "冒充者", "custody": True,
    }).json()["data"]
    nonce = "nonce-impersonate"
    # 用冒充者的私钥签，却声称自己是 edge_identity
    signature = sm2_sign(nonce, other["privateKey"], other["publicKey"])
    r = client.post("/api/v1/nodes/Node-B/online", headers=login("admin"), json={
        "did": edge_identity["did"], "nonce": nonce, "signature": signature,
    })
    assert r.status_code == 403 and r.json()["code"] == 1004


def test_冻结的身份不能上线(client, login, edge_identity):
    admin = login("admin")
    client.post(f"/api/v1/did/{edge_identity['did']}/status", headers=admin,
                json={"action": "freeze", "reason": "测试"})
    nonce = "nonce-frozen"
    signature = sm2_sign(nonce, edge_identity["privateKey"], edge_identity["publicKey"])
    r = client.post("/api/v1/nodes/Node-C/online", headers=admin, json={
        "did": edge_identity["did"], "nonce": nonce, "signature": signature,
    })
    assert r.status_code == 403 and r.json()["code"] == 1004


def test_nonce不能重放(client, login, edge_identity, monkeypatch):
    from core import redis_client

    used = set()
    monkeypatch.setattr(redis_client, "safe_exists", lambda k: k in used)
    monkeypatch.setattr(redis_client, "safe_set",
                        lambda k, v, ex=None: used.add(k) or True)

    admin = login("admin")
    nonce = "nonce-replay-001"
    signature = sm2_sign(nonce, edge_identity["privateKey"], edge_identity["publicKey"])
    body = {"did": edge_identity["did"], "nonce": nonce, "signature": signature}

    assert client.post("/api/v1/nodes/Node-D/online", headers=admin, json=body).status_code == 200
    replay = client.post("/api/v1/nodes/Node-D/online", headers=admin, json=body)
    assert replay.status_code == 403
    assert "重放" in replay.json()["message"]
