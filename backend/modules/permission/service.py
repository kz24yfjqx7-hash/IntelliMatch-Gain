"""权限中心：全平台唯一的鉴权判定入口。

需求文档要求「改造分散鉴权逻辑，统一收敛至权限中心」，所以：
**任何模块都不许自己写 if role == 'xxx'，一律调用本文件的 check_permission。**

判定顺序（先到先得，命中即返回）：
1. 角色权限矩阵，scope='all'   → 允许，matchedRule = role:<角色>:<资源>:<操作>
2. 角色权限矩阵，scope='own'   → 资源属主是本人才允许，matchedRule = role:...:own
3. 显式授权记录 perm_grant     → 有未过期的 active 授权则允许，matchedRule = grant:<id>
4. 以上都不命中               → 拒绝，返回可读的中文原因
"""
import json
import logging

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from core import redis_client
from core.exceptions import ConflictError, NotFoundError, ParamError
from core.database import SessionLocal
from core.response import now_cst
from core.retry import run_with_retry
from modules.permission.model import PermGrant, SysRolePermission

logger = logging.getLogger(__name__)

_PERM_CACHE_PREFIX = "perm:roles:"
_PERM_CACHE_TTL = 60  # 秒。权限变更后主动删缓存，这里只是兜底

# 资源属主解析器：scope='own' 时用它判断资源是不是申请人自己的。
# 用原生 SQL 而不是 import 各模块的 ORM 模型，避免权限中心反向依赖业务模块。
_OWNER_SQL = {
    "asset": "SELECT owner_did FROM energy_asset WHERE id = :rid",
    "evidence": "SELECT actor_did FROM chain_evidence WHERE evidence_id = :rid",
    "dispatch": "SELECT creator_did FROM algo_dispatch_task WHERE task_id = :rid",
    "model": "SELECT publisher_did FROM algo_model_version WHERE version = :rid",
}


# ---------------------------------------------------------------- 权限集合

def load_permissions_for_roles(roles: list[str]) -> set[str]:
    """把角色列表展开成扁平权限串集合，格式 <resourceType>:<action>。

    契约 2.1 里 GET /auth/me 的 permissions 字段直接用它，
    前端按钮级权限控制也是拿这个集合做判断。
    """
    if not roles:
        return set()

    cache_key = _PERM_CACHE_PREFIX + ",".join(sorted(roles))
    cached = redis_client.safe_get(cache_key)
    if cached:
        try:
            return set(json.loads(cached))
        except json.JSONDecodeError:
            pass

    with SessionLocal() as db:
        rows = db.execute(
            select(SysRolePermission.resource_type, SysRolePermission.action)
            .where(SysRolePermission.role_code.in_(roles))
        ).all()
    perms = {f"{r}:{a}" for r, a in rows}
    redis_client.safe_set(cache_key, json.dumps(sorted(perms)), ex=_PERM_CACHE_TTL)
    return perms


def invalidate_permission_cache() -> None:
    """角色权限被修改后调用，避免最长 60 秒的脏读。"""
    try:
        keys = list(redis_client.client.scan_iter(match=_PERM_CACHE_PREFIX + "*", count=100))
        if keys:
            redis_client.client.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning("清理权限缓存失败：%s", exc)


# ---------------------------------------------------------------- 鉴权判定

def check_permission(principal, resource_type: str, action: str,
                     resource_id: str | None = None) -> tuple[bool, str]:
    """返回 (是否允许, 原因)。原因是给人看的中文，会直接进审计日志和前端提示。"""
    allowed, reason, _ = check_permission_detail(principal, resource_type, action, resource_id)
    return allowed, reason


def check_permission_detail(principal, resource_type: str, action: str,
                            resource_id: str | None = None) -> tuple[bool, str, str | None]:
    """比 check_permission 多返回一个 matchedRule，供 POST /permissions/check 使用。"""
    roles = principal.roles or []
    role_label = "/".join(roles) or "无角色"

    with SessionLocal() as db:
        rules = db.execute(
            select(SysRolePermission.role_code, SysRolePermission.scope)
            .where(
                SysRolePermission.role_code.in_(roles),
                SysRolePermission.resource_type == resource_type,
                SysRolePermission.action == action,
            )
        ).all()

        # 1) 角色权限：全量范围
        for role_code, scope in rules:
            if scope == "all":
                return True, "角色权限矩阵放行", f"role:{role_code}:{resource_type}:{action}"

        # 2) 角色权限：仅自有数据
        for role_code, scope in rules:
            if scope == "own":
                if resource_id is None:
                    # 列表类接口没有具体资源，放行到 service 层再按 owner_did 过滤
                    return True, "角色权限矩阵放行（仅自有数据）", f"role:{role_code}:{resource_type}:{action}:own"
                owner = _resolve_owner(db, resource_type, resource_id)
                if owner and principal.did and owner == principal.did:
                    return True, "资源属主校验通过", f"role:{role_code}:{resource_type}:{action}:own"

        # 3) 显式授权记录
        if resource_id is not None and principal.did:
            grant = db.execute(
                select(PermGrant.id, PermGrant.expire_at)
                .where(
                    PermGrant.grantee_did == principal.did,
                    PermGrant.resource_type == resource_type,
                    PermGrant.resource_id == str(resource_id),
                    PermGrant.action == action,
                    PermGrant.status == "active",
                )
                .limit(1)
            ).first()
            if grant:
                grant_id, expire_at = grant
                if expire_at is None or expire_at >= now_cst().replace(tzinfo=None):
                    return True, "命中显式授权记录", f"grant:{grant_id}"
                return False, f"授权已于 {expire_at:%Y-%m-%d %H:%M} 过期，请重新申请", None

    # 4) 拒绝
    if rules:
        return False, f"角色 {role_label} 对该资源仅有自有数据范围的 {resource_type}:{action} 权限", None
    return False, f"角色 {role_label} 无 {resource_type}:{action} 权限", None


def _resolve_owner(db: Session, resource_type: str, resource_id: str) -> str | None:
    sql = _OWNER_SQL.get(resource_type)
    if not sql:
        return None
    try:
        row = db.execute(text(sql), {"rid": resource_id}).first()
    except Exception as exc:  # noqa: BLE001  资源表可能尚未有数据
        logger.debug("解析资源属主失败 %s/%s：%s", resource_type, resource_id, exc)
        return None
    return row[0] if row else None


def has_own_scope_only(principal, resource_type: str, action: str) -> bool:
    """列表类接口用它判断是否需要在 SQL 里加 owner_did 过滤。"""
    with SessionLocal() as db:
        rules = db.execute(
            select(SysRolePermission.scope).where(
                SysRolePermission.role_code.in_(principal.roles or []),
                SysRolePermission.resource_type == resource_type,
                SysRolePermission.action == action,
            )
        ).scalars().all()
    return bool(rules) and all(s == "own" for s in rules)


# ---------------------------------------------------------------- 权限矩阵

RESOURCES = ["asset", "model", "dispatch", "evidence", "algo"]
ACTIONS = ["read", "write", "execute", "issue", "export"]


def get_matrix(db: Session) -> dict:
    """契约 2.5 GET /permissions/matrix。"""
    from modules.auth.model import SysRole

    roles = db.execute(select(SysRole).order_by(SysRole.id)).scalars().all()
    perms = db.execute(select(SysRolePermission)).scalars().all()

    grants_by_role: dict[str, dict[str, list[str]]] = {}
    scopes_by_role: dict[str, dict[str, str]] = {}
    for p in perms:
        grants_by_role.setdefault(p.role_code, {}).setdefault(p.resource_type, []).append(p.action)
        scopes_by_role.setdefault(p.role_code, {})[f"{p.resource_type}:{p.action}"] = p.scope

    return {
        "resources": RESOURCES,
        "actions": ACTIONS,
        "roles": [
            {
                "code": r.code,
                "name": r.name,
                "description": r.description,
                "isBuiltin": bool(r.is_builtin),
                "grants": grants_by_role.get(r.code, {}),
                "scopes": scopes_by_role.get(r.code, {}),
            }
            for r in roles
        ],
    }


# ---------------------------------------------------------------- 角色管理

def list_roles(db: Session) -> list[dict]:
    from modules.auth.model import SysRole, SysUserRole

    roles = db.execute(select(SysRole).order_by(SysRole.id)).scalars().all()
    perms = db.execute(select(SysRolePermission)).scalars().all()
    counts = dict(
        db.execute(
            select(SysUserRole.role_code, func.count()).group_by(SysUserRole.role_code)
        ).all()
    )

    by_role: dict[str, list[dict]] = {}
    for p in perms:
        by_role.setdefault(p.role_code, []).append(
            {"resourceType": p.resource_type, "action": p.action, "scope": p.scope}
        )

    return [
        {
            "code": r.code,
            "name": r.name,
            "description": r.description,
            "isBuiltin": bool(r.is_builtin),
            "userCount": counts.get(r.code, 0),
            "grants": by_role.get(r.code, []),
        }
        for r in roles
    ]


def create_role(db: Session, payload) -> dict:
    from modules.auth.model import SysRole

    if db.execute(select(SysRole.id).where(SysRole.code == payload.code)).first():
        raise ConflictError(f"角色码 {payload.code} 已存在")

    db.add(SysRole(code=payload.code, name=payload.name,
                   description=payload.description, is_builtin=0))
    for g in payload.grants:
        db.add(SysRolePermission(role_code=payload.code, resource_type=g.resourceType,
                                 action=g.action, scope=g.scope))
    db.commit()
    invalidate_permission_cache()
    return {"code": payload.code, "name": payload.name,
            "grants": [g.model_dump() for g in payload.grants]}


def update_role(db: Session, code: str, payload) -> dict:
    from modules.auth.model import SysRole

    role = db.execute(select(SysRole).where(SysRole.code == code)).scalar_one_or_none()
    if role is None:
        raise NotFoundError(f"角色 {code} 不存在")

    if payload.name is not None:
        role.name = payload.name
    if payload.description is not None:
        role.description = payload.description

    if payload.grants is not None:
        if role.is_builtin and code == "sys_admin":
            # 兜底：把系统管理员的权限改空会让平台彻底失控，直接拦住
            raise ParamError("内置角色 sys_admin 的权限不可修改")
        db.execute(delete(SysRolePermission).where(SysRolePermission.role_code == code))
        for g in payload.grants:
            db.add(SysRolePermission(role_code=code, resource_type=g.resourceType,
                                     action=g.action, scope=g.scope))

    db.commit()
    invalidate_permission_cache()
    return {"code": code, "name": role.name, "description": role.description,
            "grants": [g.model_dump() for g in (payload.grants or [])]}


# ---------------------------------------------------------------- 申请 → 审批 → 授权

from datetime import datetime, timedelta  # noqa: E402

from core.exceptions import DidInvalidError  # noqa: E402
from core.middleware import current_trace_id  # noqa: E402
from core.response import iso  # noqa: E402
from modules.evidence.service import write_evidence  # noqa: E402
from modules.notice import service as notice_service  # noqa: E402
from modules.permission.model import PermApplication, PermChangeLog  # noqa: E402

_DEFAULT_GRANT_DAYS = 30


def _parse_expire(value: str | None, default_days: int = _DEFAULT_GRANT_DAYS) -> datetime:
    if not value:
        return (now_cst() + timedelta(days=default_days)).replace(tzinfo=None)
    try:
        expire_at = datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError as exc:
        raise ParamError(f"expireAt 格式非法，应为 ISO 8601：{value}") from exc
    # B-003：过去的时间等于「申请一个一生效就过期的授权」，没有任何业务含义，
    # 放行只会在 perm_grant 里留下一批天生失效的记录，按契约 1.1 返回 1001。
    if expire_at <= now_cst().replace(tzinfo=None):
        raise ParamError(f"expireAt 必须晚于当前时间：{value}")
    return expire_at


def _log_change(db: Session, *, target_did: str, change_type: str, resource_type: str | None,
                resource_id: str | None, action: str | None, operator_did: str | None,
                detail: str, evidence_id: str | None) -> None:
    """权限变更留痕，同时喂给 R03 高频权限变更规则。"""
    db.add(PermChangeLog(
        target_did=target_did, change_type=change_type, resource_type=resource_type,
        resource_id=resource_id, action=action, operator_did=operator_did,
        detail=detail, evidence_id=evidence_id, trace_id=current_trace_id.get(),
    ))
    try:
        from modules.audit.rules import fire_perm_change

        # 本事务刚 write_evidence 过、链尾行锁还没释放，告警必须借这条会话上链，
        # 否则另开连接会等自己的锁：请求多挂 9 秒且告警行随重试回滚丢失（见 rules.fire）
        fire_perm_change(target_did, change_type, db=db)
    except ImportError:
        pass


# ---------------------------------------------------------------- 站内消息（铃铛）

# 审批人角色：与 router 里 approve/reject 的 require_roles 保持一致，改一处要同步改另一处
APPROVER_ROLES = ("sys_admin",)

_RES_LABELS = {"asset": "数据资产", "model": "模型", "dispatch": "调度指令",
               "evidence": "存证", "algo": "算法", "user": "用户"}
_ACT_LABELS = {"read": "读取", "write": "写入", "execute": "执行",
               "issue": "签发", "export": "导出", "manage": "管理"}


def _res_label(t: str | None) -> str:
    return _RES_LABELS.get(t or "", t or "资源")


def _act_label(a: str | None) -> str:
    return _ACT_LABELS.get(a or "", a or "")


def _notify_approvers(db: Session, app: PermApplication, principal) -> None:
    """有人提交申请 → 给所有审批人的铃铛发一条待办。"""
    who = app.applicant_name or app.applicant_did
    notice_service.notify_roles(
        db, APPROVER_ROLES,
        exclude_did=app.applicant_did,   # 管理员给自己提的申请不必再通知自己
        category="permission_apply", level="warning",
        title="有新的权限申请待审批",
        content=(f"{who} 申请 {_res_label(app.resource_type)} {app.resource_id} 的"
                 f"{_act_label(app.action)}权限。理由：{app.reason or '未填写'}"),
        link=f"/permission?tab=apps&id={app.id}",
        ref_type="permission_application", ref_id=str(app.id),
        actor_did=app.applicant_did, actor_name=app.applicant_name,
    )


def _notify_applicant(db: Session, app: PermApplication, *, approved: bool) -> None:
    """审批完成 → 通知申请人结果。"""
    notice_service.notify_dids(
        db, [app.applicant_did],
        category="permission_result",
        level="success" if approved else "warning",
        title="权限申请已通过" if approved else "权限申请被驳回",
        content=(f"{_res_label(app.resource_type)} {app.resource_id} 的"
                 f"{_act_label(app.action)}权限申请"
                 f"{'已通过' if approved else '被驳回'}。"
                 f"审批意见：{app.approve_reason or '无'}"),
        link=("/permission?tab=grants" if approved
              else f"/permission?tab=apps&id={app.id}"),
        ref_type="permission_application", ref_id=str(app.id),
        actor_did=app.approver_did, actor_name=app.approver_name,
    )


def _app_to_item(app: PermApplication) -> dict:
    return {
        "id": app.id,
        "applicantDid": app.applicant_did,
        "applicantName": app.applicant_name,
        "resourceType": app.resource_type,
        "resourceId": app.resource_id,
        "action": app.action,
        "reason": app.reason,
        "status": app.status,
        "expireAt": iso(app.expire_at),
        "approverDid": app.approver_did,
        "approverName": app.approver_name,
        "approveReason": app.approve_reason,
        "approvedAt": iso(app.approved_at),
        "evidenceId": app.evidence_id,
        "traceId": app.trace_id,
        "createdAt": iso(app.created_at),
    }


def apply_permission(db: Session, principal, payload) -> dict:
    return run_with_retry(db, lambda: _apply_permission(db, principal, payload),
                          what="提交权限申请")


def _apply_permission(db: Session, principal, payload) -> dict:
    if not principal.did:
        raise DidInvalidError("当前账号未绑定 DID，无法提交权限申请")

    # 同一主体对同一资源同一操作只允许有一条待审批申请，避免刷申请
    exists = db.execute(
        select(PermApplication.id).where(
            PermApplication.applicant_did == principal.did,
            PermApplication.resource_type == payload.resourceType,
            PermApplication.resource_id == payload.resourceId,
            PermApplication.action == payload.action,
            PermApplication.status == "pending",
        )
    ).first()
    if exists:
        raise ConflictError("已有一条待审批的相同申请，请勿重复提交")

    app = PermApplication(
        applicant_did=principal.did,
        applicant_name=principal.real_name or principal.username,
        resource_type=payload.resourceType, resource_id=payload.resourceId,
        action=payload.action, reason=payload.reason, status="pending",
        expire_at=_parse_expire(payload.expireAt), trace_id=current_trace_id.get(),
    )
    db.add(app)
    db.flush()

    evidence = write_evidence(db, category="permission", ref_id=f"app-{app.id}", payload={
        "action": "permission:apply", "applicationId": app.id,
        "applicant": principal.did, "resourceType": payload.resourceType,
        "resourceId": payload.resourceId, "operation": payload.action,
        "reason": payload.reason,
    })
    app.evidence_id = evidence["evidenceId"]
    _log_change(db, target_did=principal.did, change_type="apply",
                resource_type=payload.resourceType, resource_id=payload.resourceId,
                action=payload.action, operator_did=principal.did,
                detail=payload.reason, evidence_id=evidence["evidenceId"])
    db.commit()

    item = _app_to_item(app)
    # 通知在事务提交之后发：消息里的链接指向 app.id，必须先可查再通知
    _notify_approvers(db, app, principal)
    return item


def list_applications(db: Session, page: int, size: int, *, status: str | None = None,
                      applicant_did: str | None = None) -> tuple[list[dict], int]:
    stmt = select(PermApplication)
    count_stmt = select(func.count()).select_from(PermApplication)
    if status:
        stmt = stmt.where(PermApplication.status == status)
        count_stmt = count_stmt.where(PermApplication.status == status)
    if applicant_did:
        stmt = stmt.where(PermApplication.applicant_did == applicant_did)
        count_stmt = count_stmt.where(PermApplication.applicant_did == applicant_did)

    total = db.execute(count_stmt).scalar_one()
    apps = db.execute(
        stmt.order_by(PermApplication.id.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()
    return [_app_to_item(a) for a in apps], total


def _claim_pending(db: Session, application_id: int, new_status: str) -> PermApplication:
    """把一条 pending 申请原子地「占」下来。

    B-012：原来是先 `db.get()` 读出来判断 status，再改字段提交。两个审批人同时点，
    两边都读到 pending，于是两边都发 UPDATE —— 后到的那条 0 行匹配，
    SQLAlchemy 抛 StaleDataError 冒泡成 500。

    改成条件更新 `WHERE id=:id AND status='pending'`：InnoDB 会让后到者阻塞到
    先到者提交，再以最新版本重新求值 —— 匹配 0 行即说明已被处理，按契约返回 1006。
    """
    app = db.get(PermApplication, application_id)
    if app is None:
        raise NotFoundError(f"权限申请 {application_id} 不存在")
    if app.status != "pending":
        raise ConflictError(f"申请已处于 {app.status} 状态，不能重复审批")

    claimed = db.execute(
        update(PermApplication)
        .where(PermApplication.id == application_id, PermApplication.status == "pending")
        .values(status=new_status)
        .execution_options(synchronize_session=False)
    ).rowcount
    if not claimed:
        # 不主动 rollback：占位失败没有改动任何数据，事务随请求结束自然回滚
        raise ConflictError("该申请已被其他审批人处理，不能重复审批")
    return app


def approve_application(db: Session, application_id: int, principal, payload) -> dict:
    return run_with_retry(db, lambda: _approve_application(db, application_id, principal, payload),
                          what="权限审批")


def _approve_application(db: Session, application_id: int, principal, payload) -> dict:
    app = _claim_pending(db, application_id, "approved")

    app.status = "approved"
    app.approver_did = principal.did
    app.approver_name = principal.real_name or principal.username
    app.approve_reason = payload.reason or "符合最小必要原则，予以授权"
    app.approved_at = now_cst().replace(tzinfo=None)
    if payload.expireAt:
        app.expire_at = _parse_expire(payload.expireAt)

    grant = PermGrant(
        application_id=app.id, grantee_did=app.applicant_did,
        grantee_name=app.applicant_name, resource_type=app.resource_type,
        resource_id=app.resource_id, action=app.action, status="active",
        expire_at=app.expire_at, trace_id=current_trace_id.get(),
    )
    db.add(grant)
    db.flush()

    evidence = write_evidence(db, category="permission", ref_id=f"grant-{grant.id}", payload={
        "action": "permission:grant", "applicationId": app.id, "grantId": grant.id,
        "grantee": app.applicant_did, "resourceType": app.resource_type,
        "resourceId": app.resource_id, "operation": app.action,
        "approver": principal.did, "expireAt": iso(app.expire_at),
    })
    grant.evidence_id = evidence["evidenceId"]

    _log_change(db, target_did=app.applicant_did, change_type="approve",
                resource_type=app.resource_type, resource_id=app.resource_id,
                action=app.action, operator_did=principal.did,
                detail="审批通过并生成授权", evidence_id=evidence["evidenceId"])

    # 资产类授权同步更新资产的授权状态与溯源链
    if app.resource_type == "asset" and app.resource_id.isdigit():
        from modules.asset.service import mark_authorized

        mark_authorized(db, int(app.resource_id), evidence["evidenceId"])

    db.commit()
    invalidate_permission_cache()

    result = {**_app_to_item(app), "grantId": grant.id, "evidenceId": evidence["evidenceId"]}
    _notify_applicant(db, app, approved=True)
    return result


def reject_application(db: Session, application_id: int, principal, payload) -> dict:
    return run_with_retry(db, lambda: _reject_application(db, application_id, principal, payload),
                          what="权限驳回")


def _reject_application(db: Session, application_id: int, principal, payload) -> dict:
    app = _claim_pending(db, application_id, "rejected")

    app.status = "rejected"
    app.approver_did = principal.did
    app.approver_name = principal.real_name or principal.username
    app.approve_reason = payload.reason or "申请范围超出最小必要原则，予以驳回"
    app.approved_at = now_cst().replace(tzinfo=None)

    evidence = write_evidence(db, category="permission", ref_id=f"app-{app.id}", payload={
        "action": "permission:reject", "applicationId": app.id,
        "applicant": app.applicant_did, "approver": principal.did,
        "reason": app.approve_reason,
    })
    _log_change(db, target_did=app.applicant_did, change_type="reject",
                resource_type=app.resource_type, resource_id=app.resource_id,
                action=app.action, operator_did=principal.did,
                detail=app.approve_reason, evidence_id=evidence["evidenceId"])
    db.commit()

    result = {**_app_to_item(app), "evidenceId": evidence["evidenceId"]}
    _notify_applicant(db, app, approved=False)
    return result


def _grant_to_item(grant: PermGrant, granter: tuple[str | None, str | None] = (None, None)) -> dict:
    # 授权人（审批的管理员）：授权记录本身不存这个字段，从它关联的申请
    # （approver_did / approver_name）解析。granter = (did, name)，由 list_grants 批量传入。
    granted_by_did, granted_by_name = granter
    return {
        "id": grant.id,
        "applicationId": grant.application_id,
        "granteeDid": grant.grantee_did,
        "granteeName": grant.grantee_name,
        "resourceType": grant.resource_type,
        "resourceId": grant.resource_id,
        "action": grant.action,
        "status": grant.status,
        "grantedAt": iso(grant.granted_at),
        "expireAt": iso(grant.expire_at),
        "revokedAt": iso(grant.revoked_at),
        "revokeReason": grant.revoke_reason,
        "evidenceId": grant.evidence_id,
        "grantedBy": granted_by_did,       # 授权人 DID（前端「授权人」列读它）
        "grantedByName": granted_by_name,  # 授权人名字
    }


def _resolve_granters(db: Session, grants: list) -> dict[int, tuple[str | None, str | None]]:
    """一次性查出这批授权对应申请的审批人，返回 {application_id: (did, name)}。"""
    app_ids = {g.application_id for g in grants if g.application_id is not None}
    if not app_ids:
        return {}
    rows = db.execute(
        select(PermApplication.id, PermApplication.approver_did, PermApplication.approver_name)
        .where(PermApplication.id.in_(app_ids))
    ).all()
    return {aid: (did, name) for aid, did, name in rows}


def list_grants(db: Session, page: int, size: int, *, did: str | None = None,
                status: str | None = None) -> tuple[list[dict], int]:
    stmt = select(PermGrant)
    count_stmt = select(func.count()).select_from(PermGrant)
    if did:
        stmt = stmt.where(PermGrant.grantee_did == did)
        count_stmt = count_stmt.where(PermGrant.grantee_did == did)
    if status:
        stmt = stmt.where(PermGrant.status == status)
        count_stmt = count_stmt.where(PermGrant.status == status)

    total = db.execute(count_stmt).scalar_one()
    grants = db.execute(
        stmt.order_by(PermGrant.id.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()
    granters = _resolve_granters(db, grants)
    return [_grant_to_item(g, granters.get(g.application_id, (None, None))) for g in grants], total


def revoke_grant(db: Session, grant_id: int, principal, reason: str) -> dict:
    return run_with_retry(db, lambda: _revoke_grant(db, grant_id, principal, reason),
                          what="撤销授权")


def _revoke_grant(db: Session, grant_id: int, principal, reason: str) -> dict:
    grant = db.get(PermGrant, grant_id)
    if grant is not None and grant.status == "active":
        # 并发撤销同一条授权：只有把 active 改成 revoked 的那个请求算数
        claimed = db.execute(
            update(PermGrant)
            .where(PermGrant.id == grant_id, PermGrant.status == "active")
            .values(status="revoked")
            .execution_options(synchronize_session=False)
        ).rowcount
        if not claimed:
            raise ConflictError("该授权已被撤销")
    if grant is None:
        raise NotFoundError(f"授权记录 {grant_id} 不存在")
    if grant.status != "active":
        raise ConflictError(f"授权已处于 {grant.status} 状态")

    grant.status = "revoked"
    grant.revoked_at = now_cst().replace(tzinfo=None)
    grant.revoke_reason = reason

    evidence = write_evidence(db, category="permission", ref_id=f"grant-{grant_id}", payload={
        "action": "permission:revoke", "grantId": grant_id,
        "grantee": grant.grantee_did, "resourceType": grant.resource_type,
        "resourceId": grant.resource_id, "operation": grant.action,
        "operator": principal.did, "reason": reason,
    })
    _log_change(db, target_did=grant.grantee_did, change_type="revoke",
                resource_type=grant.resource_type, resource_id=grant.resource_id,
                action=grant.action, operator_did=principal.did,
                detail=reason, evidence_id=evidence["evidenceId"])
    db.commit()
    invalidate_permission_cache()

    granter = _resolve_granters(db, [grant]).get(grant.application_id, (None, None))
    result = {**_grant_to_item(grant, granter), "evidenceId": evidence["evidenceId"]}
    notice_service.notify_dids(
        db, [grant.grantee_did],
        category="permission_revoke", level="warning",
        title="你的一项授权已被回收",
        content=(f"{_res_label(grant.resource_type)} {grant.resource_id} 的"
                 f"{_act_label(grant.action)}权限已被回收。原因：{reason}"),
        link="/permission?tab=grants",
        ref_type="perm_grant", ref_id=str(grant.id),
        actor_did=principal.did,
        actor_name=principal.real_name or principal.username,
    )
    return result
