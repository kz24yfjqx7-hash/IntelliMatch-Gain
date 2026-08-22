"""数据资产测试：登记、分级、溯源、统计、own 范围隔离。"""
import pytest


@pytest.fixture
def device_did(client, login):
    r = client.post("/api/v1/did/register", headers=login("admin"), json={
        "subjectType": "device", "subjectName": "资产测试逆变器", "orgName": "测试园区",
        "custody": True,
    })
    return r.json()["data"]["did"]


def _create(client, headers, did, **kw):
    body = {
        "name": kw.get("name", "节点A光伏出力-测试"),
        "dataType": kw.get("dataType", "pv"),
        "sourceDid": did,
        "payload": kw.get("payload", {"pvOutput": 45.3, "ts": "2026-08-18T14:00:00+08:00"}),
        "description": "分钟级采集",
        "recordCount": kw.get("recordCount", 1440),
    }
    if "level" in kw:
        body["level"] = kw["level"]
    return client.post("/api/v1/assets", headers=headers, json=body)


def test_登记资产返回摘要与上链信息(client, login, device_did):
    r = _create(client, login("admin"), device_did, level="L2")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["hash"].startswith("sm3:") and len(data["hash"]) == 68
    assert data["level"] == "L2"
    assert data["evidenceId"].startswith("ev-")
    assert data["chainTxId"].startswith("blk-")
    assert data["authStatus"] == "unauthorized"


def test_原始数据存库链上只存摘要(client, login, device_did):
    from sqlalchemy import select

    from core.gm_crypto import payload_hash
    from modules.evidence.model import ChainEvidence

    payload = {"pvOutput": 88.8, "voltage": 380, "ts": "2026-08-18T15:00:00+08:00"}
    asset = _create(client, login("admin"), device_did, level="L3", payload=payload).json()["data"]

    detail = client.get(f"/api/v1/assets/{asset['id']}", headers=login("admin")).json()["data"]
    assert detail["payload"] == payload          # 原始数据在 MySQL 里

    from core.database import SessionLocal

    with SessionLocal() as db:
        ev = db.execute(
            select(ChainEvidence).where(ChainEvidence.evidence_id == asset["evidenceId"])
        ).scalar_one()
    # 链上快照只有摘要和元信息，没有原始 payload
    assert "pvOutput" not in ev.payload_snapshot
    assert ev.payload_snapshot["payloadHash"] == payload_hash(payload)


def test_不填等级则自动分级(client, login, device_did):
    """带地理位置 + 分钟级 + 大数据量 → 至少 L3。"""
    payload = {"pvOutput": 45.3, "location": {"lat": 39.9, "lng": 116.4}, "interval": "1min"}
    data = _create(client, login("admin"), device_did, payload=payload,
                   recordCount=1440).json()["data"]
    assert data["level"] in ("L3", "L4")
    assert data["classifySource"] in ("rule", "algo")
    assert "敏感字段" in data["classifyReason"] or "粒度" in data["classifyReason"]


def test_聚合数据判为低等级(client, login, device_did):
    payload = {"dailyTotal": 1200.5, "interval": "1day"}
    data = _create(client, login("admin"), device_did, payload=payload,
                   recordCount=30).json()["data"]
    assert data["level"] in ("L1", "L2")


def test_调度类数据天然更敏感(client, login, device_did):
    payload = {"command": "discharge", "powerKw": 24.0}
    pv = _create(client, login("admin"), device_did, dataType="pv",
                 payload=payload, recordCount=100).json()["data"]
    dispatch = _create(client, login("admin"), device_did, dataType="dispatch",
                       payload=payload, recordCount=100).json()["data"]
    order = ["L1", "L2", "L3", "L4"]
    assert order.index(dispatch["level"]) >= order.index(pv["level"])


def test_来源身份不存在则拒收(client, login):
    r = _create(client, login("admin"), "did:vpp:device:0x" + "e" * 32)
    assert r.status_code == 403 and r.json()["code"] == 1004


def test_来源身份被冻结则拒收(client, login, device_did):
    headers = login("admin")
    client.post(f"/api/v1/did/{device_did}/status", headers=headers,
                json={"action": "freeze", "reason": "测试"})
    r = _create(client, headers, device_did)
    assert r.status_code == 403 and r.json()["code"] == 1004
    assert "frozen" in r.json()["message"]


def test_溯源链记录登记与访问(client, login, device_did):
    headers = login("admin")
    asset = _create(client, headers, device_did, level="L3").json()["data"]
    client.get(f"/api/v1/assets/{asset['id']}", headers=headers)

    chain = client.get(f"/api/v1/assets/{asset['id']}/lineage",
                       headers=headers).json()["data"]["chain"]
    stages = [c["stage"] for c in chain]
    assert stages[0] == "register"
    assert "access" in stages
    assert chain[0]["evidenceId"] == asset["evidenceId"]


def test_L3以上访问才额外上链(client, login, device_did):
    headers = login("admin")
    low = _create(client, headers, device_did, level="L1").json()["data"]
    high = _create(client, headers, device_did, level="L4").json()["data"]

    client.get(f"/api/v1/assets/{low['id']}", headers=headers)
    client.get(f"/api/v1/assets/{high['id']}", headers=headers)

    low_chain = client.get(f"/api/v1/assets/{low['id']}/lineage",
                           headers=headers).json()["data"]["chain"]
    high_chain = client.get(f"/api/v1/assets/{high['id']}/lineage",
                            headers=headers).json()["data"]["chain"]
    low_access = [c for c in low_chain if c["stage"] == "access"][0]
    high_access = [c for c in high_chain if c["stage"] == "access"][0]
    assert low_access["evidenceId"] is None       # L1 只记溯源不上链
    assert high_access["evidenceId"] is not None  # L4 访问必上链


def test_列表筛选与统计(client, login, device_did):
    headers = login("admin")
    _create(client, headers, device_did, dataType="wind", level="L1", name="风电测试资产")

    lst = client.get("/api/v1/assets?dataType=wind&page=1&size=10",
                     headers=headers).json()["data"]
    assert all(i["dataType"] == "wind" for i in lst["items"])

    kw = client.get("/api/v1/assets?keyword=风电测试", headers=headers).json()["data"]
    assert kw["total"] >= 1

    stats = client.get("/api/v1/assets/stats", headers=headers).json()["data"]
    assert stats["total"] >= 1
    assert stats["onChain"] == stats["total"]     # 每条资产都上了链
    assert {d["level"] for d in stats["byLevel"]} <= {"L1", "L2", "L3", "L4"}


def test_能源主体只能看到自己的资产(client, login, device_did):
    """scope='own' 在 SQL 层的隔离效果。"""
    admin = login("admin")
    _create(client, admin, device_did, name="别人的资产")

    subject_dids = client.get("/api/v1/auth/me", headers=login("subject")).json()["data"]["did"]
    lst = client.get("/api/v1/assets?page=1&size=200", headers=login("subject")).json()["data"]
    assert all(i["ownerDid"] == subject_dids for i in lst["items"])


def test_无写权限的角色不能登记(client, login, device_did):
    r = _create(client, login("regulator"), device_did)
    assert r.status_code == 403 and r.json()["code"] == 1003
    assert "asset:write" in r.json()["message"]


def test_分类分级接口(client, login):
    r = client.post("/api/v1/assets/classify", headers=login("admin"), json={
        "records": [
            {"dataType": "pv", "fields": ["power", "voltage", "gps"],
             "freq": "minute", "volume": 1440},
            {"dataType": "load", "fields": ["total"], "freq": "day", "volume": 30},
        ]
    })
    results = r.json()["data"]["results"]
    assert len(results) == 2
    order = ["L1", "L2", "L3", "L4"]
    # 含 gps 的分钟级数据必须比日聚合数据更敏感
    assert order.index(results[0]["level"]) > order.index(results[1]["level"])
    assert results[0]["factors"]["sensitivity"] > 0
