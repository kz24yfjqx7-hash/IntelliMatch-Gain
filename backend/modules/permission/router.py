"""权限中心路由。契约 2.5。

第二步先交付角色与权限矩阵部分；申请-审批-授权流转在第四步补齐。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.deps import current_user, get_db, pagination, require_roles
from core.middleware import Principal, audited
from core.response import PageQuery, ok, page
from modules.permission import service
from modules.permission.schema import (
    ApprovalRequest,
    PermissionApplyRequest,
    PermissionCheckRequest,
    RevokeRequest,
    RoleCreateRequest,
    RoleUpdateRequest,
)

router = APIRouter(tags=["权限控制中心"])


@router.get("/roles", summary="角色列表")
def list_roles(db: Session = Depends(get_db), _p: Principal = Depends(current_user)):
    return ok(service.list_roles(db))


@router.post("/roles", summary="新建自定义角色")
@audited(module="permission", action="role:create", risk="high")
def create_role(
    body: RoleCreateRequest,
    db: Session = Depends(get_db),
    _admin: Principal = Depends(require_roles("sys_admin")),
):
    return ok(service.create_role(db, body))


@router.put("/roles/{code}", summary="修改角色权限")
@audited(module="permission", action="role:update", risk="high", resource_type="user",
         resource_id_arg="code")
def update_role(
    code: str,
    body: RoleUpdateRequest,
    db: Session = Depends(get_db),
    _admin: Principal = Depends(require_roles("sys_admin")),
):
    return ok(service.update_role(db, code, body))


@router.get("/permissions/matrix", summary="资源-操作权限矩阵")
def get_matrix(db: Session = Depends(get_db), _p: Principal = Depends(current_user)):
    return ok(service.get_matrix(db))


@router.post("/permissions/check", summary="权限校验")
def check(
    body: PermissionCheckRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    """既供前端做按钮级预判，也是平台内部统一的鉴权入口。

    传 did 则校验该主体（需管理员或监管方），不传则校验当前登录主体。
    """
    target = principal
    if body.did and body.did != principal.did:
        if not ({"sys_admin", "regulator"} & set(principal.roles)):
            from core.exceptions import NoPermissionError

            raise NoPermissionError("仅系统管理员与监管方可校验他人权限")
        target = _principal_of_did(db, body.did)

    allowed, reason, matched = service.check_permission_detail(
        target, body.resourceType, body.action, body.resourceId
    )
    return ok({
        "allowed": allowed,
        "reason": reason if not allowed else None,
        "matchedRule": matched,
        "level": _resource_level(db, body.resourceType, body.resourceId),
    })


def _principal_of_did(db: Session, did: str) -> Principal:
    from sqlalchemy import select

    from core.exceptions import NotFoundError
    from modules.auth.model import SysUser, SysUserRole

    user = db.execute(select(SysUser).where(SysUser.did == did)).scalar_one_or_none()
    if user is None:
        # 非用户型 DID（设备 / 边缘节点）没有系统角色，按无角色处理
        return Principal(user_id=0, username=did, roles=[], did=did)
    roles = list(
        db.execute(select(SysUserRole.role_code).where(SysUserRole.user_id == user.id))
        .scalars().all()
    )
    return Principal(user_id=user.id, username=user.username, roles=roles, did=did,
                     real_name=user.real_name)


def _resource_level(db: Session, resource_type: str, resource_id: str | None) -> str | None:
    """资产类资源返回敏感等级，供前端提示「你在申请一条 L4 核心数据」。"""
    if resource_type != "asset" or not resource_id:
        return None
    from sqlalchemy import text

    row = db.execute(text("SELECT level FROM energy_asset WHERE id = :rid"),
                     {"rid": resource_id}).first()
    return row[0] if row else None


# ---------------------------------------------------------------- 申请 → 审批 → 授权

@router.post("/permissions/apply", summary="提交权限申请")
@audited(module="permission", action="permission:apply", risk="low")
def apply_permission(
    body: PermissionApplyRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    return ok(service.apply_permission(db, principal, body))


@router.get("/permissions/applications", summary="申请列表")
def list_applications(
    pg: PageQuery = Depends(pagination),
    status: str | None = Query(None, pattern="^(pending|approved|rejected|expired)$"),
    applicantDid: str | None = Query(None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    # 非管理员/监管方只能看自己的申请
    if not ({"sys_admin", "regulator"} & set(principal.roles)):
        applicantDid = principal.did
    items, total = service.list_applications(
        db, pg.page, pg.size, status=status, applicant_did=applicantDid)
    return ok(page(items, total, pg.page, pg.size))


@router.post("/permissions/applications/{application_id}/approve", summary="审批通过")
@audited(module="permission", action="permission:approve", risk="high",
         resource_type="asset", resource_id_arg="application_id")
def approve(
    application_id: int,
    body: ApprovalRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_roles("sys_admin")),
):
    return ok(service.approve_application(db, application_id, principal, body))


@router.post("/permissions/applications/{application_id}/reject", summary="驳回申请")
@audited(module="permission", action="permission:reject", risk="medium",
         resource_type="asset", resource_id_arg="application_id")
def reject(
    application_id: int,
    body: ApprovalRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_roles("sys_admin")),
):
    return ok(service.reject_application(db, application_id, principal, body))


@router.get("/permissions/grants", summary="已授权列表")
def list_grants(
    pg: PageQuery = Depends(pagination),
    did: str | None = Query(None),
    status: str | None = Query(None, pattern="^(active|revoked|expired)$"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    if not ({"sys_admin", "regulator"} & set(principal.roles)):
        did = principal.did
    items, total = service.list_grants(db, pg.page, pg.size, did=did, status=status)
    return ok(page(items, total, pg.page, pg.size))


@router.post("/permissions/grants/{grant_id}/revoke", summary="回收授权")
@audited(module="permission", action="permission:revoke", risk="high",
         resource_type="asset", resource_id_arg="grant_id")
def revoke_grant(
    grant_id: int,
    body: RevokeRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_roles("sys_admin")),
):
    return ok(service.revoke_grant(db, grant_id, principal, body.reason))
