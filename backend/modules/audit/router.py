"""安全审计路由。契约 2.7。

审计相关接口限系统管理员与监管方——审计的意义就在于操作者本人不能自己改自己的账。
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.deps import get_db, pagination, require_roles
from core.middleware import Principal, audited
from core.response import PageQuery, ok, page
from modules.audit import rules as risk_rules
from modules.audit import service

router = APIRouter(tags=["安全审计"])

_AUDITOR = require_roles("sys_admin", "regulator")


@router.get("/audit/logs", summary="日志检索")
def list_logs(
    pg: PageQuery = Depends(pagination),
    traceId: str | None = Query(None),
    actorDid: str | None = Query(None),
    action: str | None = Query(None),
    riskLevel: str | None = Query(None, pattern="^(low|medium|high|critical)$"),
    module: str | None = Query(None),
    result: str | None = Query(None, pattern="^(success|failed|denied)$"),
    keyword: str | None = Query(None),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
    _p: Principal = Depends(_AUDITOR),
):
    items, total = service.query_logs(
        db, pg.page, pg.size, trace_id=traceId, actor_did=actorDid, action=action,
        risk_level=riskLevel, module=module, result=result, keyword=keyword,
        from_=from_, to=to,
    )
    return ok(page(items, total, pg.page, pg.size))


@router.get("/audit/trace/{trace_id}", summary="任务级全链路追踪")
def trace(trace_id: str, db: Session = Depends(get_db), _p: Principal = Depends(_AUDITOR)):
    """答辩演示第 9 步：用一个 traceId 查出从登录到下发的完整链路。"""
    return ok(service.trace_detail(db, trace_id))


@router.get("/audit/stats", summary="审计看板统计")
def stats(db: Session = Depends(get_db), _p: Principal = Depends(_AUDITOR)):
    return ok(service.stats(db))


@router.get("/audit/report", summary="审计报告")
def report(
    period: str = Query("day", pattern="^(day|week|month)$"),
    date: str | None = Query(None, description="YYYY-MM-DD，默认今天"),
    db: Session = Depends(get_db),
    _p: Principal = Depends(_AUDITOR),
):
    return ok(service.report(db, period, date))


@router.get("/audit/alerts", summary="告警列表")
def list_alerts(
    pg: PageQuery = Depends(pagination),
    status: str | None = Query(None, pattern="^(open|acked)$"),
    ruleCode: str | None = Query(None),
    db: Session = Depends(get_db),
    _p: Principal = Depends(_AUDITOR),
):
    items, total = service.list_alerts(db, pg.page, pg.size, status=status, rule_code=ruleCode)
    return ok(page(items, total, pg.page, pg.size))


@router.post("/audit/alerts/{alert_id}/ack", summary="确认告警")
@audited(module="audit", action="alert:ack", risk="low", resource_type="evidence",
         resource_id_arg="alert_id")
def ack_alert(alert_id: int, db: Session = Depends(get_db),
              principal: Principal = Depends(_AUDITOR)):
    return ok(service.ack_alert(db, alert_id, principal))


@router.get("/audit/rules", summary="风险规则清单")
def list_rules(_p: Principal = Depends(_AUDITOR)):
    """五类规则的元数据，供前端按规则码展示名称和触发条件。"""
    return ok({"items": risk_rules.list_rules(), "total": len(risk_rules.RULES)})


@router.get("/audit/logs/export", summary="导出 CSV")
@audited(module="audit", action="audit:export", risk="medium")
def export_logs(
    riskLevel: str | None = Query(None, pattern="^(low|medium|high|critical)$"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(_AUDITOR),
):
    """契约 2.7：本接口返回文件流，**不套统一包装**。"""
    content, count = service.export_csv(db, risk_level=riskLevel, from_=from_, to=to)

    # 导出属于敏感操作，走 R04 批量数据导出规则
    risk_rules.fire_bulk_export(principal, count)

    from core.response import now_cst

    filename = f"audit-logs-{now_cst():%Y%m%d-%H%M%S}.csv"
    return Response(
        # 带 BOM，否则 Excel 打开中文会乱码
        content="﻿" + content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Rows": str(count),
        },
    )
