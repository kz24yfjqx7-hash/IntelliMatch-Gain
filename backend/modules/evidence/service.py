"""存证服务。对上层业务只暴露一个 write_evidence()，链的具体实现被 chain.py 封装。"""
import logging
import threading

from sqlalchemy.orm import Session

from core.middleware import current_principal, current_trace_id
from modules.evidence.chain import get_chain

logger = logging.getLogger(__name__)

# 上链失败 → 写告警 → 告警本身又要上链 → 再失败……这里挡住递归
_ALERT_GUARD = threading.local()


def _alert_chain_write_failed(category: str, ref_id, exc: BaseException) -> None:
    """上链彻底失败时告警（B-010）。

    刻意**不**走 modules/audit/rules.fire：R01～R05 的规则码是契约 2.7 固定的，
    不能私自加 R06；而且 fire → _create_alert 内部还会 write_evidence，
    在链本身写不动的时候只会再失败一次。
    真正落地在 modules/audit/service.report_chain_write_failure()——
    审计写入一律收敛在审计模块里，业务模块不许出现手写的审计调用。
    """
    if getattr(_ALERT_GUARD, "active", False):
        return
    _ALERT_GUARD.active = True
    try:
        from modules.audit.service import report_chain_write_failure

        report_chain_write_failure(category, ref_id, exc)
    except Exception as inner:  # noqa: BLE001  告警失败也不能再往上抛
        logger.error("上链失败告警本身也失败了：%s", inner)
    finally:
        _ALERT_GUARD.active = False


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
        # B-010：并发死锁/锁超时已经在 chain.write 里重试过 MAX_WRITE_RETRY 次，
        # 走到这里说明确实写不进去。以前只打一行日志就把存证丢了，
        # 高危审计日志因此缺 evidence_id 而无人知晓——现在补一条告警，
        # 并把 chainError 回给调用方，让上层能感知（而不是拿到一个静悄悄的 None）。
        logger.error("存证上链失败 category=%s ref=%s：%s", category, ref_id, exc)
        _alert_chain_write_failed(category, ref_id, exc)
        return {"evidenceId": None, "hash": None, "blockHeight": None, "txId": None,
                "chainError": str(exc)}

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

from sqlalchemy import func, or_, select  # noqa: E402

from core import redis_client  # noqa: E402
from core.exceptions import NotFoundError, ParamError  # noqa: E402
from core.response import iso, now_cst  # noqa: E402
from modules.evidence.chain import invalidate_status_cache  # noqa: E402
from modules.evidence.model import ChainEvidence, ChainEvidenceBackup  # noqa: E402


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
           from_: str | None = None, to: str | None = None,
           owner_only: str | None = None) -> tuple[list[dict], int]:
    stmt = select(ChainEvidence)
    count_stmt = select(func.count()).select_from(ChainEvidence)

    conditions = []
    # DB-SCHEMA 权限矩阵：energy_subject 的 evidence:read 是 scope='own'。
    # 权限中心对列表接口只能放行到这里，真正的「仅自有」要落在 SQL 上（B-002）。
    # 写法与 modules/asset/service.py:list_assets 的 owner_only 保持一致。
    if owner_only:
        conditions.append(ChainEvidence.actor_did == owner_only)
    if category:
        conditions.append(ChainEvidence.category == category)
    if did:
        # 检索框一格通吃三种标识（前端占位符「存证ID / DID / refId」）：
        # 存证自身编号 evidence_id、操作者 actor_did、关联业务对象 ref_id，任一精确命中即返回。
        # 与 mock 参考实现口径一致；此前只按 actor_did 精确匹配，填 refId / 存证ID 都检索不出结果。
        kw = did.strip()
        conditions.append(or_(
            ChainEvidence.evidence_id == kw,
            ChainEvidence.actor_did == kw,
            ChainEvidence.ref_id == kw,
        ))
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
#
# B-001 / B-025：原始快照原来只写 Redis（按 key 覆盖 + TTL），连续演示两次就把
# 「第一次篡改后的数据」当成原始快照存了进去，restore 出来的还是被改过的内容，
# 链永久停在 broken；Redis 一清更是直接 1005「没有找到原始快照备份」。
#
# 现在改为**优先落库**（chain_evidence_backup），Redis 只作为迁移脚本尚未执行时的降级。
# 两条路径都遵守同样两条规则：
#   1. 已有备份就不覆盖 —— 保住的永远是最初那份，所以连续篡改 N 次仍能还原到最初状态；
#   2. restore 成功后删掉备份 —— 下一轮演示重新记录。

_TAMPER_BACKUP_PREFIX = "evidence:tamper:backup:"


def _probe_backup_table() -> bool:
    """**必须用独立会话探测**：表不存在时 MySQL / SQLite 都会让事务进入失败态，
    要 rollback 才能继续用；如果借调用方的会话去探，这个 rollback 会顺手把
    调用方还没提交的改动（比如 restore 刚写回去的 payload_snapshot）一起丢掉。
    """
    from core.database import SessionLocal

    with SessionLocal() as probe:
        probe.execute(select(func.count()).select_from(ChainEvidenceBackup)).scalar_one()
    return True


def _db_backup_available(db: Session | None = None) -> bool:
    """备份表能不能用。不在就现场建一张（和审计按月分表 ensure_table 一个路子）。

    存量库上 sql/03_migrate_20260822.sql 未必执行过，而演示现场没人愿意先跑迁移；
    建表权限被收走时（生产环境常见）就老老实实降级回 Redis，绝不 500。
    """
    try:
        return _probe_backup_table()
    except Exception as probe_exc:  # noqa: BLE001
        try:
            from core.database import engine

            ChainEvidenceBackup.__table__.create(engine, checkfirst=True)
            _probe_backup_table()
        except Exception as create_exc:  # noqa: BLE001
            logger.warning("chain_evidence_backup 不可用（%s；自动建表也失败：%s），"
                           "篡改演示的快照备份降级到 Redis", probe_exc, create_exc)
            return False

        # MySQL 的坑：调用方那条事务是在建表**之前**开的，再去访问这张新表会报
        # 1412 Table definition has changed。先回滚掉、让它重开一个干净事务。
        # 这个分支一个库一辈子只走一次（建完就再也进不来），
        # 而且此刻调用方还没有任何待提交的改动，回滚是安全的。
        if db is not None:
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        logger.info("chain_evidence_backup 不存在，已自动创建（等价于迁移脚本 03）")
        return True


def _save_backup(db: Session, record: ChainEvidence, original: dict) -> str:
    """保存原始快照，返回实际用到的存储方式。已存在的备份一律不覆盖。"""
    if _db_backup_available(db):
        exists = db.execute(
            select(ChainEvidenceBackup)
            .where(ChainEvidenceBackup.evidence_id == record.evidence_id)
        ).scalar_one_or_none()
        if exists is None:
            db.add(ChainEvidenceBackup(
                evidence_id=record.evidence_id, payload_snapshot=original,
                payload_hash=record.payload_hash, block_hash=record.block_hash,
                created_at=now_cst().replace(tzinfo=None),
            ))
            db.flush()
            return "db"
        return "db(kept)"

    key = _TAMPER_BACKUP_PREFIX + record.evidence_id
    if redis_client.safe_get(key) is None:
        redis_client.safe_set(key, json.dumps(original, ensure_ascii=False))
        return "redis"
    return "redis(kept)"


def _load_backup(db: Session, evidence_id: str) -> tuple[dict | None, str | None]:
    """取出原始快照，返回 (快照, 来源)。"""
    if _db_backup_available(db):
        row = db.execute(
            select(ChainEvidenceBackup)
            .where(ChainEvidenceBackup.evidence_id == evidence_id)
        ).scalar_one_or_none()
        if row is not None:
            return dict(row.payload_snapshot or {}), "db"

    raw = redis_client.safe_get(_TAMPER_BACKUP_PREFIX + evidence_id)
    if raw:
        return json.loads(raw), "redis"
    return None, None


def _drop_backup(db: Session, evidence_id: str) -> None:
    if _db_backup_available(db):
        row = db.execute(
            select(ChainEvidenceBackup)
            .where(ChainEvidenceBackup.evidence_id == evidence_id)
        ).scalar_one_or_none()
        if row is not None:
            db.delete(row)
            db.flush()
    redis_client.safe_delete(_TAMPER_BACKUP_PREFIX + evidence_id)


def tamper(db: Session, evidence_id: str, new_value: dict) -> dict:
    """**演示专用**：只改 payload_snapshot，不改 payload_hash，制造一条被篡改的记录。

    这模拟的是「有人绕过应用层直接改数据库」这种最典型的攻击场景。
    改完之后 /evidence/verify 会立刻发现摘要对不上，
    /evidence/chain/status 会把断裂点定位到这一块。

    篡改前的快照会保存下来（优先落库），方便彩排完再还原（见 restore）。
    """
    record = db.execute(
        select(ChainEvidence).where(ChainEvidence.evidence_id == evidence_id)
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError(f"存证 {evidence_id} 不存在")

    original = dict(record.payload_snapshot or {})
    backup_store = _save_backup(db, record, original)

    tampered = {**original, **new_value}
    record.payload_snapshot = tampered
    db.commit()
    invalidate_status_cache()

    logger.warning("【演示】存证 %s 的快照已被篡改（原始快照备份于 %s），"
                   "链完整性校验将会失败", evidence_id, backup_store)
    return {
        "evidenceId": evidence_id,
        "tampered": True,
        "blockHeight": record.block_height,
        "originalPayload": original,
        "tamperedPayload": tampered,
        "backupStore": backup_store,
        "hint": "请调用 /evidence/verify 或 /evidence/chain/status 查看校验结果；"
                "演示结束后调用 /evidence/demo/restore 还原"
                "（连续篡改多次也只会还原到最初状态，或用 /evidence/demo/restore-all 一键全还原）",
    }


def restore(db: Session, evidence_id: str) -> dict:
    """把被篡改的存证还原回去，方便反复彩排。契约之外的补充接口，乙无需调用。"""
    record = db.execute(
        select(ChainEvidence).where(ChainEvidence.evidence_id == evidence_id)
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError(f"存证 {evidence_id} 不存在")

    original, source = _load_backup(db, evidence_id)
    if original is None:
        raise NotFoundError(f"没有找到 {evidence_id} 的原始快照备份，无法自动还原")

    record.payload_snapshot = original
    _drop_backup(db, evidence_id)
    db.commit()
    invalidate_status_cache()

    verification = get_chain().verify(db, evidence_id)
    logger.info("【演示】存证 %s 已从 %s 备份还原，完整性 %s",
                evidence_id, source, verification["intact"])
    return {"evidenceId": evidence_id, "restored": True,
            "backupStore": source, "verification": verification}


def restore_all(db: Session) -> dict:
    """一键还原所有被篡改的存证。契约外接口，答辩现场彩排完一条命令收尾。"""
    ids: list[str] = []
    if _db_backup_available(db):
        ids = list(db.execute(
            select(ChainEvidenceBackup.evidence_id)
            .order_by(ChainEvidenceBackup.evidence_id)
        ).scalars().all())

    restored, failed = [], []
    for evidence_id in ids:
        try:
            restore(db, evidence_id)
            restored.append(evidence_id)
        except Exception as exc:  # noqa: BLE001  一条还不了不能拖累其它条
            logger.error("一键还原 %s 失败：%s", evidence_id, exc)
            db.rollback()
            failed.append({"evidenceId": evidence_id, "reason": str(exc)})

    invalidate_status_cache()
    chain_status = get_chain().status(db)
    return {
        "restored": restored,
        "restoredCount": len(restored),
        "failed": failed,
        "chain": {"intact": chain_status["intact"], "brokenAt": chain_status["brokenAt"],
                  "brokenAtEvidenceId": chain_status["brokenAtEvidenceId"]},
        "hint": "Redis 里的历史备份不在本接口范围内（迁移脚本执行前的旧备份请用单条 restore）"
                if not ids else None,
    }
