"""DID 身份管理路由。契约 2.2。

身份类操作不在契约的 resourceType 枚举里（asset|model|dispatch|evidence|algo），
所以用角色粒度的 require_roles 控制，而不是硬造一个枚举值出来。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.deps import current_user, get_db, pagination, require_roles
from core.exceptions import NoPermissionError
from core.middleware import Principal, audited
from core.response import PageQuery, ok, page
from modules.did import service
from modules.did.schema import (
    DidRegisterRequest,
    DidResolveRequest,
    DidRotateKeyRequest,
    DidStatusRequest,
    DidVerifyRequest,
)

router = APIRouter(tags=["可信身份"])


@router.post("/did/register", summary="签发 DID")
@audited(module="did", action="did:register", risk="medium")
def register(
    body: DidRegisterRequest,
    db: Session = Depends(get_db),
    _p: Principal = Depends(require_roles("sys_admin", "vpp_operator")),
):
    return ok(service.register_did(
        db, subject_type=body.subjectType, subject_name=body.subjectName,
        org_name=body.orgName, controller_did=body.controllerDid,
        metadata=body.metadata, custody=body.custody,
    ))


@router.get("/did", summary="DID 列表")
def list_dids(
    pg: PageQuery = Depends(pagination),
    subjectType: str | None = Query(None, pattern="^(user|device|org|edge)$"),
    status: str | None = Query(None, pattern="^(active|frozen|revoked)$"),
    keyword: str | None = Query(None, description="按主体名称或 DID 模糊搜索"),
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    items, total = service.list_dids(db, pg.page, pg.size, subjectType, status, keyword)
    return ok(page(items, total, pg.page, pg.size))


@router.get("/did/{did:path}/document", summary="DID 文档")
def get_document(did: str, db: Session = Depends(get_db),
                 _p: Principal = Depends(current_user)):
    return ok(service.get_did_detail(db, did))


@router.post("/did/verify", summary="验签")
@audited(module="did", action="did:verify", risk="medium")
def verify(body: DidVerifyRequest, db: Session = Depends(get_db),
           _p: Principal = Depends(current_user)):
    return ok(service.verify_detail(db, body.did, body.message, body.signature))


@router.post("/did/resolve", summary="批量解析")
def resolve(body: DidResolveRequest, db: Session = Depends(get_db),
            _p: Principal = Depends(current_user)):
    return ok({"items": service.resolve(db, body.dids), "total": len(body.dids)})


@router.post("/did/{did:path}/status", summary="冻结 / 解冻 / 注销")
@audited(module="did", action="did:status", risk="high", resource_type="user",
         resource_id_arg="did")
def change_status(
    did: str,
    body: DidStatusRequest,
    db: Session = Depends(get_db),
    _p: Principal = Depends(require_roles("sys_admin")),
):
    return ok(service.change_status(db, did, body.action, body.reason))


@router.post("/did/{did:path}/rotate-key", summary="密钥轮换")
@audited(module="key", action="key:rotate", risk="high", resource_type="user",
         resource_id_arg="did")
def rotate_key(
    did: str,
    body: DidRotateKeyRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    # 只能轮换自己的密钥，管理员可以轮换任何人的
    if principal.did != did and "sys_admin" not in principal.roles:
        raise NoPermissionError("只能轮换自己名下的密钥")
    return ok(service.rotate_key(db, did, body.reason, body.custody))


@router.get("/did/{did:path}", summary="DID 详情")
def get_did(did: str, db: Session = Depends(get_db),
            _p: Principal = Depends(current_user)):
    return ok(service.get_did_detail(db, did))
