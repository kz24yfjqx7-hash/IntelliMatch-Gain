"""数据资产服务。契约 2.4。

两条硬要求：
1. **原始 payload 存 MySQL，链上只存 SM3 摘要**——数据不出域是可信数据空间的前提
2. 资产的每一次状态流转都要留溯源记录（登记 → 授权 → 访问 → 计算）
"""
import logging
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.exceptions import DidInvalidError, NotFoundError
from core.gm_crypto import payload_hash as calc_payload_hash
from core.middleware import current_principal, current_trace_id
from core.response import iso, now_cst
from core.retry import run_with_retry
from modules.algo import client as algo_client
from modules.asset.model import EnergyAsset, EnergyAssetLineage
from modules.evidence.service import write_evidence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 分类分级

# 会显著抬高敏感度的字段名。资产 payload 里出现这些键就说明数据能关联到具体主体或位置。
_SENSITIVE_FIELDS = {
    "location": 0.40, "gps": 0.40, "lat": 0.35, "lng": 0.35, "address": 0.35,
    "subjectdid": 0.30, "ownerdid": 0.30, "userid": 0.30, "meterid": 0.20,
    "phone": 0.35, "idcard": 0.45,
}
_FREQ_SCORE = {"second": 1.0, "minute": 0.80, "hour": 0.50, "day": 0.20}
# 调度指令直接关系电网运行安全，天然比采集数据敏感
_TYPE_BONUS = {"dispatch": 0.30, "storage": 0.10, "pv": 0.0, "wind": 0.0, "load": 0.05}

_LEVEL_DESC = {
    "L1": "公开级", "L2": "内部级", "L3": "敏感级", "L4": "核心级",
}


def _score_to_level(score: float) -> str:
    if score < 0.35:
        return "L1"
    if score < 0.55:
        return "L2"
    if score < 0.78:
        return "L3"
    return "L4"


def classify_locally(data_type: str, fields: list[str], freq: str = "minute",
                     volume: int = 1) -> dict:
    """规则化分类分级。

    算法服务不可用时的兜底实现。乙的 algo-service 用 k-means + 规则加权做得更细，
    但兜底逻辑必须存在——总不能因为算法服务挂了就不让登记数据。
    """
    lowered = [f.lower() for f in fields]
    sensitivity = min(1.0, sum(w for k, w in _SENSITIVE_FIELDS.items()
                               if any(k in f for f in lowered)))
    granularity = _FREQ_SCORE.get(freq, 0.5)
    if volume >= 100_000:
        volume_score = 0.85
    elif volume >= 10_000:
        volume_score = 0.65
    elif volume >= 1_000:
        volume_score = 0.45
    else:
        volume_score = 0.20

    score = min(1.0, 0.45 * sensitivity + 0.35 * granularity + 0.20 * volume_score
                + _TYPE_BONUS.get(data_type, 0.0))
    level = _score_to_level(score)

    reasons = []
    if sensitivity >= 0.3:
        hit = [k for k in _SENSITIVE_FIELDS if any(k in f for f in lowered)]
        reasons.append(f"包含敏感字段 {'/'.join(hit)}")
    if granularity >= 0.8:
        reasons.append(f"采集粒度为{'秒' if freq == 'second' else '分钟'}级，可反推用能行为")
    if volume >= 10_000:
        reasons.append(f"数据量 {volume} 条，聚合后信息量大")
    if data_type == "dispatch":
        reasons.append("调度指令关系电网运行安全")
    if not reasons:
        reasons.append("仅含聚合统计量，不含主体标识")

    return {
        "level": level,
        "score": round(score, 3),
        "reason": "；".join(reasons) + f"，判定为{_LEVEL_DESC[level]}",
        "factors": {
            "sensitivity": round(sensitivity, 2),
            "granularity": round(granularity, 2),
            "volume": round(volume_score, 2),
        },
        "source": "rule",
    }


def classify(records: list[dict]) -> dict:
    """契约 2.4 POST /assets/classify：优先走算法服务，不可用则本地规则兜底。"""
    payload = [
        {"dataType": r["dataType"], "fields": r.get("fields", []),
         "freq": r.get("freq", "minute"), "volume": r.get("volume", 1)}
        for r in records
    ]
    remote = algo_client.classify(payload, raise_on_error=False)
    if remote and "results" in remote:
        remote["source"] = "algo"
        return remote

    logger.info("算法服务不可用，本次分类分级退回本地规则实现")
    results = []
    for i, r in enumerate(payload):
        local = classify_locally(r["dataType"], r["fields"], r["freq"], r["volume"])
        results.append({
            "index": i, "level": local["level"], "score": local["score"],
            "reason": local["reason"], "cluster": None, "factors": local["factors"],
        })
    return {"results": results, "clusterCenters": [], "source": "rule"}


def _fields_of(payload: dict) -> list[str]:
    """把 payload 摊平成字段名列表，供分级用。只看键名，不看值。"""
    fields = []

    def walk(node, prefix=""):
        if isinstance(node, dict):
            for k, v in node.items():
                fields.append(f"{prefix}{k}")
                walk(v, f"{prefix}{k}.")
        elif isinstance(node, list) and node:
            walk(node[0], prefix)

    walk(payload)
    return fields


# ---------------------------------------------------------------- 登记

def create_asset(db: Session, payload) -> dict:
    """数据登记：校验来源身份 → 计算摘要 → 自动分级 → 落库 → 上链 → 记溯源。"""
    _assert_source_did_active(db, payload.sourceDid)

    principal = current_principal.get()
    trace_id = current_trace_id.get()
    phash = calc_payload_hash(payload.payload)

    level = payload.level
    classify_result = None
    if level is None:
        classify_result = classify_locally(
            payload.dataType, _fields_of(payload.payload),
            payload.payload.get("interval", "minute").replace("1min", "minute"),
            payload.recordCount,
        )
        remote = algo_client.classify([{
            "dataType": payload.dataType, "fields": _fields_of(payload.payload),
            "freq": "minute", "volume": payload.recordCount,
        }], raise_on_error=False)
        if remote and remote.get("results"):
            first = remote["results"][0]
            classify_result = {"level": first["level"], "score": first.get("score"),
                               "reason": first.get("reason"), "source": "algo"}
        level = classify_result["level"]

    def _persist() -> dict:
        asset = EnergyAsset(
            name=payload.name, data_type=payload.dataType, source_did=payload.sourceDid,
            owner_did=payload.sourceDid, level=level, payload=payload.payload,
            payload_hash=phash, description=payload.description,
            record_count=payload.recordCount, auth_status="unauthorized",
            classify_score=Decimal(str(classify_result["score"])) if classify_result and classify_result.get("score") is not None else None,
            classify_reason=classify_result["reason"] if classify_result else None,
            trace_id=trace_id,
        )
        db.add(asset)
        db.flush()

        evidence = write_evidence(db, category="data", ref_id=asset.id, payload={
            "action": "asset:register", "assetId": asset.id, "name": payload.name,
            "dataType": payload.dataType, "level": level, "payloadHash": phash,
            "sourceDid": payload.sourceDid, "createdAt": iso(now_cst()),
        })
        asset.evidence_id = evidence["evidenceId"]
        asset.chain_tx_id = evidence["txId"]

        _add_lineage(db, asset.id, "register", evidence["evidenceId"], phash,
                     "数据登记并生成 SM3 摘要上链", actor_did=payload.sourceDid)
        db.commit()

        return {
            "id": asset.id,
            "hash": phash,
            "level": level,
            "chainTxId": asset.chain_tx_id,
            "evidenceId": asset.evidence_id,
            "authStatus": asset.auth_status,
            "classifySource": classify_result["source"] if classify_result else "manual",
            "classifyReason": asset.classify_reason,
            "createdAt": iso(asset.created_at),
        }

    # B-014：并发登记时存证上链撞死锁 → MySQL 回滚整个事务 → 紧接着的
    # `UPDATE energy_asset SET evidence_id=...` 匹配 0 行 → StaleDataError → 500。
    # 整段写事务作为一个可重放单元，实在冲突才按契约返回 1006。
    return run_with_retry(db, _persist, what="数据资产登记")


def _assert_source_did_active(db: Session, did: str) -> None:
    """无合法身份的数据一律拒收——这是可信数据空间与普通数据库的分界线。"""
    from modules.did.model import DidIdentity

    status = db.execute(
        select(DidIdentity.status).where(DidIdentity.did == did)
    ).scalar_one_or_none()
    if status is None:
        raise DidInvalidError(f"数据来源身份 {did} 不存在，拒绝登记")
    if status != "active":
        raise DidInvalidError(f"数据来源身份状态为 {status}，拒绝登记")


def _add_lineage(db: Session, asset_id: int, stage: str, evidence_id: str | None,
                 hash_value: str | None, detail: str, actor_did: str | None = None) -> None:
    principal = current_principal.get()
    db.add(EnergyAssetLineage(
        asset_id=asset_id, stage=stage,
        actor_did=actor_did or (principal.did if principal else None),
        evidence_id=evidence_id, hash=hash_value,
        trace_id=current_trace_id.get(), detail=detail,
    ))


# ---------------------------------------------------------------- 查询

def _to_item(asset: EnergyAsset, with_payload: bool = False) -> dict:
    item = {
        "id": asset.id,
        "name": asset.name,
        "dataType": asset.data_type,
        "sourceDid": asset.source_did,
        "ownerDid": asset.owner_did,
        "level": asset.level,
        "hash": asset.payload_hash,
        "description": asset.description,
        "recordCount": asset.record_count,
        "authStatus": asset.auth_status,
        "classifyScore": float(asset.classify_score) if asset.classify_score is not None else None,
        "classifyReason": asset.classify_reason,
        "chainTxId": asset.chain_tx_id,
        "evidenceId": asset.evidence_id,
        "createdAt": iso(asset.created_at),
    }
    if with_payload:
        item["payload"] = asset.payload
    return item


def list_assets(db: Session, page: int, size: int, *, data_type: str | None = None,
                level: str | None = None, source_did: str | None = None,
                keyword: str | None = None, owner_only: str | None = None
                ) -> tuple[list[dict], int]:
    stmt = select(EnergyAsset)
    count_stmt = select(func.count()).select_from(EnergyAsset)

    conditions = []
    if data_type:
        conditions.append(EnergyAsset.data_type == data_type)
    if level:
        conditions.append(EnergyAsset.level == level)
    if source_did:
        conditions.append(EnergyAsset.source_did == source_did)
    if keyword:
        conditions.append(EnergyAsset.name.like(f"%{keyword}%"))
    # 权限 scope='own' 的角色只能看到自己的数据，这里是 SQL 层的落地
    if owner_only:
        conditions.append(EnergyAsset.owner_did == owner_only)

    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = db.execute(count_stmt).scalar_one()
    assets = db.execute(
        stmt.order_by(EnergyAsset.id.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()
    return [_to_item(a) for a in assets], total


def get_asset(db: Session, asset_id: int, record_access: bool = True) -> dict:
    asset = db.get(EnergyAsset, asset_id)
    if asset is None:
        raise NotFoundError(f"数据资产 {asset_id} 不存在")

    if record_access:
        # 访问也要留痕。L3/L4 敏感数据的访问额外上链，低敏感度的只记溯源表，
        # 否则每次点开详情都写一个区块，链会被读操作撑爆。
        evidence_id = None
        if asset.level in ("L3", "L4"):
            evidence = write_evidence(db, category="data", ref_id=asset.id, payload={
                "action": "asset:access", "assetId": asset.id, "level": asset.level,
                "at": iso(now_cst()),
            })
            evidence_id = evidence["evidenceId"]
        _add_lineage(db, asset.id, "access", evidence_id, asset.payload_hash,
                     f"访问 {asset.level} 级数据资产")
        db.commit()

    return _to_item(asset, with_payload=True)


def get_lineage(db: Session, asset_id: int) -> dict:
    asset = db.get(EnergyAsset, asset_id)
    if asset is None:
        raise NotFoundError(f"数据资产 {asset_id} 不存在")

    records = db.execute(
        select(EnergyAssetLineage).where(EnergyAssetLineage.asset_id == asset_id)
        .order_by(EnergyAssetLineage.id)
    ).scalars().all()

    return {
        "assetId": asset_id,
        "traceId": asset.trace_id,
        "chain": [
            {
                "stage": r.stage,
                "at": iso(r.created_at),
                "actorDid": r.actor_did,
                "evidenceId": r.evidence_id,
                "hash": r.hash,
                "detail": r.detail,
            }
            for r in records
        ],
    }


def stats(db: Session) -> dict:
    by_level = db.execute(
        select(EnergyAsset.level, func.count()).group_by(EnergyAsset.level)
        .order_by(EnergyAsset.level)
    ).all()
    by_type = db.execute(
        select(EnergyAsset.data_type, func.count()).group_by(EnergyAsset.data_type)
    ).all()
    total = db.execute(select(func.count()).select_from(EnergyAsset)).scalar_one()
    authorized = db.execute(
        select(func.count()).select_from(EnergyAsset)
        .where(EnergyAsset.auth_status == "authorized")
    ).scalar_one()
    on_chain = db.execute(
        select(func.count()).select_from(EnergyAsset)
        .where(EnergyAsset.evidence_id.isnot(None))
    ).scalar_one()

    return {
        "byLevel": [{"level": lv, "count": c} for lv, c in by_level],
        "byType": [{"dataType": t, "count": c} for t, c in by_type],
        "total": total,
        "authorized": authorized,
        "onChain": on_chain,
    }


def mark_authorized(db: Session, asset_id: int, evidence_id: str | None) -> None:
    """权限审批通过时由权限中心回调，把资产标成已授权并记一条溯源。"""
    asset = db.get(EnergyAsset, asset_id)
    if asset is None:
        return
    asset.auth_status = "authorized"
    _add_lineage(db, asset_id, "authorize", evidence_id, asset.payload_hash,
                 "权限申请审批通过，授权生效")
