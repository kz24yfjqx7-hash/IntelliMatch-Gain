"""密钥管理服务。契约 2.3。

密钥与 DID 是一对多：一个身份可以有多把密钥（签名用、加密用、历史版本）。
私钥永远不明文落库——托管密钥存 SM4-CBC 密文，非托管密钥平台完全不留存。
"""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.exceptions import ConflictError, NotFoundError
from core.gm_crypto import generate_keypair_by_algorithm, sm3_tag, sm4_cbc_encrypt
from core.middleware import current_principal
from core.response import iso, now_cst
from modules.did.model import DidIdentity, DidKey, DidKeyRotationLog
from modules.did.service import _custody_key, get_identity
from modules.evidence.service import write_evidence


def _to_item(key: DidKey) -> dict:
    return {
        "id": key.id,
        "did": key.did,
        "algorithm": key.algorithm,
        "publicKey": key.public_key,
        "keyHash": key.key_hash,
        "custody": bool(key.custody),
        "status": key.status,
        "version": key.version,
        "purpose": key.purpose,
        "boundAt": iso(key.bound_at),
        "expireAt": iso(key.expire_at),
    }


def list_keys(db: Session, page: int, size: int, did: str | None = None,
              status: str | None = None) -> tuple[list[dict], int]:
    stmt = select(DidKey)
    count_stmt = select(func.count()).select_from(DidKey)
    if did:
        stmt = stmt.where(DidKey.did == did)
        count_stmt = count_stmt.where(DidKey.did == did)
    if status:
        stmt = stmt.where(DidKey.status == status)
        count_stmt = count_stmt.where(DidKey.status == status)

    total = db.execute(count_stmt).scalar_one()
    keys = db.execute(
        stmt.order_by(DidKey.id.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()
    return [_to_item(k) for k in keys], total


def create_key(db: Session, payload) -> dict:
    identity = get_identity(db, payload.did)
    if identity.status != "active":
        raise ConflictError(f"身份状态为 {identity.status}，不允许绑定新密钥")

    public_key, private_key = generate_keypair_by_algorithm(payload.algorithm)
    latest_version = db.execute(
        select(func.max(DidKey.version)).where(DidKey.did == payload.did)
    ).scalar() or 0

    key = DidKey(
        did=payload.did, algorithm=payload.algorithm, public_key=public_key,
        key_hash=sm3_tag(public_key),
        private_key_enc=sm4_cbc_encrypt(private_key, _custody_key()) if payload.custody else None,
        custody=1 if payload.custody else 0, status="active", version=latest_version + 1,
        purpose=payload.purpose,
        expire_at=(now_cst() + timedelta(days=payload.expireDays)).replace(tzinfo=None),
    )
    db.add(key)
    db.flush()

    evidence = write_evidence(db, category="identity", ref_id=payload.did, payload={
        "action": "key:create", "did": payload.did, "algorithm": payload.algorithm,
        "publicKey": public_key, "version": key.version, "purpose": payload.purpose,
    })
    db.commit()

    item = _to_item(key)
    # 私钥仅本次返回。custody=false 时这是唯一一次能拿到它的机会
    item["privateKey"] = private_key
    item["evidenceId"] = evidence["evidenceId"]
    return item


def _get_key(db: Session, key_id: int) -> DidKey:
    key = db.get(DidKey, key_id)
    if key is None:
        raise NotFoundError(f"密钥 {key_id} 不存在")
    return key


def change_key_status(db: Session, key_id: int, target: str, reason: str | None) -> dict:
    key = _get_key(db, key_id)
    if key.status == "revoked":
        raise ConflictError("密钥已注销，注销不可逆")
    if key.status == target:
        raise ConflictError(f"密钥已处于 {target} 状态")

    key.status = target
    evidence = write_evidence(db, category="identity", ref_id=str(key_id), payload={
        "action": f"key:{target}", "keyId": key_id, "did": key.did,
        "reason": reason, "at": iso(now_cst()),
    })
    db.commit()
    return {"id": key_id, "did": key.did, "status": target, "reason": reason,
            "evidenceId": evidence["evidenceId"]}


def key_history(db: Session, key_id: int) -> dict:
    """某把密钥所属 DID 的完整轮换历史。"""
    key = _get_key(db, key_id)
    logs = db.execute(
        select(DidKeyRotationLog).where(DidKeyRotationLog.did == key.did)
        .order_by(DidKeyRotationLog.id)
    ).scalars().all()
    all_keys = db.execute(
        select(DidKey).where(DidKey.did == key.did).order_by(DidKey.version)
    ).scalars().all()

    return {
        "did": key.did,
        "currentVersion": max((k.version for k in all_keys), default=0),
        "keys": [_to_item(k) for k in all_keys],
        "rotations": [
            {
                "id": log.id,
                "oldVersion": log.old_version,
                "newVersion": log.new_version,
                "oldPublicKey": log.old_public_key,
                "newPublicKey": log.new_public_key,
                "reason": log.reason,
                "operatorDid": log.operator_did,
                "evidenceId": log.evidence_id,
                "at": iso(log.created_at),
            }
            for log in logs
        ],
    }
