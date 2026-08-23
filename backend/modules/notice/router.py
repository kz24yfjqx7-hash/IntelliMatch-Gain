"""站内消息（铃铛）路由。

消息是「写给某个 DID 的」，所以这里没有任何按角色开的后门：
每个接口都只操作 principal.did 自己的消息，管理员也看不到别人的铃铛。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.deps import current_user, get_db, pagination
from core.middleware import Principal
from core.response import PageQuery, ok, page
from modules.notice import service
from modules.notice.schema import MarkReadRequest

router = APIRouter(tags=["站内消息"])


@router.get("/notices", summary="我的消息列表")
def list_notices(
    pg: PageQuery = Depends(pagination),
    status: str | None = Query(None, pattern="^(unread|read)$"),
    category: str | None = Query(None, max_length=32),
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    if not principal.did:
        # 没绑 DID 的账号收不到站内信，返回空列表而不是报错：铃铛是附属功能，不该挡住页面
        return ok(page([], 0, pg.page, pg.size))
    items, total = service.list_notices(
        db, principal.did, pg.page, pg.size, status=status, category=category)
    return ok(page(items, total, pg.page, pg.size))


@router.get("/notices/unread-count", summary="未读消息数（铃铛角标）")
def unread_count(
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    return ok({"count": service.unread_count(db, principal.did) if principal.did else 0})


@router.post("/notices/read", summary="标记已读")
def mark_read(
    body: MarkReadRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    if not principal.did:
        return ok({"updated": 0})
    updated = (service.mark_all_read(db, principal.did) if not body.ids
               else service.mark_read(db, principal.did, body.ids))
    return ok({"updated": updated, "count": service.unread_count(db, principal.did)})


@router.post("/notices/read-all", summary="全部标记已读")
def mark_all_read(
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
):
    if not principal.did:
        return ok({"updated": 0})
    return ok({"updated": service.mark_all_read(db, principal.did), "count": 0})
