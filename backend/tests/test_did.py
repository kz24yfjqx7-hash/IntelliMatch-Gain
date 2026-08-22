"""DID 身份与密钥测试：签发、验签、冻结、注销、轮换、托管签名。"""
import pytest

from core.gm_crypto import sign as sm2_sign


def _register(client, headers, name="光伏逆变器-测试01", subject_type="device", custody=True):
    r = client.post("/api/v1/did/register", headers=headers, json={
        "subjectType": subject_type, "subjectName": name, "orgName": "测试园区",
        "metadata": {"model": "VPP-2000", "location": "A区"}, "custody": custody,
    })
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_签发did格式符合契约(client, login):
    data = _register(client, login("admin"))
    did = data["did"]
    assert did.startswith("did:vpp:device:0x")
    assert len(did.split(":")[-1]) == 34          # 0x + 32 位 hex
    assert data["publicKey"].startswith("04") and len(data["publicKey"]) == 130
    assert len(data["privateKey"]) == 64
    assert data["evidenceId"].startswith("ev-")
    assert data["chainTxId"].startswith("blk-")


def test_did文档符合w3c结构(client, login):
    data = _register(client, login("admin"), name="储能PCS-测试02")
    doc = data["didDocument"]
    assert doc["@context"] == "https://w3id.org/did/v1"
    assert doc["id"] == data["did"]
    vm = doc["verificationMethod"][0]
    assert vm["type"] == "SM2VerificationKey2023"
    assert vm["publicKeyHex"] == data["publicKey"]
    assert doc["authentication"] == [f"{data['did']}#key-1"]


def test_did由公钥推导可复现(client, login):
    from core.gm_crypto import sm3_hex

    data = _register(client, login("admin"), name="智能电表-测试03")
    expected = "0x" + sm3_hex(bytes.fromhex(data["publicKey"]))[:32]
    assert data["did"].endswith(expected)


def test_真实签名验签通过(client, login):
    headers = login("admin")
    data = _register(client, login("admin"), name="风机控制器-测试04")
    message = "设备上线请求 nonce=abc123"
    signature = sm2_sign(message, data["privateKey"], data["publicKey"])

    r = client.post("/api/v1/did/verify", headers=headers,
                    json={"did": data["did"], "message": message, "signature": signature})
    body = r.json()["data"]
    assert body["valid"] is True
    assert body["subjectType"] == "device"
    assert body["status"] == "active"
    assert body["reason"] is None


def test_原文被改一个字就验签失败(client, login):
    headers = login("admin")
    data = _register(client, login("admin"), name="采集终端-测试05")
    signature = sm2_sign("放电 24kW", data["privateKey"], data["publicKey"])

    r = client.post("/api/v1/did/verify", headers=headers,
                    json={"did": data["did"], "message": "放电 240kW", "signature": signature})
    body = r.json()["data"]
    assert body["valid"] is False
    assert "篡改" in body["reason"] or "伪造" in body["reason"]


def test_伪造签名验签失败(client, login):
    headers = login("admin")
    data = _register(client, login("admin"), name="伪造测试设备-06")
    r = client.post("/api/v1/did/verify", headers=headers,
                    json={"did": data["did"], "message": "任意原文", "signature": "ab" * 64})
    assert r.json()["data"]["valid"] is False


def test_不存在的did验签返回unknown(client, login):
    r = client.post("/api/v1/did/verify", headers=login("admin"), json={
        "did": "did:vpp:device:0x" + "f" * 32, "message": "x", "signature": "ab" * 64})
    body = r.json()["data"]
    assert body["valid"] is False and body["status"] == "unknown"


def test_冻结后不能验签且可解冻(client, login):
    headers = login("admin")
    data = _register(client, headers, name="待冻结设备-07")
    message, priv, pub = "上线", data["privateKey"], data["publicKey"]
    signature = sm2_sign(message, priv, pub)

    r = client.post(f"/api/v1/did/{data['did']}/status", headers=headers,
                    json={"action": "freeze", "reason": "设备离线超 24 小时"})
    assert r.json()["data"]["status"] == "frozen"

    v = client.post("/api/v1/did/verify", headers=headers,
                    json={"did": data["did"], "message": message, "signature": signature})
    assert v.json()["data"]["valid"] is False
    assert "frozen" in v.json()["data"]["reason"]

    client.post(f"/api/v1/did/{data['did']}/status", headers=headers,
                json={"action": "unfreeze", "reason": "设备恢复"})
    v2 = client.post("/api/v1/did/verify", headers=headers,
                     json={"did": data["did"], "message": message, "signature": signature})
    assert v2.json()["data"]["valid"] is True


def test_注销不可逆(client, login):
    headers = login("admin")
    data = _register(client, headers, name="待注销设备-08")
    client.post(f"/api/v1/did/{data['did']}/status", headers=headers,
                json={"action": "revoke", "reason": "设备退役"})
    r = client.post(f"/api/v1/did/{data['did']}/status", headers=headers,
                    json={"action": "unfreeze"})
    assert r.status_code == 409 and r.json()["code"] == 1006


def test_注销身份同时注销其名下密钥(client, login):
    headers = login("admin")
    data = _register(client, headers, name="待注销设备-09")
    client.post(f"/api/v1/did/{data['did']}/status", headers=headers,
                json={"action": "revoke", "reason": "退役"})
    keys = client.get(f"/api/v1/keys?did={data['did']}", headers=headers).json()["data"]["items"]
    assert keys and all(k["status"] == "revoked" for k in keys)


def test_密钥轮换后did不变旧签名失效(client, login):
    headers = login("admin")
    data = _register(client, headers, name="待轮换设备-10")
    old_sig = sm2_sign("原文", data["privateKey"], data["publicKey"])

    r = client.post(f"/api/v1/did/{data['did']}/rotate-key", headers=headers,
                    json={"reason": "密钥到期轮换", "custody": True})
    rotated = r.json()["data"]
    assert rotated["did"] == data["did"]          # DID 标识不变
    assert rotated["version"] == 2
    assert rotated["publicKey"] != data["publicKey"]

    # 旧签名用新公钥验不过
    v = client.post("/api/v1/did/verify", headers=headers,
                    json={"did": data["did"], "message": "原文", "signature": old_sig})
    assert v.json()["data"]["valid"] is False

    # 新私钥签的能过
    new_sig = sm2_sign("原文", rotated["privateKey"], rotated["publicKey"])
    v2 = client.post("/api/v1/did/verify", headers=headers,
                     json={"did": data["did"], "message": "原文", "signature": new_sig})
    assert v2.json()["data"]["valid"] is True
    assert v2.json()["data"]["keyVersion"] == 2


def test_轮换历史可追溯(client, login):
    headers = login("admin")
    data = _register(client, headers, name="轮换历史设备-11")
    client.post(f"/api/v1/did/{data['did']}/rotate-key", headers=headers, json={"reason": "第一次"})
    client.post(f"/api/v1/did/{data['did']}/rotate-key", headers=headers, json={"reason": "第二次"})

    keys = client.get(f"/api/v1/keys?did={data['did']}", headers=headers).json()["data"]["items"]
    history = client.get(f"/api/v1/keys/{keys[0]['id']}/history", headers=headers).json()["data"]
    assert history["currentVersion"] == 3
    assert len(history["rotations"]) == 2
    assert [r["reason"] for r in history["rotations"]] == ["第一次", "第二次"]


def test_托管私钥可代签(client, login):
    from modules.did.service import sign_with_custody, verify_signature

    data = _register(client, login("admin"), name="托管签名设备-12", custody=True)
    signature = sign_with_custody(data["did"], "调度指令：discharge 24kW")
    assert signature is not None
    assert verify_signature(data["did"], "调度指令：discharge 24kW", signature) is True


def test_非托管密钥平台不留存私钥(client, login):
    from modules.did.service import sign_with_custody

    data = _register(client, login("admin"), name="非托管设备-13", custody=False)
    assert sign_with_custody(data["did"], "任意原文") is None
    keys = client.get(f"/api/v1/keys?did={data['did']}",
                      headers=login("admin")).json()["data"]["items"]
    assert keys[0]["custody"] is False


def test_列表筛选与分页(client, login):
    headers = login("admin")
    _register(client, headers, name="筛选测试机构", subject_type="org")
    data = client.get("/api/v1/did?subjectType=org&page=1&size=10", headers=headers).json()["data"]
    assert set(data) == {"items", "total", "page", "size"}
    assert all(i["subjectType"] == "org" for i in data["items"])

    kw = client.get("/api/v1/did?keyword=筛选测试", headers=headers).json()["data"]
    assert kw["total"] >= 1


def test_批量解析含未知did(client, login):
    headers = login("admin")
    data = _register(client, headers, name="批量解析设备-14")
    unknown = "did:vpp:device:0x" + "0" * 32
    r = client.post("/api/v1/did/resolve", headers=headers,
                    json={"dids": [data["did"], unknown]})
    items = r.json()["data"]["items"]
    assert items[0]["exists"] is True
    assert items[1]["exists"] is False and items[1]["status"] == "unknown"


def test_普通角色不能签发身份(client, login):
    r = client.post("/api/v1/did/register", headers=login("subject"), json={
        "subjectType": "device", "subjectName": "越权签发", "custody": True})
    assert r.status_code == 403 and r.json()["code"] == 1003


def test_只能轮换自己的密钥(client, login):
    data = _register(client, login("admin"), name="他人密钥-15")
    r = client.post(f"/api/v1/did/{data['did']}/rotate-key", headers=login("vpp"),
                    json={"reason": "越权轮换"})
    assert r.status_code == 403 and r.json()["code"] == 1003


def test_冻结的身份不能绑定新密钥(client, login):
    headers = login("admin")
    data = _register(client, headers, name="冻结绑定测试-16")
    client.post(f"/api/v1/did/{data['did']}/status", headers=headers,
                json={"action": "freeze", "reason": "测试"})
    r = client.post("/api/v1/keys", headers=headers,
                    json={"did": data["did"], "algorithm": "SM2", "custody": True})
    assert r.status_code == 409 and r.json()["code"] == 1006


@pytest.mark.parametrize("action", ["freeze", "revoke"])
def test_身份状态变更全部上链(client, login, action):
    headers = login("admin")
    data = _register(client, headers, name=f"上链测试-{action}")
    r = client.post(f"/api/v1/did/{data['did']}/status", headers=headers,
                    json={"action": action, "reason": "测试"})
    assert r.json()["data"]["evidenceId"].startswith("ev-")
