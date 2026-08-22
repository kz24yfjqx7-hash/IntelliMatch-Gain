"""横切链路：TraceID → JWT 认证 → DID 状态校验 → 权限中心校验 → 业务 → 审计 → 存证上链。

这是四大安全模块的黏合剂，也是需求文档「改造分散鉴权逻辑，统一收敛至权限中心」
「全链路审计埋点」两条硬性要求的落地点。

设计原则：**业务代码里不许出现手写的 add_audit_log() 调用。**
审计与存证一律通过装饰器声明，例如：

    @router.post("/dispatch/tasks/{id}/issue")
    @audited(module="algo", action="dispatch:issue", risk="high")
    @require_permission("dispatch", "issue")
    @require_signature()
    def issue_dispatch(...): ...

装饰器之间通过 contextvar 传递当前请求的身份与 traceId，
所以被装饰函数的签名不受影响，FastAPI 的依赖注入照常工作。
"""
import asyncio
import functools
import inspect
import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.exceptions import (
    BizError,
    DidInvalidError,
    ErrorCode,
    NoPermissionError,
    UnauthorizedError,
)
from core.security import decode_token, is_revoked, new_trace_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 请求上下文

current_trace_id: ContextVar[str] = ContextVar("current_trace_id", default="tr-00000000-00000000")
current_principal: ContextVar["Principal | None"] = ContextVar("current_principal", default=None)
current_request: ContextVar[Request | None] = ContextVar("current_request", default=None)


@dataclass
class Principal:
    """当前请求的主体。由认证中间件填充，全链路共用。"""

    user_id: int
    username: str
    roles: list[str]
    did: str | None = None
    real_name: str | None = None
    org_name: str | None = None
    ip: str = ""
    jti: str | None = None
    # 扁平权限串集合，格式 <resourceType>:<action>，如 dispatch:issue
    permissions: set[str] = field(default_factory=set)

    def has(self, resource_type: str, action: str) -> bool:
        return f"{resource_type}:{action}" in self.permissions

    @property
    def is_admin(self) -> bool:
        return "sys_admin" in self.roles


def get_principal() -> Principal:
    """取当前登录主体，未登录抛 1002。"""
    p = current_principal.get()
    if p is None:
        raise UnauthorizedError()
    return p


def adopt_trace(trace_id: str | None) -> str:
    """让当前请求沿用某个业务对象**发起时**的 traceId。

    契约 1.2：「该请求产生的所有审计日志、存证记录共用同一 traceId」。
    联邦学习（创建 → 启动 → 每轮上链 → 完成）与智能调度（创建 → run → issue → ack）
    是跨多个 HTTP 请求的长流程，如果每个请求各自生成新 traceId，
    `/audit/trace/{traceId}` 就只能查到孤零零的一步（B-015）。
    因此这些接口在拿到任务后立刻把上下文里的 traceId 换成任务的 traceId，
    后续的 @audited 审计日志、write_evidence 存证、WebSocket 推送就自动串到同一条链上。

    调用点必须在被 @audited 包裹的函数体内（同一个 contextvar 上下文），
    这样装饰器收尾写审计日志时取到的才是任务的 traceId。

    **但客户端显式带了 `X-Trace-Id` 时不继承**：契约 1.2 规定「客户端可通过请求头
    X-Trace-Id 指定，backend 沿用」，覆盖它会让调用方拿不回自己指定的 traceId，
    也查不到本次请求的日志。此时链路由调用方负责——前端在同一条业务流程
    （创建 → run → issue → ack）里复用任务的 traceId 即可串成完整链。
    """
    request = current_request.get()
    if request is not None and getattr(request.state, "trace_from_client", False):
        return current_trace_id.get()
    if trace_id:
        current_trace_id.set(trace_id)
        # 同步写回 request.state：同步接口跑在线程池里，contextvar 改动传不回事件循环，
        # 而全局异常处理器（1001/1006 那些分支）是在事件循环里取 traceId 的。
        # 不写回的话，被拒绝的响应会带着一个查不到任何日志的 traceId。
        request = current_request.get()
        if request is not None:
            try:
                request.state.trace_id = trace_id
            except Exception as exc:  # noqa: BLE001
                logger.debug("回写 request.state.trace_id 失败：%s", exc)
    return current_trace_id.get()


def audit_step(*, module: str, action: str, risk: str = "low",
               resource_type: str | None = None, resource_id: str | None = None,
               result: str = "success", detail: str | None = None,
               trace_id: str | None = None, actor_did: str | None = None,
               evidence_id: str | None = None, chain: bool | None = None) -> None:
    """给「不在 HTTP 请求里」的流程步骤补一条审计埋点。

    @audited 只能覆盖请求-响应式的调用；联邦学习每轮结果是后台协程轮询算法服务后落库的，
    没有请求可以挂装饰器，但它同样属于「任务级全流程」的一步，必须留痕，
    否则 /audit/trace 的时间轴上会缺掉整个训练过程。

    仍然遵守「业务代码不许直接调 add_audit_log」的原则——业务侧只声明发生了什么，
    落库细节由本模块统一处理。
    """
    payload = {
        "traceId": trace_id or current_trace_id.get(),
        "actorDid": actor_did,
        "actorName": None,
        "module": module,
        "action": action,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "result": result,
        "riskLevel": _escalate(risk, result, module),
        "detail": detail,
        "ip": "",
        "costMs": 0,
        # 关联到业务侧已经写好的那条存证（例如某一轮梯度哈希）。
        # 审计模块暂时只在自己上链时才填 evidence_id，这里先带上，
        # 待 modules/audit 支持透传后 /audit/trace 的 steps[].evidenceId 即可非空。
        "evidenceId": evidence_id,
    }
    try:
        from modules.audit.service import write_audit_log

        write_audit_log(payload, to_chain=chain)
    except ImportError:
        logger.info("[审计·暂存] %s", json.dumps(payload, ensure_ascii=False))


def get_client_ip(request: Request | None = None) -> str:
    request = request or current_request.get()
    if request is None:
        return ""
    # 经过 nginx 反代，真实 IP 在 X-Forwarded-For 里
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


# ---------------------------------------------------------------- 中间件

# 无需登录即可访问的路径前缀
PUBLIC_PATHS = (
    "/health",
    "/api/v1/auth/login",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/ws",  # WebSocket 走 query 参数鉴权，不走本中间件
)


class TraceMiddleware(BaseHTTPMiddleware):
    """第一环：生成 / 沿用 traceId，并把它写进响应头，方便前端和日志排查。"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_trace = request.headers.get("x-trace-id")
        trace_id = client_trace or new_trace_id()
        token_trace = current_trace_id.set(trace_id)
        token_req = current_request.set(request)
        # B-014：未捕获异常会一路冒泡到 Starlette 最外层的 ServerErrorMiddleware，
        # 那里已经在本中间件的 finally 之外，contextvar 一旦 reset 就只剩默认值，
        # 于是 500 响应的 traceId 退化成 tr-00000000-00000000，全流程追踪彻底断掉。
        # 把 traceId 同时挂到 request.state 上，异常分支再不 reset，兜住这条路径。
        request.state.trace_id = trace_id
        # 客户端显式指定的 traceId 必须原样沿用并回显（契约 1.2），adopt_trace() 不得覆盖它
        request.state.trace_from_client = bool(client_trace)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException:
            cost = (time.perf_counter() - started) * 1000
            current_request.reset(token_req)
            logger.info("%s %s trace=%s cost=%.1fms（异常）",
                        request.method, request.url.path, trace_id, cost)
            # 故意不 reset current_trace_id：本请求的上下文是独立副本，
            # 留给外层异常处理器取真实 traceId，请求结束即随上下文一起销毁。
            raise
        cost = (time.perf_counter() - started) * 1000
        current_request.reset(token_req)
        logger.info(
            "%s %s trace=%s cost=%.1fms", request.method, request.url.path, trace_id, cost
        )
        current_trace_id.reset(token_trace)
        # 走过 adopt_trace 的接口（FL / 调度全流程）会把 traceId 换成任务的，响应头跟着一起换
        response.headers["X-Trace-Id"] = getattr(request.state, "trace_id", trace_id)
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """第二、三环：解析 JWT 并校验 DID 状态，把 Principal 放进上下文。

    注意这里**不做拒绝**（公开接口要放行），拒绝交给 deps.py 的依赖与
    @require_permission 装饰器，这样权限判定统一收敛在权限中心一处。
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        token_principal = current_principal.set(None)
        try:
            if not self._is_public(request.url.path):
                auth = request.headers.get("authorization", "")
                if auth.lower().startswith("bearer "):
                    try:
                        principal = await asyncio.to_thread(
                            self._build_principal, auth[7:].strip(), get_client_ip(request)
                        )
                        current_principal.set(principal)
                    except BizError as exc:
                        return _error_response(exc)
            return await call_next(request)
        finally:
            current_principal.reset(token_principal)

    @staticmethod
    def _is_public(path: str) -> bool:
        return any(path.startswith(p) for p in PUBLIC_PATHS)

    @staticmethod
    def _build_principal(token: str, ip: str) -> Principal:
        payload = decode_token(token)
        if is_revoked(payload):
            raise UnauthorizedError("登录已失效，请重新登录")

        principal = Principal(
            user_id=int(payload.get("sub", 0)),
            username=payload.get("username", ""),
            roles=payload.get("roles", []) or [],
            did=payload.get("did"),
            ip=ip,
            jti=payload.get("jti"),
        )
        _load_permissions(principal)
        _hydrate_profile(principal)
        _check_did_status(principal)
        return principal


_PROFILE_CACHE_PREFIX = "user:profile:"


def _hydrate_profile(principal: Principal) -> None:
    """补上真实姓名与所属机构，顺带确认账号还没被停用。

    JWT 里只有 username，但审计日志和告警要显示「张三」而不是「zhangsan」，
    契约 2.7 的 actorName 字段也是这么定义的。走 Redis 缓存，一个用户 5 分钟一次查询。

    账号状态也在这一次查询里一起取回来：Token 有效期 8 小时，
    如果只在登录时查一次状态，管理员停用一个账号后对方还能再用大半天。
    停用 / 删除用户时会主动清缓存（见 modules/auth/service.py），所以是即时生效的。
    """
    import json as _json

    from core import redis_client

    cache_key = f"{_PROFILE_CACHE_PREFIX}{principal.user_id}"
    cached = redis_client.safe_get(cache_key)
    if cached:
        try:
            profile = _json.loads(cached)
            principal.real_name = profile.get("realName")
            principal.org_name = profile.get("orgName")
            _assert_account_active(profile.get("status"))
            return
        except _json.JSONDecodeError:
            pass

    try:
        from sqlalchemy import select

        from core.database import SessionLocal
        from modules.auth.model import SysUser

        with SessionLocal() as db:
            row = db.execute(
                select(SysUser.real_name, SysUser.org_name, SysUser.status)
                .where(SysUser.id == principal.user_id)
            ).first()
    except Exception as exc:  # noqa: BLE001  查不到就退回用登录名，不影响请求
        logger.debug("加载用户资料失败 uid=%s：%s", principal.user_id, exc)
        return

    if row is None:
        # 账号已被删除，但 Token 还没到期
        raise UnauthorizedError("账号不存在或已被删除，请重新登录")

    principal.real_name, principal.org_name = row[0], row[1]
    redis_client.safe_set(
        cache_key,
        _json.dumps({"realName": row[0], "orgName": row[1], "status": row[2]},
                    ensure_ascii=False),
        ex=300,
    )
    _assert_account_active(row[2])


def _assert_account_active(status: str | None) -> None:
    if status is not None and status != "active":
        raise UnauthorizedError("账号已被停用，请联系系统管理员")


def invalidate_profile_cache(user_id: int) -> None:
    """停用 / 改动 / 删除用户后调用，让新的账号状态立刻生效。"""
    from core import redis_client

    redis_client.safe_delete(f"{_PROFILE_CACHE_PREFIX}{user_id}")


def _load_permissions(principal: Principal) -> None:
    """从权限中心加载该主体的扁平权限集合。权限中心模块在第二步实现。"""
    try:
        from modules.permission.service import load_permissions_for_roles

        principal.permissions = load_permissions_for_roles(principal.roles)
    except ImportError:
        logger.debug("权限中心尚未就绪，跳过权限加载")


def _check_did_status(principal: Principal) -> None:
    """DID 状态校验：已冻结 / 已注销的身份一律拒绝接入（code 1004）。

    命中时同时触发 R02_ABNORMAL_DID 风控规则。DID 模块在第三步实现。
    """
    if not principal.did:
        return
    try:
        from modules.did.service import get_did_status
    except ImportError:
        return

    status = get_did_status(principal.did)
    if status is None:
        return
    if status != "active":
        _fire_rule("R02_ABNORMAL_DID", principal, f"DID 状态为 {status}，拒绝接入")
        raise DidInvalidError(f"身份 {principal.did} 状态为 {status}，已被拒绝接入")


def _fire_rule(rule_code: str, principal: Principal | None, message: str) -> None:
    """触发风控规则。审计模块在第五步实现，未就绪时静默跳过。"""
    try:
        from modules.audit.rules import fire

        fire(rule_code, principal=principal, message=message)
    except ImportError:
        logger.debug("风控引擎尚未就绪，跳过规则 %s", rule_code)


def _error_response(exc: BizError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
            "traceId": current_trace_id.get(),
        },
    )


# ---------------------------------------------------------------- 声明式装饰器

def require_permission(resource_type: str, action: str, *, resource_id_arg: str | None = None):
    """第四环：权限中心校验。

    统一收敛点——所有需要鉴权的接口都用它，业务函数内部不许再写 if role == ...。
    拒绝时抛 1003，并累计 R01_UNAUTHORIZED 风控计数。
    """

    def decorator(func):
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            _check_permission(resource_type, action, kwargs, resource_id_arg)
            return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            _check_permission(resource_type, action, kwargs, resource_id_arg)
            return await func(*args, **kwargs)

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


def _check_permission(resource_type: str, action: str, kwargs: dict, resource_id_arg: str | None):
    principal = get_principal()
    resource_id = str(kwargs.get(resource_id_arg)) if resource_id_arg else None

    allowed, reason = _delegate_permission_check(principal, resource_type, action, resource_id)
    if not allowed:
        # R01 的计数统一放在 main.py 的全局异常处理器里做，
        # 这样 @require_permission 和 require_roles 两条拒绝路径都能被覆盖，
        # 也不会出现同一次拒绝被记两次的情况。
        raise NoPermissionError(reason)


def _delegate_permission_check(principal, resource_type, action, resource_id):
    try:
        from modules.permission.service import check_permission
    except ImportError:
        # 权限中心尚未实现时退化为「按 JWT 里的角色权限集合判断」
        if principal.has(resource_type, action):
            return True, ""
        return False, f"角色 {'/'.join(principal.roles)} 无 {resource_type}:{action} 权限"
    return check_permission(principal, resource_type, action, resource_id)


def require_signature(*, did_arg: str = "did", signature_arg: str = "signature",
                      message_arg: str = "nonce", message_factory: Callable | None = None):
    """DID 签名校验装饰器：调度下发、设备上线等高危操作必须验签，失败抛 1004。

    - message_arg：待签原文直接来自请求体的某个字段（设备上线用 nonce）
    - message_factory：待签原文需要由服务端计算（调度下发要签的是策略摘要，
      不能让客户端自己说签了什么）。签名 (kwargs) -> str。
    """

    def decorator(func):
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            _verify_signature(kwargs, did_arg, signature_arg, message_arg, message_factory)
            return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            _verify_signature(kwargs, did_arg, signature_arg, message_arg, message_factory)
            return await func(*args, **kwargs)

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


def _verify_signature(kwargs: dict, did_arg: str, signature_arg: str, message_arg: str,
                      message_factory: Callable | None = None) -> None:
    """取出 did / signature / 待签原文，交给 DID 模块验签。"""
    principal = current_principal.get()
    body = _find_body(kwargs)
    did = _extract(body, did_arg) or (principal.did if principal else None)
    signature = _extract(body, signature_arg)
    message = message_factory(kwargs) if message_factory else _extract(body, message_arg)

    try:
        from modules.did.service import sign_with_custody, verify_signature
    except ImportError:
        logger.warning("DID 模块尚未就绪，跳过验签")
        return

    if not signature:
        # 前端没保存私钥时，允许用平台托管的私钥代签。
        # 注意这里**只是补出签名，验签环节一个都不少**——
        # 没有托管密钥的主体照样过不去，越权演示的效果不受影响。
        signature = sign_with_custody(did, message or "") if did else None
        if not signature:
            raise DidInvalidError("缺少签名，且该身份没有可用的托管密钥")
        logger.info("使用托管私钥为 %s 补签", did)
        # B-004：代签出来的签名必须回写给业务层，否则 algo_dispatch_task.signature 恒为 NULL，
        # 事后既无法追溯是谁签的，边端也拿不到签名字节做复核验签。
        _write_back(body, signature_arg, signature)

    if not verify_signature(did, message or "", signature):
        raise DidInvalidError("签名校验失败")


def _find_body(kwargs: dict) -> Any:
    for value in kwargs.values():
        if hasattr(value, "model_dump"):
            return value
    return kwargs


def _extract(body: Any, name: str):
    if body is None:
        return None
    if isinstance(body, dict):
        return body.get(name)
    return getattr(body, name, None)


def _write_back(body: Any, name: str, value: str) -> None:
    """把托管代签补出来的签名写回请求体，业务层照常从 body.signature 取。"""
    if body is None:
        return
    try:
        if isinstance(body, dict):
            body[name] = value
        else:
            setattr(body, name, value)
    except Exception as exc:  # noqa: BLE001  请求体是 frozen 模型时降级，不影响验签
        logger.debug("回写托管签名失败：%s", exc)


def audited(*, module: str, action: str, risk: str = "low",
            resource_type: str | None = None, resource_id_arg: str | None = None,
            chain: bool | None = None):
    """第六、七环：自动写审计日志 + 自动推送存证上链。

    - risk 取值 low | medium | high | critical
    - chain 为 None 时，risk >= high 自动上链；显式传 True/False 可覆盖
    - 业务函数抛异常时记 failed / denied，不吞异常
    """

    def decorator(func):
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except BizError as exc:
                _write_audit(module, action, risk, resource_type, resource_id_arg, kwargs,
                             result="denied" if exc.code in (ErrorCode.NO_PERMISSION,
                                                             ErrorCode.DID_INVALID) else "failed",
                             detail=exc.message, chain=chain, cost_ms=_ms(started))
                raise
            except Exception as exc:  # noqa: BLE001
                _write_audit(module, action, risk, resource_type, resource_id_arg, kwargs,
                             result="failed", detail=str(exc), chain=chain, cost_ms=_ms(started))
                raise
            _write_audit(module, action, risk, resource_type, resource_id_arg, kwargs,
                         result="success", detail=None, chain=chain, cost_ms=_ms(started))
            return result

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
            except BizError as exc:
                _write_audit(module, action, risk, resource_type, resource_id_arg, kwargs,
                             result="denied" if exc.code in (ErrorCode.NO_PERMISSION,
                                                             ErrorCode.DID_INVALID) else "failed",
                             detail=exc.message, chain=chain, cost_ms=_ms(started))
                raise
            except Exception as exc:  # noqa: BLE001
                _write_audit(module, action, risk, resource_type, resource_id_arg, kwargs,
                             result="failed", detail=str(exc), chain=chain, cost_ms=_ms(started))
                raise
            _write_audit(module, action, risk, resource_type, resource_id_arg, kwargs,
                         result="success", detail=None, chain=chain, cost_ms=_ms(started))
            return result

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


_RISK_ORDER = ["low", "medium", "high", "critical"]


def _escalate(declared: str, result: str, module: str) -> str:
    """按执行结果自动抬高风险等级，声明的等级只是下限。

    - 被拒绝（越权 / 身份校验失败）一律至少 high
    - 认证失败至少 high：连续失败的登录尝试是最典型的攻击前兆
    - 其它失败至少 medium
    """
    floor = declared
    if result == "denied":
        floor = "high"
    elif result == "failed":
        floor = "high" if module == "auth" else "medium"
    return max(declared, floor, key=lambda r: _RISK_ORDER.index(r))


def _write_audit(module, action, risk, resource_type, resource_id_arg, kwargs,
                 *, result, detail, chain, cost_ms):
    """写审计日志。审计模块在第五步实现，未就绪时降级为打印日志。"""
    principal = current_principal.get()
    resource_id = str(kwargs.get(resource_id_arg)) if resource_id_arg and kwargs.get(resource_id_arg) else None
    payload = {
        "traceId": current_trace_id.get(),
        "actorDid": principal.did if principal else None,
        "actorName": principal.real_name or principal.username if principal else None,
        "module": module,
        "action": action,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "result": result,
        "riskLevel": _escalate(risk, result, module),
        "detail": detail,
        "ip": get_client_ip(),
        "costMs": cost_ms,
    }
    try:
        from modules.audit.service import write_audit_log

        write_audit_log(payload, to_chain=chain)
    except ImportError:
        logger.info("[审计·暂存] %s", json.dumps(payload, ensure_ascii=False))
