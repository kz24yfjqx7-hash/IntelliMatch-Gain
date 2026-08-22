"""存证服务。对上层业务只暴露一个 write_evidence()，链的具体实现被 chain.py 封装。"""
import logging

from sqlalchemy.orm import Session

from core.middleware import current_principal, current_trace_id
from modules.evidence.chain import get_chain

logger = logging.getLogger(__name__)


def write_evidence(db: Session, *, category: str, ref_id, payload: dict,
                   actor_did: str | None = None, trace_id: str | None = None) -> dict:
    """写一条存证。actor 与 traceId 缺省时自动从请求上下文取。

    存证失败不能把业务操作也带崩——链不可用时记 warning 并返回空引用，
    业务照常完成，事后可通过审计日志补链。
    """
    if actor_did is None:
        principal = current_principal.get()
        actor_did = principal.did if principal else None
    if trace_id is None:
        trace_id = current_trace_id.get()

    try:
        result = get_chain().write(db, category=category, ref_id=str(ref_id), payload=payload,
                                   actor_did=actor_did, trace_id=trace_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("存证上链失败 category=%s ref=%s：%s", category, ref_id, exc)
        return {"evidenceId": None, "hash": None, "blockHeight": None, "txId": None}

    # 契约 2.13：新存证上链要实时推给前端，链高度看板才能动起来
    from ws import manager as ws_manager

    ws_manager.push("evidence_written", {
        "evidenceId": result["evidenceId"],
        "category": category,
        "blockHeight": result["blockHeight"],
    }, trace_id)
    return result


# ---------------------------------------------------------------- 检索与凭证

import json  # noqa: E402
from datetime import datetime  # noqa: E402

from sqlalchemy import func, select  # noqa: E402

from core import redis_client  # noqa: E402
from core.exceptions import NotFoundError, ParamError  # noqa: E402
from core.response import iso, now_cst  # noqa: E402
from modules.evidence.model import ChainEvidence  # noqa: E402


def _to_item(record: ChainEvidence, with_payload: bool = False) -> dict:
    item = {
        "evidenceId": record.evidence_id,
        "category": record.category,
        "refId": record.ref_id,
        "actorDid": record.actor_did,
        "hash": record.payload_hash,
        "prevHash": record.prev_hash,
        "blockHash": record.block_hash,
        "blockHeight": record.block_height,
        "txId": record.tx_id,
        "traceId": record.trace_id,
        "createdAt": iso(record.created_at),
    }
    if with_payload:
        item["payload"] = record.payload_snapshot
    return item


def _parse_time(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError as exc:
        raise ParamError(f"{field} 格式非法，应为 ISO 8601：{value}") from exc


def search(db: Session, page: int, size: int, *, category: str | None = None,
           did: str | None = None, data_type: str | None = None,
           from_: str | None = None, to: str | None = None) -> tuple[list[dict], int]:
    stmt = select(ChainEvidence)
    count_stmt = select(func.count()).select_from(ChainEvidence)

    conditions = []
    if category:
        conditions.append(ChainEvidence.category == category)
    if did:
        conditions.append(ChainEvidence.actor_did == did)
    if data_type:
        # 存证表里没有 dataType，它是资产的属性，所以先查出该类型的资产 id 再反查。
        # ref_id 是字符串列，这里显式转成字符串比较，避免依赖数据库的隐式类型转换。
        from modules.asset.model import EnergyAsset

        asset_ids = db.execute(
            select(EnergyAsset.id).where(EnergyAsset.data_type == data_type)
        ).scalars().all()
        conditions.append(ChainEvidence.ref_id.in_([str(i) for i in asset_ids] or ["__none__"]))
    start = _parse_time(from_, "from")
    end = _parse_time(to, "to")
    if start:
        conditions.append(ChainEvidence.created_at >= start)
    if end:
        conditions.append(ChainEvidence.created_at <= end)

    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = db.execute(count_stmt).scalar_one()
    records = db.execute(
        stmt.order_by(ChainEvidence.block_height.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()
    return [_to_item(r) for r in records], total


def get_evidence(db: Session, evidence_id: str) -> dict:
    record = db.execute(
        select(ChainEvidence).where(ChainEvidence.evidence_id == evidence_id)
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError(f"存证 {evidence_id} 不存在")

    item = _to_item(record, with_payload=True)
    # 详情里直接带上完整性校验结果，前端不用再发一次请求
    item["verification"] = get_chain().verify(db, evidence_id)
    return item


def certificate(db: Session, evidence_id: str) -> dict:
    """导出存证凭证。凭证本身用平台身份的托管私钥签名，可离线校验。"""
    record = db.execute(
        select(ChainEvidence).where(ChainEvidence.evidence_id == evidence_id)
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError(f"存证 {evidence_id} 不存在")

    neighbour = db.execute(
        select(ChainEvidence.evidence_id, ChainEvidence.block_hash)
        .where(ChainEvidence.block_height == record.block_height + 1)
    ).first()

    body = {
        "certificateType": "EnergyTDS-Evidence-Certificate",
        "version": "1.0",
        "issuedAt": iso(now_cst()),
        "evidence": _to_item(record, with_payload=True),
        "chainProof": {
            "chainType": "LocalHashChain",
            "hashAlgorithm": "SM3",
            "blockFormula": "SM3(prevHash + payloadHash + timestamp)",
            "prevHash": record.prev_hash,
            "blockHash": record.block_hash,
            "nextEvidenceId": neighbour[0] if neighbour else None,
            "nextBlockHash": neighbour[1] if neighbour else None,
        },
        "verification": get_chain().verify(db, evidence_id),
    }

    signature, signer = _sign_certificate(db, body)
    body["signature"] = {"algorithm": "SM2", "signerDid": signer, "value": signature}
    return body


def _sign_certificate(db: Session, body: dict) -> tuple[str | None, str | None]:
    """用平台机构身份给凭证签名。托管密钥不可用时返回空签名，不影响凭证导出。"""
    from modules.did.model import DidIdentity
    from modules.did.service import sign_with_custody

    platform = db.execute(
        select(DidIdentity.did).where(DidIdentity.subject_type == "org")
        .order_by(DidIdentity.id).limit(1)
    ).scalar_one_or_none()
    if not platform:
        return None, None

    from core.gm_crypto import canonical_json

    signature = sign_with_custody(platform, canonical_json(body))
    return signature, platform


# ---------------------------------------------------------------- 演示专用：篡改与还原

_TAMPER_BACKUP_PREFIX = "evidence:tamper:backup:"


def tamper(db: Session, evidence_id: str, new_value: dict) -> dict:
    """**演示专用**：只改 payload_snapshot，不改 payload_hash，制造一条被篡改的记录。

    这模拟的是「有人绕过应用层直接改数据库」这种最典型的攻击场景。
    改完之后 /evidence/verify 会立刻发现摘要对不上，
    /evidence/chain/status 会把断裂点定位到这一块。

    原始快照会备份到 Redis，方便彩排完再还原（见 restore）。
    """
    record = db.execute(
        select(ChainEvidence).where(ChainEvidence.evidence_id == evidence_id)
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError(f"存证 {evidence_id} 不存在")

    original = dict(record.payload_snapshot or {})
    redis_client.safe_set(_TAMPER_BACKUP_PREFIX + evidence_id,
                          json.dumps(original, ensure_ascii=False))

    tampered = {**original, **new_value}
    record.payload_snapshot = tampered
    db.commit()

    logger.warning("【演示】存证 %s 的快照已被篡改，链完整性校验将会失败", evidence_id)
    return {
        "evidenceId": evidence_id,
        "tampered": True,
        "blockHeight": record.block_height,
        "originalPayload": original,
        "tamperedPayload": tampered,
        "hint": "请调用 /evidence/verify 或 /evidence/chain/status 查看校验结果；"
                "演示结束后调用 /evidence/demo/restore 还原",
    }


def restore(db: Session, evidence_id: str) -> dict:
    """把被篡改的存证还原回去，方便反复彩排。契约之外的补充接口，乙无需调用。"""
    record = db.execute(
        select(ChainEvidence).where(ChainEvidence.evidence_id == evidence_id)
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError(f"存证 {evidence_id} 不存在")

    backup = redis_client.safe_get(_TAMPER_BACKUP_PREFIX + evidence_id)
    if not backup:
        raise NotFoundError(f"没有找到 {evidence_id} 的原始快照备份，无法自动还原")

    record.payload_snapshot = json.loads(backup)
    db.commit()
    try:
        redis_client.client.delete(_TAMPER_BACKUP_PREFIX + evidence_id)
    except Exception:  # noqa: BLE001
        pass

    verification = get_chain().verify(db, evidence_id)
    return {"evidenceId": evidence_id, "restored": True, "verification": verification}
