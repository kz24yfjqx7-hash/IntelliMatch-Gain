"""安全审计服务。契约 2.7。

写入路径：@audited 装饰器 → write_audit_log() → 当月分表 + 高危副本进 Redis + 上链 + WebSocket
查询路径：跨月 UNION ALL 合并 → 分页返回

**写审计绝不能把业务带崩**：本文件所有对外函数都自己吞异常，
最坏情况是丢一条日志，而不是让一次正常的业务请求返回 500。
"""
import csv
import io
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core import redis_client
from core.database import SessionLocal
from core.exceptions import NotFoundError, ParamError
from core.gm_crypto import sm3_tag
from core.response import iso, now_cst
from modules.audit import sharding
from modules.audit.model import AuditAlert

logger = logging.getLogger(__name__)

_HIGH_RISK = ("high", "critical")
_REDIS_HIGH_RISK_KEY = "audit:highrisk:recent"


def ensure_current_month_table() -> None:
    """启动时调一次，保证当月分表存在。"""
    try:
        with SessionLocal() as db:
            sharding.ensure_table(db, sharding.table_of(now_cst()))
    except Exception as exc:  # noqa: BLE001
        logger.error("创建当月审计分表失败：%s", exc)


# ---------------------------------------------------------------- 写入

def write_audit_log(payload: dict, to_chain: bool | None = None) -> str | None:
    """写一条审计日志。返回 evidenceId（未上链则为 None）。

    这个函数由 core/middleware.py 的 @audited 装饰器调用，
    业务代码里不应该出现对它的直接调用。
    """
    try:
        return _write(payload, to_chain)
    except Exception as exc:  # noqa: BLE001
        logger.error("写审计日志失败（业务不受影响）：%s | %s", exc,
                     json.dumps(payload, ensure_ascii=False, default=str)[:300])
        return None


def _write(payload: dict, to_chain: bool | None) -> str | None:
    risk = payload.get("riskLevel", "low")
    # 默认策略：高危及以上自动上链。显式传 to_chain 可以覆盖
    should_chain = to_chain if to_chain is not None else risk in _HIGH_RISK

    now = now_cst()
    row = {
        "trace_id": payload.get("traceId"),
        "actor_did": payload.get("actorDid"),
        "actor_name": payload.get("actorName"),
        "module": payload.get("module", "unknown"),
        "action": payload.get("action", "unknown"),
        "resource_type": payload.get("resourceType"),
        "resource_id": payload.get("resourceId"),
        "result": payload.get("result", "success"),
        "risk_level": risk,
        "detail": payload.get("detail"),
        "ip": payload.get("ip"),
        "evidence_id": None,
        "hash": None,
        "created_at": now.replace(tzinfo=None),
    }
    # 日志本身也做摘要，这样即使有人直接改数据库，也能通过重算发现
    row["hash"] = sm3_tag(json.dumps(
        {k: str(v) for k, v in row.items() if k not in ("evidence_id", "hash")},
        ensure_ascii=False, sort_keys=True,
    ))

    with SessionLocal() as db:
        if should_chain:
            from modules.evidence.service import write_evidence

            evidence = write_evidence(db, category="audit",
                                      ref_id=payload.get("resourceId") or payload.get("action"),
                                      payload={
                                          "action": "audit:log",
                                          "module": row["module"], "operation": row["action"],
                                          "result": row["result"], "riskLevel": risk,
                                          "actorDid": row["actor_did"], "detail": row["detail"],
                                          "at": iso(now),
                                      },
                                      actor_did=row["actor_did"], trace_id=row["trace_id"])
            row["evidence_id"] = evidence["evidenceId"]

        table = sharding.ensure_table(db, sharding.table_of(now))
        cols = ", ".join(sharding.COLUMNS)
        binds = ", ".join(f":{c}" for c in sharding.COLUMNS)
        db.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({binds})"), row)
        db.commit()

    if risk in _HIGH_RISK:
        # 高危日志额外存一份到 Redis，实时告警面板不用查库就能秒出
        redis_client.safe_lpush_trim(_REDIS_HIGH_RISK_KEY, json.dumps({
            **{k: (iso(v) if isinstance(v, datetime) else v) for k, v in row.items()},
        }, ensure_ascii=False), max_len=500)

    _push_log_message(row)
    return row["evidence_id"]


def _push_log_message(row: dict) -> None:
    """把值得在前端底部日志栏滚动的事件推出去。低风险的读操作不推，否则日志栏会刷屏。"""
    if row["result"] == "success" and row["risk_level"] == "low":
        return
    from ws import manager as ws_manager

    level = {"low": "info", "medium": "warn", "high": "error", "critical": "error"}[row["risk_level"]]
    ws_manager.push("log", {
        "level": level,
        "module": row["module"],
        "content": f"{row['actor_name'] or '匿名'} {row['action']} → {row['result']}"
                   + (f"：{row['detail']}" if row["detail"] else ""),
        "traceId": row["trace_id"],
    }, row["trace_id"])


# ---------------------------------------------------------------- 查询

def _parse_time(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError as exc:
        raise ParamError(f"{field} 格式非法，应为 ISO 8601：{value}") from exc


def _row_to_item(row) -> dict:
    m = row._mapping
    return {
        "id": m["id"],
        "traceId": m["trace_id"],
        "actorDid": m["actor_did"],
        "actorName": m["actor_name"],
        "module": m["module"],
        "action": m["action"],
        "resourceType": m["resource_type"],
        "resourceId": m["resource_id"],
        "result": m["result"],
        "riskLevel": m["risk_level"],
        "detail": m["detail"],
        "ip": m["ip"],
        "evidenceId": m["evidence_id"],
        "hash": m["hash"],
        "at": iso(m["created_at"]) if isinstance(m["created_at"], datetime) else m["created_at"],
    }


def _build_filters(*, trace_id=None, actor_did=None, action=None, risk_level=None,
                   module=None, result=None, keyword=None,
                   start=None, end=None) -> tuple[str, dict]:
    clauses, params = [], {}
    if trace_id:
        clauses.append("trace_id = :trace_id")
        params["trace_id"] = trace_id
    if actor_did:
        clauses.append("actor_did = :actor_did")
        params["actor_did"] = actor_did
    if action:
        clauses.append("action = :action")
        params["action"] = action
    if risk_level:
        clauses.append("risk_level = :risk_level")
        params["risk_level"] = risk_level
    if module:
        clauses.append("module = :module")
        params["module"] = module
    if result:
        clauses.append("result = :result")
        params["result"] = result
    if keyword:
        clauses.append("(detail LIKE :kw OR action LIKE :kw OR actor_name LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    if start:
        clauses.append("created_at >= :start")
        params["start"] = start
    if end:
        clauses.append("created_at <= :end")
        params["end"] = end
    return " AND ".join(clauses), params


def query_logs(db: Session, page: int, size: int, *, trace_id=None, actor_did=None,
               action=None, risk_level=None, module=None, result=None, keyword=None,
               from_=None, to=None) -> tuple[list[dict], int]:
    start, end = _parse_time(from_, "from"), _parse_time(to, "to")
    tables = sharding.tables_in_range(db, start, end)
    if not tables:
        return [], 0

    where, params = _build_filters(trace_id=trace_id, actor_did=actor_did, action=action,
                                   risk_level=risk_level, module=module, result=result,
                                   keyword=keyword, start=start, end=end)

    count_sql = " UNION ALL ".join(
        f"SELECT COUNT(*) AS c FROM {sharding.assert_valid(t)}"
        + (f" WHERE {where}" if where else "")
        for t in tables
    )
    total = sum(r[0] for r in db.execute(text(f"SELECT * FROM ({count_sql}) AS m"), params))

    sql = sharding.union_query(tables, where, order="created_at DESC, id DESC",
                               limit="LIMIT :limit OFFSET :offset")
    rows = db.execute(text(sql), {**params, "limit": size, "offset": (page - 1) * size}).all()
    return [_row_to_item(r) for r in rows], total


def trace_detail(db: Session, trace_id: str) -> dict:
    """任务级全链路追踪：把一个 traceId 下的所有步骤按时间串起来。"""
    tables = sharding.tables_in_range(db, None, None)
    if not tables:
        raise NotFoundError(f"未找到 traceId {trace_id} 的记录")

    sql = sharding.union_query(tables, "trace_id = :trace_id", order="created_at ASC, id ASC")
    rows = db.execute(text(sql), {"trace_id": trace_id}).all()
    if not rows:
        raise NotFoundError(f"未找到 traceId {trace_id} 的记录")

    items = [_row_to_item(r) for r in rows]
    times = [r._mapping["created_at"] for r in rows]
    duration = 0
    if isinstance(times[0], datetime) and isinstance(times[-1], datetime):
        duration = int((times[-1] - times[0]).total_seconds() * 1000)

    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    peak_risk = max((i["riskLevel"] for i in items), key=lambda r: order.get(r, 0))
    final_result = "denied" if any(i["result"] == "denied" for i in items) else (
        "failed" if any(i["result"] == "failed" for i in items) else "success")

    return {
        "traceId": trace_id,
        "summary": {
            "startAt": items[0]["at"],
            "endAt": items[-1]["at"],
            "durationMs": duration,
            "actorDid": items[0]["actorDid"],
            "actorName": items[0]["actorName"],
            "result": final_result,
            "riskLevel": peak_risk,
            "steps": len(items),
        },
        "steps": [
            {
                "seq": i + 1, "module": it["module"], "action": it["action"],
                "at": it["at"], "result": it["result"], "riskLevel": it["riskLevel"],
                "detail": it["detail"], "evidenceId": it["evidenceId"],
            }
            for i, it in enumerate(items)
        ],
    }


def export_csv(db: Session, *, risk_level=None, from_=None, to=None,
               max_rows: int = 5000) -> tuple[str, int]:
    """导出 CSV。契约 2.7 说明这个接口返回文件流，不套统一包装。

    上限 5000 行是硬约束——树莓派上把几十万行日志拼成一个字符串会直接把内存打爆。
    """
    start, end = _parse_time(from_, "from"), _parse_time(to, "to")
    tables = sharding.tables_in_range(db, start, end)
    if not tables:
        return "", 0

    where, params = _build_filters(risk_level=risk_level, start=start, end=end)
    sql = sharding.union_query(tables, where, order="created_at DESC, id DESC",
                               limit="LIMIT :limit")
    rows = db.execute(text(sql), {**params, "limit": max_rows}).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["日志ID", "追踪ID", "操作主体DID", "主体名称", "模块", "操作",
                     "资源类型", "资源ID", "结果", "风险等级", "详情", "IP",
                     "存证ID", "摘要", "时间"])
    for row in rows:
        it = _row_to_item(row)
        writer.writerow([it["id"], it["traceId"], it["actorDid"], it["actorName"],
                         it["module"], it["action"], it["resourceType"], it["resourceId"],
                         it["result"], it["riskLevel"], it["detail"], it["ip"],
                         it["evidenceId"], it["hash"], it["at"]])
    return buffer.getvalue(), len(rows)


# ---------------------------------------------------------------- 告警

def _alert_to_item(alert: AuditAlert) -> dict:
    return {
        "id": alert.id,
        "alertId": alert.alert_id,
        "ruleCode": alert.rule_code,
        "ruleName": alert.rule_name,
        "riskLevel": alert.risk_level,
        "message": alert.message,
        "actorDid": alert.actor_did,
        "actorName": alert.actor_name,
        "hitCount": alert.hit_count,
        "traceId": alert.trace_id,
        "status": alert.status,
        "ackedBy": alert.acked_by,
        "ackedAt": iso(alert.acked_at),
        "evidenceId": alert.evidence_id,
        "createdAt": iso(alert.created_at),
    }


def list_alerts(db: Session, page: int, size: int, *, status: str | None = None,
                rule_code: str | None = None) -> tuple[list[dict], int]:
    stmt = select(AuditAlert)
    count_stmt = select(func.count()).select_from(AuditAlert)
    if status:
        stmt = stmt.where(AuditAlert.status == status)
        count_stmt = count_stmt.where(AuditAlert.status == status)
    if rule_code:
        stmt = stmt.where(AuditAlert.rule_code == rule_code)
        count_stmt = count_stmt.where(AuditAlert.rule_code == rule_code)

    total = db.execute(count_stmt).scalar_one()
    alerts = db.execute(
        stmt.order_by(AuditAlert.id.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()
    return [_alert_to_item(a) for a in alerts], total


def ack_alert(db: Session, alert_id: int, principal) -> dict:
    alert = db.get(AuditAlert, alert_id)
    if alert is None:
        raise NotFoundError(f"告警 {alert_id} 不存在")
    if alert.status == "acked":
        from core.exceptions import ConflictError

        raise ConflictError("该告警已确认")

    alert.status = "acked"
    alert.acked_by = principal.real_name or principal.username
    alert.acked_at = now_cst().replace(tzinfo=None)
    db.commit()
    return _alert_to_item(alert)


# ---------------------------------------------------------------- 看板与报告

def stats(db: Session) -> dict:
    now = now_cst().replace(tzinfo=None)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=6)

    tables = sharding.tables_in_range(db, week_ago, now)
    if not tables:
        return {"todayLogs": 0, "highRiskLogs": 0, "openAlerts": 0, "onChainLogs": 0,
                "byModule": [], "byRisk": [], "trend": []}

    def _scalar(where: str, params: dict) -> int:
        sql = " UNION ALL ".join(
            f"SELECT COUNT(*) AS c FROM {sharding.assert_valid(t)} WHERE {where}"
            for t in tables
        )
        return sum(r[0] for r in db.execute(text(f"SELECT * FROM ({sql}) AS m"), params))

    today_logs = _scalar("created_at >= :today", {"today": today})
    high_risk = _scalar("created_at >= :today AND risk_level IN ('high','critical')",
                        {"today": today})
    on_chain = _scalar("created_at >= :today AND evidence_id IS NOT NULL", {"today": today})

    def _group(column: str) -> list[tuple]:
        sql = " UNION ALL ".join(
            f"SELECT {column} AS k, COUNT(*) AS c FROM {sharding.assert_valid(t)} "
            f"WHERE created_at >= :week GROUP BY {column}"
            for t in tables
        )
        rows = db.execute(
            text(f"SELECT k, SUM(c) AS c FROM ({sql}) AS m GROUP BY k ORDER BY c DESC"),
            {"week": week_ago},
        ).all()
        return [(r[0], int(r[1])) for r in rows]

    open_alerts = db.execute(
        select(func.count()).select_from(AuditAlert).where(AuditAlert.status == "open")
    ).scalar_one()

    trend = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        nxt = day + timedelta(days=1)
        total = _scalar("created_at >= :d AND created_at < :n", {"d": day, "n": nxt})
        high = _scalar("created_at >= :d AND created_at < :n "
                       "AND risk_level IN ('high','critical')", {"d": day, "n": nxt})
        trend.append({"date": f"{day:%Y-%m-%d}", "total": total, "high": high})

    return {
        "todayLogs": today_logs,
        "highRiskLogs": high_risk,
        "openAlerts": open_alerts,
        "onChainLogs": on_chain,
        "byModule": [{"module": k, "count": c} for k, c in _group("module")],
        "byRisk": [{"riskLevel": k, "count": c} for k, c in _group("risk_level")],
        "trend": trend,
    }


_PERIOD_DAYS = {"day": 1, "week": 7, "month": 30}


def report(db: Session, period: str, date: str | None) -> dict:
    if period not in _PERIOD_DAYS:
        raise ParamError("period 只能是 day / week / month")

    anchor = _parse_time(date, "date") or now_cst().replace(tzinfo=None)
    end = anchor.replace(hour=23, minute=59, second=59, microsecond=0)
    start = (end - timedelta(days=_PERIOD_DAYS[period])).replace(hour=0, minute=0, second=0)

    tables = sharding.tables_in_range(db, start, end)
    params = {"start": start, "end": end}

    def _count_actions(prefix_list: list[str]) -> dict[str, int]:
        if not tables:
            return {}
        sql = " UNION ALL ".join(
            f"SELECT action AS a, COUNT(*) AS c FROM {sharding.assert_valid(t)} "
            f"WHERE created_at BETWEEN :start AND :end GROUP BY action"
            for t in tables
        )
        rows = db.execute(text(f"SELECT a, SUM(c) FROM ({sql}) AS m GROUP BY a"), params).all()
        out = {}
        for action, count in rows:
            for prefix in prefix_list:
                if action and action.startswith(prefix):
                    out[action] = out.get(action, 0) + int(count)
        return out

    identity_actions = _count_actions(["did:", "key:"])
    permission_actions = _count_actions(["permission:"])

    from modules.evidence.model import ChainEvidence

    evidence_total = db.execute(
        select(func.count()).select_from(ChainEvidence)
        .where(ChainEvidence.created_at.between(start, end))
    ).scalar_one()
    evidence_by_category = dict(
        db.execute(
            select(ChainEvidence.category, func.count())
            .where(ChainEvidence.created_at.between(start, end))
            .group_by(ChainEvidence.category)
        ).all()
    )

    risk_rows = db.execute(
        select(AuditAlert.rule_code, AuditAlert.risk_level, func.count())
        .where(AuditAlert.created_at.between(start, end))
        .group_by(AuditAlert.rule_code, AuditAlert.risk_level)
    ).all()
    risk_events = [{"ruleCode": c, "level": lv, "count": int(n)} for c, lv, n in risk_rows]

    total_logs = 0
    if tables:
        sql = " UNION ALL ".join(
            f"SELECT COUNT(*) AS c FROM {sharding.assert_valid(t)} "
            f"WHERE created_at BETWEEN :start AND :end" for t in tables
        )
        total_logs = sum(r[0] for r in db.execute(text(f"SELECT * FROM ({sql}) AS m"), params))

    body = {
        "period": period,
        "date": f"{anchor:%Y-%m-%d}",
        "range": {"from": iso(start), "to": iso(end)},
        "totalLogs": total_logs,
        "identityOps": {
            "register": identity_actions.get("did:register", 0),
            "freeze": identity_actions.get("did:status", 0),
            "revoke": identity_actions.get("key:revoke", 0),
            "rotate": identity_actions.get("key:rotate", 0),
        },
        "permissionOps": {
            "applied": permission_actions.get("permission:apply", 0),
            "approved": permission_actions.get("permission:approve", 0),
            "rejected": permission_actions.get("permission:reject", 0),
            "revoked": permission_actions.get("permission:revoke", 0),
        },
        "evidence": {"total": evidence_total, "byCategory": evidence_by_category},
        "riskEvents": risk_events,
    }

    narrative, source = _narrative(body)
    body["narrative"] = narrative
    body["narrativeSource"] = source
    return body


def _narrative(body: dict) -> tuple[str, str]:
    """报告解读文字：优先调 DeepSeek，失败退回规则化模板。

    契约要求 narrativeSource 标记 live | cache | rule，
    离线环境下必须仍然有可读的中文结论，不能是空字符串或报错。
    """
    from modules.algo import client as algo_client

    result = algo_client.call("POST", "/deepseek/analyze", json={
        "scene": "audit",
        "context": body,
        "question": "请用一段话总结本期平台的安全审计情况，指出风险点。",
    }, raise_on_error=False)
    if result and result.get("answer"):
        return result["answer"], result.get("source", "live")

    ident = body["identityOps"]
    perm = body["permissionOps"]
    risks = body["riskEvents"]
    parts = [
        f"本期平台共记录 {body['totalLogs']} 条审计日志，"
        f"上链存证 {body['evidence']['total']} 条。",
        f"身份类操作：签发 {ident['register']} 次、状态变更 {ident['freeze']} 次、"
        f"密钥轮换 {ident['rotate']} 次。",
        f"权限类操作：申请 {perm['applied']} 次、审批通过 {perm['approved']} 次、"
        f"驳回 {perm['rejected']} 次、回收 {perm['revoked']} 次。",
    ]
    if risks:
        detail = "、".join(f"{r['ruleCode']} {r['count']} 次" for r in risks)
        parts.append(f"本期触发风控告警：{detail}，全部已记录并上链，建议关注越权访问类事件。")
    else:
        parts.append("本期未触发风控告警，各项操作均在授权范围内。")
    return "".join(parts), "rule"
