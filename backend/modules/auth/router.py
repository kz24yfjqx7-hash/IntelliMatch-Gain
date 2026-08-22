"""认证与用户管理路由。契约 2.1。

注意本文件里没有任何一行手写的审计代码——审计与风险等级全部由 @audited 声明式产生。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.deps import current_user, get_db, pagination, require_roles
from core.middleware import Principal, audited, current_principal
from core.response import PageQuery, ok, page
from core.security import decode_token, revoke_token
from modules.auth import service
from modules.auth.schema import LoginRequest, UserCreateRequest, UserUpdateRequest

router = APIRouter(tags=["认证与用户"])


@router.post("/auth/login", summary="登录")
@audited(module="auth", action="login", risk="low")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    return ok(service.login(db, body.username, body.password))


@router.post("/auth/logout", summary="登出")
@audited(module="auth", action="logout", risk="low")
def logout(principal: Principal = Depends(current_user)):
    # 把当前 token 的 jti 拉黑到原过期时刻，实现真正的登出而不是前端删 token
    from core.middleware import current_request

    request = current_request.get()
    auth = request.headers.get("authorization", "") if request else ""
    if auth.lower().startswith("bearer "):
        revoke_token(decode_token(auth[7:].strip()))
    return ok({"loggedOut": True})


@router.get("/auth/me", summary="当前用户信息")
def get_me(principal: Principal = Depends(current_user), db: Session = Depends(get_db)):
    return ok(service.me(db, principal))


@router.get("/users", summary="用户列表")
def list_users(
    pg: PageQuery = Depends(pagination),
    keyword: str | None = Query(None, description="按用户名或姓名模糊搜索"),
    role: str | None = Query(None, description="按角色码筛选"),
    db: Session = Depends(get_db),
    _admin: Principal = Depends(require_roles("sys_admin")),
):
    items, total = service.list_users(db, pg.page, pg.size, keyword, role)
    return ok(page(items, total, pg.page, pg.size))


@router.post("/users", summary="新建用户")
@audited(module="auth", action="user:create", risk="medium")
def create_user(
    body: UserCreateRequest,
    db: Session = Depends(get_db),
    _admin: Principal = Depends(require_roles("sys_admin")),
):
    return ok(service.create_user(db, body))


@router.put("/users/{user_id}", summary="修改用户")
@audited(module="auth", action="user:update", risk="medium", resource_type="user",
         resource_id_arg="user_id")
def update_user(
    user_id: int,
    body: UserUpdateRequest,
    db: Session = Depends(get_db),
    _admin: Principal = Depends(require_roles("sys_admin")),
):
    return ok(service.update_user(db, user_id, body))


@router.delete("/users/{user_id}", summary="删除用户")
@audited(module="auth", action="user:delete", risk="high", resource_type="user",
         resource_id_arg="user_id")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: Principal = Depends(require_roles("sys_admin")),
):
    return ok(service.delete_user(db, user_id, admin))
