"""存证接口测试：检索、校验、链状态、篡改演示、凭证导出。

对应答辩演示脚本第 8 步：篡改一条存证 → 完整性校验立刻标红 → 链状态显示断裂点。
"""
import pytest


def _restore_snapshot(SessionFactory, evidence_id, payload):
    """直接把快照写回数据库，供用例自清理（不依赖 Redis 备份）。"""
    from sqlalchemy import select

    from modules.evidence.model import ChainEvidence

    with SessionFactory() as db:
        record = db.execute(
            select(ChainEvidence).where(ChainEvidence.evidence_id == evidence_id)
        ).scalar_one()
        record.payload_snapshot = payload
        db.commit()


@pytest.fixture
def evidence_id(client, login):
    """造一条存证：注册设备身份会自动上链。"""
    r = client.post("/api/v1/did/register", headers=login("admin"), json={
        "subjectType": "device", "subjectName": "存证测试设备", "custody": True,
    })
    return r.json()["data"]["evidenceId"]


def test_链状态在未篡改时是完整的(client, login):
    data = client.get("/api/v1/evidence/chain/status", headers=login("admin")).json()["data"]
    assert data["intact"] is True
    assert data["brokenAt"] is None
    assert data["chainType"] == "LocalHashChain"
    assert data["height"] == data["totalRecords"]


def test_存证详情自带校验结果(client, login, evidence_id):
    data = client.get(f"/api/v1/evidence/{evidence_id}", headers=login("admin")).json()["data"]
    assert data["evidenceId"] == evidence_id
    assert data["hash"].startswith("sm3:")
    assert data["blockHash"].startswith("sm3:")
    assert data["verification"]["intact"] is True


def test_篡改演示三连(client, login, evidence_id, SessionFactory):
    """答辩现场就是这三步。"""
    admin = login("admin")

    # 篡改前：完整
    before = client.post("/api/v1/evidence/verify", headers=admin,
                         json={"evidenceId": evidence_id}).json()["data"]
    assert before["intact"] is True

    # 篡改
    tampered = client.post("/api/v1/evidence/demo/tamper", headers=admin, json={
        "evidenceId": evidence_id, "newValue": {"pvOutput": 999.9}}).json()["data"]
    assert tampered["tampered"] is True
    assert tampered["tamperedPayload"]["pvOutput"] == 999.9

    # 单条校验立刻标红
    after = client.post("/api/v1/evidence/verify", headers=admin,
                        json={"evidenceId": evidence_id}).json()["data"]
    assert after["intact"] is False
    assert after["localHash"] != after["chainHash"]
    assert "篡改" in after["message"]

    # 链状态精确定位断裂点。B-017：brokenAt 是区块高度，evidenceId 走 brokenAtEvidenceId
    status = client.get("/api/v1/evidence/chain/status", headers=admin).json()["data"]
    assert status["intact"] is False
    assert status["brokenAt"] == tampered["blockHeight"]
    assert status["brokenAtEvidenceId"] == evidence_id

    # 用例之间不能互相污染：把快照写回去，让后面的用例仍然面对一条完整的链
    _restore_snapshot(SessionFactory, evidence_id, tampered["originalPayload"])
    assert client.get("/api/v1/evidence/chain/status",
                      headers=admin).json()["data"]["intact"] is True


def test_篡改后可还原以便反复彩排(client, login, evidence_id, monkeypatch):
    from core import redis_client

    store = {}
    monkeypatch.setattr(redis_client, "safe_set",
                        lambda k, v, ex=None: store.__setitem__(k, v) or True)
    monkeypatch.setattr(redis_client, "safe_get", lambda k: store.get(k))

    admin = login("admin")
    client.post("/api/v1/evidence/demo/tamper", headers=admin,
                json={"evidenceId": evidence_id, "newValue": {"pvOutput": 999.9}})
    assert client.post("/api/v1/evidence/verify", headers=admin,
                       json={"evidenceId": evidence_id}).json()["data"]["intact"] is False

    restored = client.post("/api/v1/evidence/demo/restore", headers=admin,
                           json={"evidenceId": evidence_id}).json()["data"]
    assert restored["restored"] is True
    assert restored["verification"]["intact"] is True
    assert client.get("/api/v1/evidence/chain/status",
                      headers=admin).json()["data"]["intact"] is True


def test_只有管理员能调篡改接口(client, login, evidence_id):
    r = client.post("/api/v1/evidence/demo/tamper", headers=login("grid"),
                    json={"evidenceId": evidence_id, "newValue": {"x": 1}})
    assert r.status_code == 403 and r.json()["code"] == 1003


def test_传入外部payload校验(client, login, evidence_id):
    """前端可以拿本地留存的数据来对账，不依赖库内快照。"""
    r = client.post("/api/v1/evidence/verify", headers=login("admin"), json={
        "evidenceId": evidence_id, "payload": {"随便什么": "对不上的数据"}})
    assert r.json()["data"]["intact"] is False


def test_按类别与时间检索(client, login, evidence_id):
    admin = login("admin")
    data = client.get("/api/v1/evidence?category=identity&page=1&size=10",
                      headers=admin).json()["data"]
    assert set(data) == {"items", "total", "page", "size"}
    assert all(i["category"] == "identity" for i in data["items"])

    bad = client.get("/api/v1/evidence?from=不是时间", headers=admin)
    assert bad.status_code == 400 and bad.json()["code"] == 1001


def test_按traceid查完整链路(client, login):
    trace = "tr-20260818-abcd1234"
    admin = login("admin")
    client.post("/api/v1/did/register", headers={**admin, "X-Trace-Id": trace}, json={
        "subjectType": "device", "subjectName": "链路追踪设备", "custody": True})

    data = client.get(f"/api/v1/evidence/trace/{trace}", headers=admin).json()["data"]
    assert data["traceId"] == trace
    assert data["total"] >= 1
    assert all(s["evidenceId"].startswith("ev-") for s in data["steps"])


def test_导出凭证含链证明与平台签名(client, login, evidence_id):
    cert = client.get(f"/api/v1/evidence/{evidence_id}/certificate",
                      headers=login("admin")).json()["data"]
    assert cert["certificateType"] == "EnergyTDS-Evidence-Certificate"
    assert cert["chainProof"]["hashAlgorithm"] == "SM3"
    assert cert["chainProof"]["blockFormula"] == "SM3(prevHash + payloadHash + timestamp)"
    assert cert["verification"]["intact"] is True
    assert cert["signature"]["algorithm"] == "SM2"


def test_凭证签名可用平台公钥验证(client, login, evidence_id, SessionFactory):
    """凭证是可离线核验的：拿平台公钥就能验，不需要连回平台。"""
    from sqlalchemy import select

    from core.gm_crypto import canonical_json, verify
    from modules.did.model import DidIdentity, DidKey

    # 先造一个平台机构身份，凭证签名要用它
    client.post("/api/v1/did/register", headers=login("admin"), json={
        "subjectType": "org", "subjectName": "平台运营方", "custody": True})

    cert = client.get(f"/api/v1/evidence/{evidence_id}/certificate",
                      headers=login("admin")).json()["data"]
    signature = cert.pop("signature")
    assert signature["value"], "凭证没有签名"

    with SessionFactory() as db:
        key = db.execute(
            select(DidKey).where(DidKey.did == signature["signerDid"],
                                 DidKey.status == "active")
        ).scalar_one()
    assert verify(canonical_json(cert), signature["value"], key.public_key) is True


def test_不存在的存证返回1005(client, login):
    r = client.get("/api/v1/evidence/ev-999999", headers=login("admin"))
    assert r.status_code == 404 and r.json()["code"] == 1005


def test_无存证读权限的角色被拦(client, login, evidence_id):
    """边缘节点角色没有 evidence:read。"""
    r = client.get("/api/v1/evidence/chain/status", headers=login("edge"))
    assert r.status_code == 403 and r.json()["code"] == 1003
