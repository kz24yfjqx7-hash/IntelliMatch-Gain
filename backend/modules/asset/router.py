"""数据资产路由。契约 2.4。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.deps import current_user, get_db, pagination
from core.exceptions import NoPermissionError
from core.middleware import Principal, audited, require_permission
from core.response import PageQuery, ok, page
from modules.asset import service
from modules.asset.schema import AssetCreateRequest, ClassifyRequest
from modules.permission.service import check_permission, has_own_scope_only

router = APIRouter(tags=["能源数据资产"])


@router.post("/assets", summary="数据登记")
@audited(module="asset", action="asset:register", risk="medium", resource_type="asset")
@require_permission("asset", "write")
def create_asset(
    body: AssetCreateRequest,
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    return ok(service.create_asset(db, body))


@router.get("/assets", summary="资产列表")
@require_permission("asset", "read")
def list_assets(
    pg: PageQuery = Depends(pagination),
    dataType: str | None = Query(None, pattern="^(pv|wind|storage|load|dispatch)$"),
    level: str | None = Query(None, pattern="^(L1|L2|L3|L4)$"),
    sourceDid: str | None = Query(None),
    keyword: str | None = Query(None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    # scope='own' 的角色（能源主体、边缘节点）只能看到自己名下的资产
    owner_only = principal.did if has_own_scope_only(principal, "asset", "read") else None
    items, total = service.list_assets(
        db, pg.page, pg.size, data_type=dataType, level=level,
        source_did=sourceDid, keyword=keyword, owner_only=owner_only,
    )
    return ok(page(items, total, pg.page, pg.size))


@router.get("/assets/stats", summary="分级统计")
@require_permission("asset", "read")
def stats(db: Session = Depends(get_db), _p: Principal = Depends(current_user)):
    return ok(service.stats(db))


@router.post("/assets/classify", summary="自动分类分级")
@audited(module="asset", action="asset:classify", risk="low")
@require_permission("asset", "read")
def classify(
    body: ClassifyRequest,
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    return ok(service.classify([r.model_dump() for r in body.records]))


@router.get("/assets/{asset_id}/lineage", summary="数据溯源链")
@require_permission("asset", "read", resource_id_arg="asset_id")
def lineage(asset_id: int, db: Session = Depends(get_db),
            _p: Principal = Depends(current_user)):
    return ok(service.get_lineage(db, asset_id))


@router.get("/assets/{asset_id}", summary="资产详情")
@audited(module="asset", action="asset:read", risk="low", resource_type="asset",
         resource_id_arg="asset_id")
@require_permission("asset", "read", resource_id_arg="asset_id")
def get_asset(asset_id: int, db: Session = Depends(get_db),
              principal: Principal = Depends(current_user)):
    return ok(service.get_asset(db, asset_id))
