"""密钥管理路由。契约 2.3。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.deps import current_user, get_db, pagination, require_roles
from core.middleware import Principal, audited
from core.response import PageQuery, ok, page
from modules.did.schema import KeyCreateRequest, KeyStatusRequest
from modules.key import service

router = APIRouter(tags=["密钥管理"])


@router.get("/keys", summary="密钥列表")
def list_keys(
    pg: PageQuery = Depends(pagination),
    did: str | None = Query(None),
    status: str | None = Query(None, pattern="^(active|frozen|revoked)$"),
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    items, total = service.list_keys(db, pg.page, pg.size, did, status)
    return ok(page(items, total, pg.page, pg.size))


@router.post("/keys", summary="生成并绑定密钥")
@audited(module="key", action="key:create", risk="high")
def create_key(
    body: KeyCreateRequest,
    db: Session = Depends(get_db),
    _p: Principal = Depends(require_roles("sys_admin", "vpp_operator")),
):
    return ok(service.create_key(db, body))


@router.post("/keys/{key_id}/freeze", summary="冻结密钥")
@audited(module="key", action="key:freeze", risk="high", resource_type="user",
         resource_id_arg="key_id")
def freeze_key(
    key_id: int,
    body: KeyStatusRequest,
    db: Session = Depends(get_db),
    _p: Principal = Depends(require_roles("sys_admin")),
):
    return ok(service.change_key_status(db, key_id, "frozen", body.reason))


@router.post("/keys/{key_id}/revoke", summary="注销密钥")
@audited(module="key", action="key:revoke", risk="critical", resource_type="user",
         resource_id_arg="key_id")
def revoke_key(
    key_id: int,
    body: KeyStatusRequest,
    db: Session = Depends(get_db),
    _p: Principal = Depends(require_roles("sys_admin")),
):
    return ok(service.change_key_status(db, key_id, "revoked", body.reason))


@router.get("/keys/{key_id}/history", summary="轮换历史")
def key_history(key_id: int, db: Session = Depends(get_db),
                _p: Principal = Depends(current_user)):
    return ok(service.key_history(db, key_id))
