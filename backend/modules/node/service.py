"""节点服务。契约 2.8。

字段命名严格对齐现有前端 raspi/public/data/mock.json：
pvOutput / storageOutput / load / soc，一个字母都不能改，
否则乙那边七个存量页面全部要返工。
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core import redis_client
from core.exceptions import DidInvalidError, NotFoundError, ParamError
from core.response import iso, now_cst
from modules.evidence.service import write_evidence
from modules.node.model import NodeInfo, NodeMetric

logger = logging.getLogger(__name__)

# 设备上线的 nonce 防重放：用过的 nonce 十分钟内不能再用
_NONCE_PREFIX = "node:nonce:"
_NONCE_TTL = 600


def _f(value) -> float | None:
    return float(value) if isinstance(value, Decimal) else value


def _to_item(node: NodeInfo, did_status: str | None = None) -> dict:
    return {
        "id": node.id,
        "name": node.name,
        "status": node.status,
        "model": node.model,
        "did": node.did,
        "didStatus": did_status,
        "location": node.location,
        "capacityKw": _f(node.capacity_kw),
        "metrics": {
            "pvOutput": _f(node.pv_output),
            "storageOutput": _f(node.storage_output),
            "load": _f(node.load_kw),
            "soc": _f(node.soc),
        },
        "lastSeenAt": iso(node.last_seen_at),
    }


def list_nodes(db: Session, page: int = 1, size: int = 20) -> tuple[list[dict], int]:
    total = db.execute(select(func.count()).select_from(NodeInfo)).scalar_one()
    nodes = db.execute(
        select(NodeInfo).order_by(NodeInfo.id).offset((page - 1) * size).limit(size)
    ).scalars().all()
    statuses = _did_statuses(db, [n.did for n in nodes if n.did])
    return [_to_item(n, statuses.get(n.did)) for n in nodes], total


def _did_statuses(db: Session, dids: list[str]) -> dict[str, str]:
    if not dids:
        return {}
    from modules.did.model import DidIdentity

    rows = db.execute(
        select(DidIdentity.did, DidIdentity.status).where(DidIdentity.did.in_(dids))
    ).all()
    return dict(rows)


def get_node(db: Session, node_id: str) -> dict:
    node = db.get(NodeInfo, node_id)
    if node is None:
        raise NotFoundError(f"节点 {node_id} 不存在")
    statuses = _did_statuses(db, [node.did] if node.did else [])
    item = _to_item(node, statuses.get(node.did))

    latest = db.execute(
        select(NodeMetric).where(NodeMetric.node_id == node_id)
        .order_by(NodeMetric.ts.desc()).limit(1)
    ).scalar_one_or_none()
    item["latestMetricAt"] = iso(latest.ts) if latest else None
    return item


_INTERVALS = {"raw": 1, "hour": 1, "day": 24}
_MAX_POINTS = 2000


def get_metrics(db: Session, node_id: str, from_: str | None, to: str | None,
                interval: str = "hour") -> dict:
    """历史指标。

    降采样在 Python 侧做而不是写 SQL 的 DATE_FORMAT，
    是因为 MySQL 和 SQLite 的日期函数不一样，测试和生产会走两条代码路径。
    """
    if interval not in _INTERVALS:
        raise ParamError(f"interval 只能是 {'/'.join(_INTERVALS)}")
    if db.get(NodeInfo, node_id) is None:
        raise NotFoundError(f"节点 {node_id} 不存在")

    end = _parse(to) or now_cst().replace(tzinfo=None)
    start = _parse(from_) or (end - timedelta(days=7))

    rows = db.execute(
        select(NodeMetric)
        .where(NodeMetric.node_id == node_id, NodeMetric.ts.between(start, end))
        .order_by(NodeMetric.ts)
        .limit(_MAX_POINTS)     # 硬上限：树莓派上禁止一次拉几万个点进内存
    ).scalars().all()

    step = _INTERVALS[interval]
    sampled = rows[::step] if step > 1 else rows

    return {
        "nodeId": node_id,
        "interval": interval,
        "from": iso(start),
        "to": iso(end),
        "total": len(sampled),
        "truncated": len(rows) >= _MAX_POINTS,
        "items": [
            {
                "ts": iso(m.ts),
                "pvOutput": _f(m.pv_output),
                "storageOutput": _f(m.storage_output),
                "load": _f(m.load_kw),
                "soc": _f(m.soc),
                "price": _f(m.price),
            }
            for m in sampled
        ],
    }


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError as exc:
        raise ParamError(f"时间格式非法，应为 ISO 8601：{value}") from exc


def node_online(db: Session, node_id: str, did: str, nonce: str) -> dict:
    """设备上线。签名校验已由 @require_signature 装饰器完成，这里做业务侧校验。

    **无合法 DID 的节点一律拒绝接入**，这是需求文档里明确的硬要求。
    """
    node = db.get(NodeInfo, node_id)
    if node is None:
        raise NotFoundError(f"节点 {node_id} 不存在")

    if node.did and node.did != did:
        raise DidInvalidError(f"节点 {node_id} 已绑定身份 {node.did}，与请求身份不符")

    # nonce 防重放：同一个 nonce 十分钟内只能用一次
    nonce_key = f"{_NONCE_PREFIX}{did}:{nonce}"
    if redis_client.safe_exists(nonce_key):
        raise DidInvalidError("nonce 已被使用过，疑似重放攻击")
    redis_client.safe_set(nonce_key, "1", ex=_NONCE_TTL)

    node.status = "online"
    node.last_seen_at = now_cst().replace(tzinfo=None)
    if not node.did:
        node.did = did

    evidence = write_evidence(db, category="identity", ref_id=node_id, payload={
        "action": "node:online", "nodeId": node_id, "did": did,
        "at": iso(now_cst()),
    }, actor_did=did)
    db.commit()

    from ws import manager as ws_manager

    ws_manager.push("node_status", {
        "nodeId": node_id, "status": "online",
        "metrics": _to_item(node)["metrics"],
    })

    return {
        "accepted": True,
        "nodeId": node_id,
        "sessionToken": evidence["txId"],
        "evidenceId": evidence["evidenceId"],
    }


def snapshot_for_ws(db: Session) -> list[dict]:
    """给 WebSocket 定时广播用的节点快照。"""
    nodes = db.execute(select(NodeInfo).order_by(NodeInfo.id)).scalars().all()
    return [
        {"nodeId": n.id, "status": n.status, "metrics": _to_item(n)["metrics"]}
        for n in nodes
    ]
