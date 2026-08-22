"""算法代理路由。契约 2.9 ~ 2.12。

这些接口本身不算任何东西，它们的价值在于**把算法调用纳入统一的安全治理**：
每一次训练、每一条调度指令都经过权限校验、DID 验签、审计埋点和存证上链。
"""
import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.deps import current_user, get_db, pagination
from core.middleware import Principal, audited, require_permission, require_signature
from core.response import PageQuery, ok, page
from modules.algo import analysis, service
from modules.algo.schema import (
    AiAnalyzeRequest,
    DispatchAckRequest,
    DispatchIssueRequest,
    DispatchTaskCreateRequest,
    FlTaskCreateRequest,
    RiskAssessRequest,
)

router = APIRouter(tags=["算法代理"])


# ---------------------------------------------------------------- 联邦学习

@router.post("/fl/tasks", summary="创建训练任务")
@audited(module="algo", action="fl:create", risk="medium", resource_type="algo")
@require_permission("algo", "execute")
def create_fl_task(body: FlTaskCreateRequest, db: Session = Depends(get_db),
                   _p: Principal = Depends(current_user)):
    return ok(service.create_fl_task(db, body))


@router.get("/fl/tasks", summary="任务列表")
@require_permission("model", "read")
def list_fl_tasks(
    pg: PageQuery = Depends(pagination),
    status: str | None = Query(None, pattern="^(created|running|success|failed|cancelled)$"),
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    items, total = service.list_fl_tasks(db, pg.page, pg.size, status)
    return ok(page(items, total, pg.page, pg.size))


@router.get("/fl/models", summary="模型版本列表")
@require_permission("model", "read")
def list_models(pg: PageQuery = Depends(pagination), db: Session = Depends(get_db),
                _p: Principal = Depends(current_user)):
    items, total = service.list_models(db, pg.page, pg.size)
    return ok(page(items, total, pg.page, pg.size))


@router.post("/fl/models/{version}/publish", summary="发布模型")
@audited(module="algo", action="model:publish", risk="high", resource_type="model",
         resource_id_arg="version")
@require_permission("model", "read")
def publish_model(version: str, db: Session = Depends(get_db),
                  _p: Principal = Depends(current_user)):
    return ok(service.publish_model(db, version))


@router.get("/fl/tasks/{task_id}/rounds", summary="每轮指标")
@require_permission("model", "read")
def get_fl_rounds(task_id: str, db: Session = Depends(get_db),
                  _p: Principal = Depends(current_user)):
    return ok(service.get_fl_rounds(db, task_id))


@router.post("/fl/tasks/{task_id}/start", summary="启动训练")
@audited(module="algo", action="fl:train", risk="medium", resource_type="algo",
         resource_id_arg="task_id")
@require_permission("algo", "execute")
async def start_fl_task(task_id: str, db: Session = Depends(get_db),
                        _p: Principal = Depends(current_user)):
    """异步任务：立刻返回，进度由后台协程轮询算法服务后通过 WebSocket 推送。"""
    from core.middleware import current_trace_id

    result = await asyncio.to_thread(service.start_fl_task, db, task_id)
    # 用协程而不是线程池跑轮询——树莓派上 backend 只有 300MB 内存
    asyncio.create_task(service.poll_fl_job(task_id, current_trace_id.get()))
    return ok(result)


@router.post("/fl/tasks/{task_id}/cancel", summary="取消训练")
@audited(module="algo", action="fl:cancel", risk="medium", resource_type="algo",
         resource_id_arg="task_id")
@require_permission("algo", "execute")
def cancel_fl_task(task_id: str, db: Session = Depends(get_db),
                   _p: Principal = Depends(current_user)):
    return ok(service.cancel_fl_task(db, task_id))


@router.get("/fl/tasks/{task_id}", summary="任务详情")
@require_permission("model", "read")
def get_fl_task(task_id: str, db: Session = Depends(get_db),
                _p: Principal = Depends(current_user)):
    """前端画收敛曲线直接用返回里的 rounds 数组。"""
    return ok(service.get_fl_task(db, task_id))


# ---------------------------------------------------------------- 智能调度

@router.post("/dispatch/tasks", summary="创建调度任务")
@audited(module="algo", action="dispatch:create", risk="low", resource_type="dispatch")
@require_permission("dispatch", "read")
def create_dispatch_task(body: DispatchTaskCreateRequest, db: Session = Depends(get_db),
                         _p: Principal = Depends(current_user)):
    return ok(service.create_dispatch_task(db, body))


@router.get("/dispatch/tasks", summary="调度任务列表")
@require_permission("dispatch", "read")
def list_dispatch_tasks(
    pg: PageQuery = Depends(pagination),
    status: str | None = Query(None, pattern="^(created|running|success|failed|cancelled)$"),
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    items, total = service.list_dispatch_tasks(db, pg.page, pg.size, status)
    return ok(page(items, total, pg.page, pg.size))


@router.post("/dispatch/tasks/{task_id}/run", summary="运行 DQN 生成策略")
@audited(module="algo", action="dispatch:run", risk="medium", resource_type="dispatch",
         resource_id_arg="task_id")
@require_permission("algo", "execute")
def run_dispatch(task_id: str, db: Session = Depends(get_db),
                 _p: Principal = Depends(current_user)):
    return ok(service.run_dispatch(db, task_id))


@router.post("/dispatch/tasks/{task_id}/issue", summary="下发指令")
@audited(module="algo", action="dispatch:issue", risk="high", resource_type="dispatch",
         resource_id_arg="task_id")
@require_permission("dispatch", "issue")
@require_signature(message_factory=lambda kwargs: service.build_sign_message(kwargs["task_id"]))
def issue_dispatch(
    task_id: str,
    body: DispatchIssueRequest,
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    """下发需要同时满足两个条件：有 dispatch:issue 权限，且签名校验通过。

    无权限 → 1003，签名无效 → 1004，两种失败都会记 high 风险审计日志并触发风控告警。
    """
    return ok(service.issue_dispatch(db, task_id, body.signature))


@router.post("/dispatch/tasks/{task_id}/ack", summary="边缘节点回执")
@audited(module="algo", action="dispatch:ack", risk="low", resource_type="dispatch",
         resource_id_arg="task_id")
@require_permission("dispatch", "read")
def ack_dispatch(task_id: str, body: DispatchAckRequest, db: Session = Depends(get_db),
                 _p: Principal = Depends(current_user)):
    return ok(service.ack_dispatch(db, task_id, body.nodeId, body.accepted, body.detail))


@router.get("/dispatch/tasks/{task_id}", summary="调度任务详情")
@require_permission("dispatch", "read")
def get_dispatch_task(task_id: str, db: Session = Depends(get_db),
                      _p: Principal = Depends(current_user)):
    return ok(service.get_dispatch_task(db, task_id))


# ---------------------------------------------------------------- AI 分析

@router.post("/ai/analyze", summary="智能分析 / 问答")
@audited(module="algo", action="ai:analyze", risk="low", resource_type="algo")
@require_permission("asset", "read")
def ai_analyze(body: AiAnalyzeRequest, db: Session = Depends(get_db),
               _p: Principal = Depends(current_user)):
    """契约 2.11：**永远返回 200**。真实 API 超时或失败自动回落到缓存/规则文本。

    注意这个接口对所有角色只读，任何角色都不能通过它触发调度下发。
    """
    return ok(analysis.analyze(db, body.scene, body.context, body.question))


@router.get("/ai/history", summary="分析历史")
@require_permission("asset", "read")
def ai_history(
    pg: PageQuery = Depends(pagination),
    scene: str | None = Query(None, pattern="^(dispatch|risk|data|qa|audit)$"),
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    items, total = analysis.analysis_history(db, pg.page, pg.size, scene)
    return ok(page(items, total, pg.page, pg.size))


# ---------------------------------------------------------------- 风险评估

@router.post("/risk/assess", summary="动态隐私风险评估")
@audited(module="algo", action="risk:assess", risk="low", resource_type="algo")
@require_permission("asset", "read")
def risk_assess(body: RiskAssessRequest, db: Session = Depends(get_db),
                _p: Principal = Depends(current_user)):
    return ok(analysis.assess(db, body.nodeId, body.features))


@router.get("/risk/history", summary="历史评分")
@require_permission("asset", "read")
def risk_history(
    pg: PageQuery = Depends(pagination),
    nodeId: str | None = Query(None),
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    items, total = analysis.risk_history(db, pg.page, pg.size, nodeId)
    return ok(page(items, total, pg.page, pg.size))
