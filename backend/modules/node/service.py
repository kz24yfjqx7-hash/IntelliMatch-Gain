"""节点服务。契约 2.8。

字段命名严格对齐现有前端 raspi/public/data/mock.json：
pvOutput / storageOutput / load / soc，一个字母都不能改，
否则乙那边七个存量页面全部要返工。
"""
import logging
import random
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
    """给 WebSocket 定时广播用的节点快照。契约 2.13 payload：{nodeId, status, metrics}。"""
    nodes = db.execute(select(NodeInfo).order_by(NodeInfo.id)).scalars().all()
    return [_ws_payload(n) for n in nodes]


def _ws_payload(node: NodeInfo) -> dict:
    return {"nodeId": node.id, "status": node.status, "metrics": _to_item(node)["metrics"]}


# ---------------------------------------------------------------- 实时指标步进（WS 5 秒广播用）
# 演示环境里没有真实光伏/储能硬件在往上报数据，node_info 的四个指标如果不动，
# 每 5 秒推出去的就是同一串常数，前端曲线是一条直线，等于没有"实时"。
# 这里的做法是**在库里的真实值上做有界随机游走并落库**：
#   - 推出去的值 == 数据库里真正存着的值 == GET /nodes 读到的值，三者永远一致，不是凭空编的展示数据；
#   - soc 与 storageOutput 的符号耦合（放电 soc 降、充电 soc 升），不会出现放电还涨电量的物理谬误；
#   - offline 节点不动（离线设备本来就不该有新数据）；
#   - 只在有 WebSocket 客户端连着时才被调用，没人看时既不打库也不改数。
_WALK_RATIO = 0.03          # 每次步进最多波动额定容量的 3%
_LOAD_HEADROOM = 1.5        # 负荷允许到额定容量的 1.5 倍
_SOC_MIN, _SOC_MAX = 5.0, 100.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _walk(node: NodeInfo) -> None:
    capacity = float(node.capacity_kw or 100) or 100.0
    step = capacity * _WALK_RATIO
    pv = _clamp(float(node.pv_output or 0) + random.uniform(-step, step), 0.0, capacity)
    storage = _clamp(float(node.storage_output or 0) + random.uniform(-step, step),
                     -capacity, capacity)
    load = _clamp(float(node.load_kw or 0) + random.uniform(-step, step),
                  0.0, capacity * _LOAD_HEADROOM)
    # storageOutput > 0 表示放电（soc 下降），< 0 表示充电（soc 上升）
    soc = _clamp(float(node.soc or 0) - storage / capacity * 1.0, _SOC_MIN, _SOC_MAX)

    node.pv_output = Decimal(f"{pv:.2f}")
    node.storage_output = Decimal(f"{storage:.2f}")
    node.load_kw = Decimal(f"{load:.2f}")
    node.soc = Decimal(f"{soc:.2f}")
    node.last_seen_at = now_cst().replace(tzinfo=None)


def tick_node_metrics(db: Session) -> list[dict]:
    """步进一次实时指标并返回全部节点的 node_status payload。

    返回值直接就是契约 2.13 `node_status` 的 payload 列表，一个节点一条消息。
    """
    nodes = db.execute(select(NodeInfo).order_by(NodeInfo.id)).scalars().all()
    moved = False
    for node in nodes:
        if node.status == "offline":
            continue
        _walk(node)
        moved = True
    if moved:
        try:
            db.commit()
        except Exception as exc:  # noqa: BLE001  落库失败不能让广播线程死掉
            db.rollback()
            logger.warning("实时指标落库失败：%s", exc)
    return [_ws_payload(n) for n in nodes]
