"""认证与用户管理的业务逻辑。"""
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from core import redis_client
from core.exceptions import (
    ConflictError,
    DidInvalidError,
    NotFoundError,
    ParamError,
    RateLimitedError,
    UnauthorizedError,
)
from core.middleware import (
    Principal,
    current_principal,
    get_client_ip,
    invalidate_profile_cache,
)
from core.response import iso, now_cst
from core.security import create_token, hash_password, verify_password
from modules.auth.model import SysRole, SysUser, SysUserRole
from modules.permission.service import invalidate_permission_cache, load_permissions_for_roles

logger = logging.getLogger(__name__)


def get_roles_of(db: Session, user_id: int) -> list[str]:
    return list(
        db.execute(select(SysUserRole.role_code).where(SysUserRole.user_id == user_id))
        .scalars()
        .all()
    )


# 登录失败计数：同一账号 5 分钟内错 5 次就锁到窗口结束
_LOGIN_FAIL_PREFIX = "auth:login:fail:"
_LOGIN_FAIL_WINDOW = 300
_LOGIN_FAIL_LIMIT = 5


def _login_fail_key(username: str) -> str:
    return f"{_LOGIN_FAIL_PREFIX}{username}"


def _assert_not_locked(username: str) -> None:
    """按账号维度限流，不按 IP。

    按 IP 限会把演示现场整台机器一起锁掉（评委和我们走的是同一个出口），
    按账号限既挡得住撞库，又不会误伤。IP 维度的限流交给 nginx。
    Redis 挂掉时 safe_get 返回 None，这里自动退化为不限流——
    宁可放行也不能因为缓存故障把所有人挡在门外。
    """
    cached = redis_client.safe_get(_login_fail_key(username))
    if cached is None:
        return
    try:
        hits = int(cached)
    except (TypeError, ValueError):
        return
    if hits >= _LOGIN_FAIL_LIMIT:
        raise RateLimitedError(
            f"密码连续错误 {hits} 次，账号已临时锁定，请 {_LOGIN_FAIL_WINDOW // 60} 分钟后再试"
        )


def _record_login_fail(username: str) -> int:
    """INCR 本身就把计数存进了那个键，所以读的时候直接 safe_get 就行。"""
    return redis_client.safe_incr_window(_login_fail_key(username), _LOGIN_FAIL_WINDOW)


def login(db: Session, username: str, password: str) -> dict:
    """登录。失败一律返回 1002，且不区分「用户不存在」和「密码错误」，避免账号枚举。"""
    _assert_not_locked(username)

    user = db.execute(select(SysUser).where(SysUser.username == username)).scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        # 把尝试的用户名放进上下文，让 @audited 能记下「谁在试」
        current_principal.set(Principal(user_id=0, username=username, roles=[], ip=get_client_ip()))
        hits = _record_login_fail(username)
        if hits >= _LOGIN_FAIL_LIMIT:
            logger.warning("账号 %s 连续登录失败 %s 次，已临时锁定", username, hits)
        raise UnauthorizedError("用户名或密码错误")

    if user.status != "active":
        current_principal.set(Principal(user_id=user.id, username=username, roles=[], did=user.did,
                                        real_name=user.real_name, ip=get_client_ip()))
        raise UnauthorizedError("账号已被停用，请联系系统管理员")

    # 身份可信是第一道关：DID 被冻结或注销的用户不允许登录
    _assert_did_active(user.did)

    roles = get_roles_of(db, user.id)
    token, expires_in = create_token(
        user_id=user.id, username=user.username, roles=roles, did=user.did
    )

    user.last_login_at = now_cst().replace(tzinfo=None)
    db.commit()
    redis_client.safe_delete(_login_fail_key(username))

    # 让本次请求后续的审计埋点知道「登录成功的是谁」
    principal = Principal(
        user_id=user.id, username=user.username, roles=roles, did=user.did,
        real_name=user.real_name, org_name=user.org_name, ip=get_client_ip(),
        permissions=load_permissions_for_roles(roles),
    )
    current_principal.set(principal)

    return {
        "token": token,
        "expiresIn": expires_in,
        "user": {
            "id": user.id,
            "username": user.username,
            "realName": user.real_name,
            "roles": roles,
            "did": user.did,
            "orgName": user.org_name,
        },
    }


def _assert_did_active(did: str | None) -> None:
    if not did:
        return
    try:
        from modules.did.service import get_did_status
    except ImportError:
        return
    status = get_did_status(did)
    if status and status != "active":
        raise DidInvalidError(f"身份 {did} 状态为 {status}，禁止登录")


def me(db: Session, principal: Principal) -> dict:
    user = db.get(SysUser, principal.user_id)
    if user is None:
        raise UnauthorizedError("用户不存在或已被删除")
    roles = get_roles_of(db, user.id)
    return {
        "id": user.id,
        "username": user.username,
        "realName": user.real_name,
        "orgName": user.org_name,
        "roles": roles,
        "did": user.did,
        # 扁平权限串数组，前端按钮级权限控制直接用它判断
        "permissions": sorted(load_permissions_for_roles(roles)),
        "lastLoginAt": iso(user.last_login_at),
    }


def _to_item(db: Session, user: SysUser) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "realName": user.real_name,
        "orgName": user.org_name,
        "roles": get_roles_of(db, user.id),
        "did": user.did,
        "phone": user.phone,
        "email": user.email,
        "status": user.status,
        "lastLoginAt": iso(user.last_login_at),
        "createdAt": iso(user.created_at),
    }


def list_users(db: Session, page: int, size: int, keyword: str | None = None,
               role: str | None = None) -> tuple[list[dict], int]:
    stmt = select(SysUser)
    count_stmt = select(func.count()).select_from(SysUser)

    if keyword:
        like = f"%{keyword}%"
        cond = SysUser.username.like(like) | SysUser.real_name.like(like)
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if role:
        sub = select(SysUserRole.user_id).where(SysUserRole.role_code == role)
        stmt = stmt.where(SysUser.id.in_(sub))
        count_stmt = count_stmt.where(SysUser.id.in_(sub))

    total = db.execute(count_stmt).scalar_one()
    users = db.execute(
        stmt.order_by(SysUser.id).offset((page - 1) * size).limit(size)
    ).scalars().all()
    return [_to_item(db, u) for u in users], total


def _assert_roles_exist(db: Session, roles: list[str]) -> None:
    known = set(db.execute(select(SysRole.code)).scalars().all())
    unknown = set(roles) - known
    if unknown:
        raise ParamError(f"角色不存在：{'、'.join(sorted(unknown))}")


def create_user(db: Session, payload) -> dict:
    if db.execute(select(SysUser.id).where(SysUser.username == payload.username)).first():
        raise ConflictError(f"用户名 {payload.username} 已存在")
    _assert_roles_exist(db, payload.roles)

    did = None
    if payload.bindDid:
        did = _issue_user_did(db, payload.realName, payload.orgName)

    user = SysUser(
        username=payload.username,
        password_hash=hash_password(payload.password),
        real_name=payload.realName,
        org_name=payload.orgName,
        did=did,
        phone=payload.phone,
        email=payload.email,
        status="active",
    )
    db.add(user)
    db.flush()
    for role in payload.roles:
        db.add(SysUserRole(user_id=user.id, role_code=role))
    db.commit()
    return _to_item(db, user)


def _issue_user_did(db: Session, real_name: str, org_name: str | None) -> str | None:
    """新建用户时顺带签发一个 user 类型的 DID，并托管私钥。"""
    from modules.did.service import register_did

    result = register_did(db, subject_type="user", subject_name=real_name,
                          org_name=org_name, metadata={"source": "user:create"},
                          custody=True)
    return result["did"]


def update_user(db: Session, user_id: int, payload) -> dict:
    user = db.get(SysUser, user_id)
    if user is None:
        raise NotFoundError(f"用户 {user_id} 不存在")

    if payload.password:
        user.password_hash = hash_password(payload.password)
    if payload.realName is not None:
        user.real_name = payload.realName
    if payload.orgName is not None:
        user.org_name = payload.orgName
    if payload.phone is not None:
        user.phone = payload.phone
    if payload.email is not None:
        user.email = payload.email
    if payload.status is not None:
        user.status = payload.status

    if payload.roles is not None:
        _assert_roles_exist(db, payload.roles)
        db.execute(delete(SysUserRole).where(SysUserRole.user_id == user_id))
        for role in payload.roles:
            db.add(SysUserRole(user_id=user_id, role_code=role))
        invalidate_permission_cache()

    db.commit()
    # 状态、姓名、角色都缓存在 Redis 里，改完必须立刻抹掉，
    # 否则被停用的账号还能拿旧 Token 撑满 5 分钟缓存期
    invalidate_profile_cache(user_id)
    return _to_item(db, user)


def delete_user(db: Session, user_id: int, operator: Principal) -> dict:
    if user_id == operator.user_id:
        raise ParamError("不能删除当前登录的账号")
    user = db.get(SysUser, user_id)
    if user is None:
        raise NotFoundError(f"用户 {user_id} 不存在")
    if user.username == "admin":
        raise ParamError("内置管理员账号不可删除")

    username = user.username
    db.execute(delete(SysUserRole).where(SysUserRole.user_id == user_id))
    db.delete(user)
    db.commit()
    invalidate_profile_cache(user_id)
    return {"id": user_id, "username": username, "deleted": True}
