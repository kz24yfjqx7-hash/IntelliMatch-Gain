"""节点与拓扑路由。契约 2.8。"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.deps import current_user, get_db, pagination
from core.middleware import Principal, audited, require_permission, require_signature
from core.response import PageQuery, ok, page
from modules.node import service

router = APIRouter(tags=["节点与拓扑"])


class NodeOnlineRequest(BaseModel):
    did: str = Field(max_length=128)
    nonce: str = Field(min_length=4, max_length=64, description="一次性随机串，防重放")
    signature: str = Field(max_length=256, description="用节点私钥对 nonce 的 SM2 签名")


@router.get("/nodes", summary="节点列表")
@require_permission("asset", "read")
def list_nodes(
    pg: PageQuery = Depends(pagination),
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    items, total = service.list_nodes(db, pg.page, pg.size)
    return ok(page(items, total, pg.page, pg.size))


@router.get("/nodes/{node_id}/metrics", summary="历史指标")
@require_permission("asset", "read")
def get_metrics(
    node_id: str,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    interval: str = Query("hour", pattern="^(raw|hour|day)$"),
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    return ok(service.get_metrics(db, node_id, from_, to, interval))


@router.post("/nodes/{node_id}/online", summary="设备上线（DID 签名校验）")
@audited(module="node", action="node:online", risk="medium", resource_type="asset",
         resource_id_arg="node_id")
@require_signature(did_arg="did", signature_arg="signature", message_arg="nonce")
def node_online(
    node_id: str,
    body: NodeOnlineRequest,
    db: Session = Depends(get_db),
    _p: Principal = Depends(current_user),
):
    """验签失败返回 1004，且会记一条高危审计日志并触发 R02 告警。"""
    return ok(service.node_online(db, node_id, body.did, body.nonce))


@router.get("/nodes/{node_id}", summary="节点详情")
@require_permission("asset", "read")
def get_node(node_id: str, db: Session = Depends(get_db),
             _p: Principal = Depends(current_user)):
    return ok(service.get_node(db, node_id))
