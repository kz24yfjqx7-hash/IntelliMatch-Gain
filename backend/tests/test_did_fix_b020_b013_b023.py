"""缺陷修复回归测试。

- B-020：同一 DID 上 SM2 / ECC / RSA 多把活跃密钥并存时，任意一把的签名都要能验过；
         ECC / RSA 必须真的能签能验（不允许「能生成、验不了」）。
- B-013：`POST /did/{did}/rotate-key` 不带请求体也要能轮换（契约 2.2 未定义该 body）。
- B-023：createdAt 与 updatedAt 必须指向同一时刻、同为东八区。
"""
from datetime import datetime

from core.gm_crypto import (
    ecdsa_sign,
    generate_keypair_by_algorithm,
    rsa_sign,
    sign as sm2_sign,
    sign_by_algorithm,
    verify_by_algorithm,
)
from core.response import CST, now_cst

MESSAGE = "设备上线请求 nonce=b020"


def _register(client, headers, name="多算法密钥-测试", subject_type="device"):
    r = client.post("/api/v1/did/register", headers=headers, json={
        "subjectType": subject_type, "subjectName": name, "custody": True,
    })
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _bind_key(client, headers, did, algorithm):
    r = client.post("/api/v1/keys", headers=headers,
                    json={"did": did, "algorithm": algorithm})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _verify(client, headers, did, message, signature):
    r = client.post("/api/v1/did/verify", headers=headers,
                    json={"did": did, "message": message, "signature": signature})
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---------------------------------------------------------------- B-020

def test_b020_ecc与rsa能签能验且不会互相混淆():
    """算法层自检：三种算法各自签名可验、改一个字就验不过、跨算法一律不通过。"""
    for algorithm in ("SM2", "ECC", "RSA"):
        pub, priv = generate_keypair_by_algorithm(algorithm)
        sig = sign_by_algorithm(MESSAGE, priv, algorithm, pub)
        assert verify_by_algorithm(MESSAGE, sig, pub, algorithm) is True, algorithm
        assert verify_by_algorithm(MESSAGE + "!", sig, pub, algorithm) is False, algorithm
        for other in ("SM2", "ECC", "RSA"):
            if other != algorithm:
                assert verify_by_algorithm(MESSAGE, sig, pub, other) is False
        # 签名与公钥长度必须落在既有列宽 / 契约字段长度之内
        assert len(sig) <= 256 and len(pub) <= 512


def test_b020_三种算法密钥并存时每把私钥的签名都验得过(client, login):
    headers = login("admin")
    data = _register(client, headers, name="多算法密钥-并存")
    did = data["did"]

    # v1 = 注册时的 SM2，再绑 SM2(v2) / ECC(v3) / RSA(v4)
    keys = [("SM2", 1, data["publicKey"], data["privateKey"])]
    for algorithm in ("SM2", "ECC", "RSA"):
        item = _bind_key(client, headers, did, algorithm)
        assert item["algorithm"] == algorithm
        keys.append((algorithm, item["version"], item["publicKey"], item["privateKey"]))
    assert [k[1] for k in keys] == [1, 2, 3, 4]

    for algorithm, version, pub, priv in keys:
        sig = sign_by_algorithm(MESSAGE, priv, algorithm, pub)
        body = _verify(client, headers, did, MESSAGE, sig)
        assert body["valid"] is True, f"{algorithm} v{version} 验签失败：{body}"
        # 响应里回传的是实际命中的那把密钥
        assert body["keyVersion"] == version
        assert body["algorithm"] == algorithm
        assert body["reason"] is None
        # 契约 2.2 的字段名必须保持
        assert body["subjectType"] == "device"
        assert body["status"] == "active"


def test_b020_后绑ecc密钥不会让原sm2签名失效(client, login):
    """B-020 的原始复现路径：绑一把 ECC 就让该 DID 的 SM2 签名全废。"""
    headers = login("admin")
    data = _register(client, headers, name="多算法密钥-ECC不破坏SM2")
    did = data["did"]
    sig = sm2_sign(MESSAGE, data["privateKey"], data["publicKey"])
    assert _verify(client, headers, did, MESSAGE, sig)["valid"] is True

    _bind_key(client, headers, did, "ECC")
    _bind_key(client, headers, did, "RSA")

    body = _verify(client, headers, did, MESSAGE, sig)
    assert body["valid"] is True, body
    assert body["keyVersion"] == 1 and body["algorithm"] == "SM2"


def test_b020_ecc与rsa私钥签名走完整接口链路(client, login):
    headers = login("admin")
    did = _register(client, headers, name="多算法密钥-接口链路")["did"]
    ecc = _bind_key(client, headers, did, "ECC")
    rsa = _bind_key(client, headers, did, "RSA")

    for item, signer in ((ecc, ecdsa_sign), (rsa, rsa_sign)):
        body = _verify(client, headers, did, MESSAGE, signer(MESSAGE, item["privateKey"]))
        assert body["valid"] is True, body
        assert body["keyId"] == item["id"]


def test_b020_伪造签名与被改的原文依旧验不过(client, login):
    headers = login("admin")
    data = _register(client, headers, name="多算法密钥-负例")
    did = data["did"]
    _bind_key(client, headers, did, "ECC")
    _bind_key(client, headers, did, "RSA")

    sig = sm2_sign(MESSAGE, data["privateKey"], data["publicKey"])
    tampered = _verify(client, headers, did, MESSAGE + "（被改过）", sig)
    assert tampered["valid"] is False
    assert "不匹配" in tampered["reason"]

    # 别人的密钥签的名也不行
    other_pub, other_priv = generate_keypair_by_algorithm("SM2")
    forged = sm2_sign(MESSAGE, other_priv, other_pub)
    assert _verify(client, headers, did, MESSAGE, forged)["valid"] is False


def test_b020_注销掉的密钥立刻失去验签能力(client, login):
    headers = login("admin")
    did = _register(client, headers, name="多算法密钥-注销")["did"]
    rsa = _bind_key(client, headers, did, "RSA")
    sig = rsa_sign(MESSAGE, rsa["privateKey"])
    assert _verify(client, headers, did, MESSAGE, sig)["valid"] is True

    r = client.post(f"/api/v1/keys/{rsa['id']}/revoke", headers=headers,
                    json={"reason": "测试注销"})
    assert r.status_code == 200, r.text
    assert _verify(client, headers, did, MESSAGE, sig)["valid"] is False


def test_b020_托管代签在只有ecc密钥时也走得通(client, login):
    """sign_with_custody 必须按密钥算法选签名算法，不能一律当 SM2 签。"""
    from modules.did.service import sign_with_custody, verify_signature

    headers = login("admin")
    did = _register(client, headers, name="多算法密钥-托管")["did"]
    _bind_key(client, headers, did, "ECC")

    signature = sign_with_custody(did, MESSAGE)
    assert signature is not None
    assert verify_signature(did, MESSAGE, signature) is True


def test_b020_存量占位密钥只会让签名失败不会抛异常(client, login, db):
    """本轮之前绑的 ECC / RSA 是随机串占位，拿它去签会抛异常，必须降级成 None 而不是 500。"""
    import os

    from core.gm_crypto import sm3_tag, sm4_cbc_encrypt
    from modules.did.model import DidKey
    from modules.did.service import _custody_key, sign_with_custody

    headers = login("admin")
    did = _register(client, headers, name="多算法密钥-存量占位")["did"]

    # 把注册时的真 SM2 密钥冻结，只剩一把旧版占位的 RSA 托管密钥
    for key in db.query(DidKey).filter(DidKey.did == did).all():
        key.status = "frozen"
    db.add(DidKey(
        did=did, algorithm="RSA", public_key="04" + os.urandom(64).hex(),
        key_hash=sm3_tag("legacy"),
        private_key_enc=sm4_cbc_encrypt(os.urandom(32).hex(), _custody_key()),
        custody=1, status="active", version=9, purpose="sign",
    ))
    db.commit()

    assert sign_with_custody(did, MESSAGE) is None


# ---------------------------------------------------------------- B-013

def test_b013_轮换密钥不带请求体也能成功(client, login):
    headers = login("admin")
    did = _register(client, headers, name="轮换-无body")["did"]

    # 契约 2.2 没有为该接口定义 body，客户端可以什么都不发
    r = client.post(f"/api/v1/did/{did}/rotate-key", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["version"] == 2
    assert data["algorithm"] == "SM2"

    # 轮换出来的新密钥立刻可用于验签，旧密钥已作废
    sig = sm2_sign(MESSAGE, data["privateKey"], data["publicKey"])
    assert _verify(client, headers, did, MESSAGE, sig)["valid"] is True

    # 带 body 的老用法保持兼容
    r2 = client.post(f"/api/v1/did/{did}/rotate-key", headers=headers,
                     json={"reason": "定期轮换"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["version"] == 3


# ---------------------------------------------------------------- B-023

def _parse(ts: str) -> datetime:
    assert ts.endswith("+08:00"), ts
    return datetime.fromisoformat(ts)


def test_b023_新建记录的createdat与updatedat指向同一时刻(client, login):
    headers = login("admin")
    did = _register(client, headers, name="时间口径-新建")["did"]

    detail = client.get(f"/api/v1/did/{did}", headers=headers).json()["data"]
    created, updated = _parse(detail["createdAt"]), _parse(detail["updatedAt"])
    assert abs((updated - created).total_seconds()) <= 1, (created, updated)
    # 不能再出现「差 8 小时」这种时区口径不一致
    assert abs((now_cst() - created).total_seconds()) < 60


def test_b023_更新记录后updatedat仍是东八区且不早于createdat(client, login):
    headers = login("admin")
    did = _register(client, headers, name="时间口径-更新")["did"]

    r = client.post(f"/api/v1/did/{did}/status", headers=headers,
                    json={"action": "freeze", "reason": "测试时间口径"})
    assert r.status_code == 200, r.text

    detail = client.get(f"/api/v1/did/{did}", headers=headers).json()["data"]
    created, updated = _parse(detail["createdAt"]), _parse(detail["updatedAt"])
    assert updated >= created
    assert abs((now_cst() - updated).total_seconds()) < 60


def test_b023_所有模型的时间列都由应用侧写入():
    """防回归：不允许再出现只有 server_default 的时间列（那取的是数据库服务器时区）。"""
    from core.database import Base
    import modules.algo.model  # noqa: F401
    import modules.asset.model  # noqa: F401
    import modules.audit.model  # noqa: F401
    import modules.auth.model  # noqa: F401
    import modules.did.model  # noqa: F401
    import modules.evidence.model  # noqa: F401
    import modules.node.model  # noqa: F401
    import modules.permission.model  # noqa: F401

    from sqlalchemy import DateTime

    bad = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime) and column.server_default is not None:
                if column.default is None:
                    bad.append(f"{table.name}.{column.name}")
    assert not bad, f"这些时间列还在用数据库时钟：{bad}"

    # updated_at 还必须有 onupdate，否则更新后时间不刷新
    missing = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.tables.values() for c in t.columns
        if c.name == "updated_at" and c.onupdate is None
    ]
    assert not missing, f"这些 updated_at 缺 onupdate：{missing}"


def test_b023_now_naive返回东八区的无时区时间():
    from core.response import now_naive

    naive = now_naive()
    assert naive.tzinfo is None
    assert naive.microsecond == 0
    assert abs((naive.replace(tzinfo=CST) - now_cst()).total_seconds()) < 5
