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


def require_roles(*roles: str):
    """粗粒度角色限制，用于「仅 sys_admin 可访问」这类接口。"""

    def dependency(principal: Principal = Depends(current_user)) -> Principal:
        if not set(roles) & set(principal.roles):
            raise NoPermissionError(
                f"该操作仅限 {'/'.join(roles)}，当前角色 {'/'.join(principal.roles) or '无'}"
            )
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
