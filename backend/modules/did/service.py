"""DID 身份服务。契约 2.2。

DID 标识生成规则：did:vpp:<subjectType>:0x<SM3(公钥) 前 32 位 hex>
公钥变了 DID 就变了，所以密钥轮换保留原 DID、只换 verificationMethod，
这与 W3C DID 的设计一致。
"""
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core import redis_client
from core.config import settings
from core.database import SessionLocal
from core.exceptions import ConflictError, NotFoundError, ParamError
from core.gm_crypto import (
    derive_key,
    generate_keypair_by_algorithm,
    sign as sm2_sign,
    sm3_hex,
    sm3_tag,
    sm4_cbc_decrypt,
    sm4_cbc_encrypt,
    verify as sm2_verify,
)
from core.middleware import current_principal
from core.response import iso, now_cst
from modules.did.model import DidIdentity, DidKey, DidKeyRotationLog
from modules.evidence.service import write_evidence

logger = logging.getLogger(__name__)

_STATUS_CACHE_PREFIX = "did:status:"
_STATUS_CACHE_TTL = 30  # 秒。冻结后最多 30 秒生效；演示时会主动删缓存，即时生效


def _custody_key() -> bytes:
    return derive_key(settings.KEY_CUSTODY_SECRET)


def make_did(subject_type: str, public_key: str) -> str:
    """由公钥推导 DID。同一把公钥永远得到同一个 DID。"""
    return f"did:vpp:{subject_type}:0x{sm3_hex(bytes.fromhex(public_key))[:32]}"


def build_did_document(did: str, controller: str | None, public_key: str,
                       algorithm: str, created) -> dict:
    return {
        "@context": "https://w3id.org/did/v1",
        "id": did,
        "controller": controller or did,
        "verificationMethod": [{
            "id": f"{did}#key-1",
            "type": f"{algorithm}VerificationKey2023",
            "controller": controller or did,
            "publicKeyHex": public_key,
        }],
        "authentication": [f"{did}#key-1"],
        "created": iso(created),
    }


# ---------------------------------------------------------------- 签发

def register_did(db: Session, *, subject_type: str, subject_name: str,
                 org_name: str | None = None, controller_did: str | None = None,
                 metadata: dict | None = None, algorithm: str = "SM2",
                 custody: bool = True) -> dict:
    public_key, private_key = generate_keypair_by_algorithm(algorithm)
    did = make_did(subject_type, public_key)

    if db.execute(select(DidIdentity.id).where(DidIdentity.did == did)).first():
        raise ConflictError(f"身份 {did} 已存在")

    created = now_cst()
    document = build_did_document(did, controller_did, public_key, algorithm, created)

    db.add(DidIdentity(
        did=did, subject_type=subject_type, subject_name=subject_name, org_name=org_name,
        controller_did=controller_did, did_document=document, status="active",
        metadata_=metadata or {}, created_at=created.replace(tzinfo=None),
    ))
    db.add(DidKey(
        did=did, algorithm=algorithm, public_key=public_key, key_hash=sm3_tag(public_key),
        private_key_enc=sm4_cbc_encrypt(private_key, _custody_key()) if custody else None,
        custody=1 if custody else 0, status="active", version=1, purpose="sign",
        bound_at=created.replace(tzinfo=None),
    ))
    db.flush()

    evidence = write_evidence(db, category="identity", ref_id=did, payload={
        "action": "did:register", "did": did, "subjectType": subject_type,
        "subjectName": subject_name, "publicKey": public_key, "createdAt": iso(created),
    })
    db.commit()
    _invalidate_status_cache(did)

    return {
        "did": did,
        "didDocument": document,
        "publicKey": public_key,
        # 契约要求私钥仅本次返回。custody=true 时平台另存一份 SM4 密文用于演示签名，
        # custody=false 时平台完全不留存，丢了只能轮换密钥。
        "privateKey": private_key,
        "custody": custody,
        "chainTxId": evidence["txId"],
        "evidenceId": evidence["evidenceId"],
        "createdAt": iso(created),
    }


# ---------------------------------------------------------------- 查询

def _to_item(identity: DidIdentity, key: DidKey | None = None) -> dict:
    return {
        "did": identity.did,
        "subjectType": identity.subject_type,
        "subjectName": identity.subject_name,
        "orgName": identity.org_name,
        "controllerDid": identity.controller_did,
        "status": identity.status,
        "metadata": identity.metadata_,
        "publicKey": key.public_key if key else None,
        "algorithm": key.algorithm if key else None,
        "keyVersion": key.version if key else None,
        "createdAt": iso(identity.created_at),
        "updatedAt": iso(identity.updated_at),
    }


def list_dids(db: Session, page: int, size: int, subject_type: str | None = None,
              status: str | None = None, keyword: str | None = None) -> tuple[list[dict], int]:
    stmt = select(DidIdentity)
    count_stmt = select(func.count()).select_from(DidIdentity)

    if subject_type:
        stmt = stmt.where(DidIdentity.subject_type == subject_type)
        count_stmt = count_stmt.where(DidIdentity.subject_type == subject_type)
    if status:
        stmt = stmt.where(DidIdentity.status == status)
        count_stmt = count_stmt.where(DidIdentity.status == status)
    if keyword:
        like = f"%{keyword}%"
        cond = DidIdentity.subject_name.like(like) | DidIdentity.did.like(like)
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = db.execute(count_stmt).scalar_one()
    identities = db.execute(
        stmt.order_by(DidIdentity.id.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()

    keys = _active_keys_of(db, [i.did for i in identities])
    return [_to_item(i, keys.get(i.did)) for i in identities], total


def _active_keys_of(db: Session, dids: list[str]) -> dict[str, DidKey]:
    if not dids:
        return {}
    rows = db.execute(
        select(DidKey)
        .where(DidKey.did.in_(dids), DidKey.status == "active")
        .order_by(DidKey.version)
    ).scalars().all()
    return {k.did: k for k in rows}   # 同一 DID 有多把时取版本最大的


def get_identity(db: Session, did: str) -> DidIdentity:
    identity = db.execute(
        select(DidIdentity).where(DidIdentity.did == did)
    ).scalar_one_or_none()
    if identity is None:
        raise NotFoundError(f"身份 {did} 不存在")
    return identity


def get_did_detail(db: Session, did: str) -> dict:
    identity = get_identity(db, did)
    key = _active_keys_of(db, [did]).get(did)
    detail = _to_item(identity, key)
    detail["didDocument"] = identity.did_document
    return detail


def resolve(db: Session, dids: list[str]) -> list[dict]:
    """批量解析。不存在的 DID 也要返回一条，标 status=unknown，方便前端逐个对照。"""
    found = {
        i.did: i for i in db.execute(
            select(DidIdentity).where(DidIdentity.did.in_(dids))
        ).scalars().all()
    }
    keys = _active_keys_of(db, list(found))
    result = []
    for did in dids:
        identity = found.get(did)
        if identity is None:
            result.append({"did": did, "status": "unknown", "exists": False})
        else:
            item = _to_item(identity, keys.get(did))
            item["exists"] = True
            result.append(item)
    return result


# ---------------------------------------------------------------- 状态与轮换

_ACTION_TO_STATUS = {"freeze": "frozen", "unfreeze": "active", "revoke": "revoked"}


def change_status(db: Session, did: str, action: str, reason: str | None) -> dict:
    identity = get_identity(db, did)
    target = _ACTION_TO_STATUS[action]

    if identity.status == "revoked":
        raise ConflictError("身份已注销，注销不可逆")
    if identity.status == target:
        raise ConflictError(f"身份已处于 {target} 状态")
    if action == "unfreeze" and identity.status != "frozen":
        raise ParamError("只有已冻结的身份才能解冻")

    identity.status = target
    if target == "revoked":
        # 注销身份的同时把它名下所有密钥一并注销，否则密钥还能验签
        for key in db.execute(select(DidKey).where(DidKey.did == did)).scalars().all():
            key.status = "revoked"

    evidence = write_evidence(db, category="identity", ref_id=did, payload={
        "action": f"did:{action}", "did": did, "status": target, "reason": reason,
        "at": iso(now_cst()),
    })
    db.commit()
    _invalidate_status_cache(did)

    return {"did": did, "status": target, "reason": reason,
            "evidenceId": evidence["evidenceId"]}


def rotate_key(db: Session, did: str, reason: str | None, custody: bool = True) -> dict:
    """密钥轮换：旧密钥置 revoked，新密钥版本号 +1，DID 标识保持不变。"""
    identity = get_identity(db, did)
    if identity.status != "active":
        raise ConflictError(f"身份状态为 {identity.status}，不允许轮换密钥")

    old = db.execute(
        select(DidKey).where(DidKey.did == did, DidKey.status == "active")
        .order_by(DidKey.version.desc()).limit(1)
    ).scalar_one_or_none()

    algorithm = old.algorithm if old else "SM2"
    public_key, private_key = generate_keypair_by_algorithm(algorithm)
    new_version = (old.version if old else 0) + 1

    if old:
        old.status = "revoked"

    new_key = DidKey(
        did=did, algorithm=algorithm, public_key=public_key, key_hash=sm3_tag(public_key),
        private_key_enc=sm4_cbc_encrypt(private_key, _custody_key()) if custody else None,
        custody=1 if custody else 0, status="active", version=new_version, purpose="sign",
    )
    db.add(new_key)
    db.flush()

    # DID 文档里的验证方法要同步换成新公钥，否则验签会一直用旧公钥
    document = dict(identity.did_document or {})
    document["verificationMethod"] = [{
        "id": f"{did}#key-{new_version}",
        "type": f"{algorithm}VerificationKey2023",
        "controller": identity.controller_did or did,
        "publicKeyHex": public_key,
    }]
    document["authentication"] = [f"{did}#key-{new_version}"]
    document["updated"] = iso(now_cst())
    identity.did_document = document

    principal = current_principal.get()
    evidence = write_evidence(db, category="identity", ref_id=did, payload={
        "action": "key:rotate", "did": did, "oldVersion": old.version if old else 0,
        "newVersion": new_version, "newPublicKey": public_key, "reason": reason,
    })
    db.add(DidKeyRotationLog(
        did=did, key_id=new_key.id,
        old_public_key=old.public_key if old else None, new_public_key=public_key,
        old_version=old.version if old else 0, new_version=new_version,
        reason=reason, operator_did=principal.did if principal else None,
        evidence_id=evidence["evidenceId"],
    ))
    db.commit()

    return {
        "did": did, "publicKey": public_key, "privateKey": private_key,
        "version": new_version, "algorithm": algorithm, "custody": custody,
        "evidenceId": evidence["evidenceId"], "didDocument": document,
    }


# ---------------------------------------------------------------- 验签

def verify_signature(did: str | None, message: str, signature: str) -> bool:
    """用 DID 名下的活跃公钥验签。身份或密钥被冻结/注销时一律返回 False。"""
    if not did or not signature:
        return False
    with SessionLocal() as db:
        identity = db.execute(
            select(DidIdentity).where(DidIdentity.did == did)
        ).scalar_one_or_none()
        if identity is None or identity.status != "active":
            return False
        key = db.execute(
            select(DidKey).where(DidKey.did == did, DidKey.status == "active")
            .order_by(DidKey.version.desc()).limit(1)
        ).scalar_one_or_none()
    if key is None or key.algorithm != "SM2":
        return False
    return sm2_verify(message, signature, key.public_key)


def verify_detail(db: Session, did: str, message: str, signature: str) -> dict:
    """契约 2.2 POST /did/verify：除 valid 外还要返回主体类型、状态与失败原因。"""
    identity = db.execute(
        select(DidIdentity).where(DidIdentity.did == did)
    ).scalar_one_or_none()
    if identity is None:
        return {"valid": False, "subjectType": None, "status": "unknown",
                "reason": "身份不存在"}
    if identity.status != "active":
        return {"valid": False, "subjectType": identity.subject_type,
                "status": identity.status, "reason": f"身份状态为 {identity.status}，不可用于验签"}

    key = db.execute(
        select(DidKey).where(DidKey.did == did, DidKey.status == "active")
        .order_by(DidKey.version.desc()).limit(1)
    ).scalar_one_or_none()
    if key is None:
        return {"valid": False, "subjectType": identity.subject_type,
                "status": identity.status, "reason": "该身份没有可用的活跃密钥"}

    valid = sm2_verify(message, signature, key.public_key)
    return {
        "valid": valid,
        "subjectType": identity.subject_type,
        "status": identity.status,
        "keyVersion": key.version,
        "reason": None if valid else "签名与公钥不匹配，原文可能被篡改或签名伪造",
    }


def sign_with_custody(did: str, message: str) -> str | None:
    """用托管私钥代签。仅在密钥标记为托管时可用，否则返回 None。

    这是给答辩演示留的路：前端没有保存私钥时，调度下发这类需要签名的操作
    仍然能走通完整的「签名 → 验签 → 上链」流程，而不是把验签环节跳过去。
    """
    with SessionLocal() as db:
        key = db.execute(
            select(DidKey).where(DidKey.did == did, DidKey.status == "active", DidKey.custody == 1)
            .order_by(DidKey.version.desc()).limit(1)
        ).scalar_one_or_none()
        if key is None or not key.private_key_enc:
            return None
        public_key = key.public_key
        enc = key.private_key_enc
    try:
        private_key = sm4_cbc_decrypt(enc, _custody_key()).decode()
    except Exception as exc:  # noqa: BLE001
        logger.error("托管私钥解密失败 did=%s：%s（KEY_CUSTODY_SECRET 是否被改过？）", did, exc)
        return None
    return sm2_sign(message, private_key, public_key)


# ---------------------------------------------------------------- 状态缓存（供中间件高频调用）

def get_did_status(did: str) -> str | None:
    """中间件每个请求都会调，所以走 Redis 缓存。查不到返回 None（不阻断请求）。"""
    if not did:
        return None
    cached = redis_client.safe_get(_STATUS_CACHE_PREFIX + did)
    if cached:
        return None if cached == "-" else cached

    try:
        with SessionLocal() as db:
            status = db.execute(
                select(DidIdentity.status).where(DidIdentity.did == did)
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.warning("查询 DID 状态失败 %s：%s", did, exc)
        return None

    redis_client.safe_set(_STATUS_CACHE_PREFIX + did, status or "-", ex=_STATUS_CACHE_TTL)
    return status


def _invalidate_status_cache(did: str) -> None:
    """冻结 / 解冻 / 注销后立刻抹掉缓存，让新状态即时生效，不用等 30 秒 TTL。"""
    redis_client.safe_delete(_STATUS_CACHE_PREFIX + did)
