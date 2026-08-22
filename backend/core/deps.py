"""FastAPI 依赖注入：当前用户、角色校验、分页参数、数据库会话。

与 core/middleware.py 的分工：
- 中间件负责「解析身份」，不做拒绝（公开接口要放行）
- 本文件的依赖负责「必须登录」这类粗粒度拒绝
- 细粒度的资源级鉴权一律用 @require_permission 装饰器，收敛在权限中心
"""
from fastapi import Depends, Query
from sqlalchemy.orm import Session

from core.database import get_db  # noqa: F401  供路由 import
from core.exceptions import NoPermissionError
from core.middleware import Principal, current_principal
from core.exceptions import UnauthorizedError
from core.response import PageQuery


def current_user() -> Principal:
    """必须登录。未登录抛 1002。"""
    principal = current_principal.get()
    if principal is None:
        raise UnauthorizedError()
    return principal


CurrentUser = Depends(current_user)


# 路径首段 → 审计日志的 module 名。审计埋点的 module 口径要和 @audited 声明的一致，
# 否则同一个模块的日志会散落在两个 module 名下，/audit/logs?module= 就筛不全。
_PATH_MODULE = {
    "users": "auth", "roles": "auth", "auth": "auth",
    "did": "did", "keys": "key",
    "permissions": "permission", "assets": "asset",
    "evidence": "evidence", "audit": "audit", "nodes": "node",
    "fl": "algo", "dispatch": "algo", "models": "algo", "risk": "algo",
    "privacy": "algo", "ai": "algo",
}
_METHOD_VERB = {"GET": "read", "POST": "write", "PUT": "update",
                "PATCH": "update", "DELETE": "delete"}


def _audit_role_denial(reason: str) -> None:
    """require_roles 拒绝时补写审计日志。

    用权限串保护的接口由 @require_permission → BizError → @audited 写 denied 日志，
    而 require_roles 是 FastAPI 依赖，在被装饰的业务函数**之前**执行，
    @audited 根本没机会跑，于是这条拒绝只进了 R01 风控计数，没有留痕
    （测试文档-接口与安全 §4 B-001 / 用例 API-AUD-01）。需求(四)1 要求
    「所有访问与操作留痕」，所以在这里补齐，字段口径与 @audited 的 denied 分支完全一致：
    result=denied、riskLevel 经 _escalate 抬到 high、高危自动上链。

    依赖先于业务函数执行 ⇒ 同一次拒绝只会被记一次，不会和 @audited 重复。
    写审计失败绝不能盖掉本该返回的 1003，所以整段吞异常。
    """
    try:
        from core.middleware import _write_audit, current_request

        request = current_request.get()
        path = request.url.path if request is not None else ""
        method = request.method if request is not None else ""
        segment = path.split("/api/v1/", 1)[-1].split("/")[0] if "/api/v1/" in path else ""
        module = _PATH_MODULE.get(segment, segment or "unknown")
        action = f"{module}:{_METHOD_VERB.get(method, 'access')}"
        _write_audit(module, action, "high", None, None, {},
                     result="denied", detail=reason, chain=None, cost_ms=0)
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning("补写 require_roles 拒绝审计失败：%s", exc)


def require_roles(*roles: str):
    """粗粒度角色限制，用于「仅 sys_admin 可访问」这类接口。"""

    def dependency(principal: Principal = Depends(current_user)) -> Principal:
        if not set(roles) & set(principal.roles):
            reason = (f"该操作仅限 {'/'.join(roles)}，"
                      f"当前角色 {'/'.join(principal.roles) or '无'}")
            _audit_role_denial(reason)
            raise NoPermissionError(reason)
        return principal

    return dependency


def pagination(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    size: int = Query(20, ge=1, le=200, description="每页条数，最大 200"),
) -> PageQuery:
    """契约 1.4。上限 200 是硬约束：树莓派上禁止一次性拉全表。"""
    return PageQuery(page=page, size=size)


DbSession = Depends(get_db)

__all__ = [
    "get_db",
    "Session",
    "current_user",
    "CurrentUser",
    "require_roles",
    "pagination",
    "DbSession",
    "Principal",
]
