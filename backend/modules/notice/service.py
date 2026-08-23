"""站内消息服务。

对外只有两类入口：
  * 写：notify_dids() / notify_roles()，业务模块在事务提交后调用
  * 读：list_notices() / unread_count() / mark_read() / mark_all_read()

**写入口永不抛异常**。消息是通知手段而不是业务结果，一条铃铛发不出去
不能把权限申请这种已经落库上链的动作回滚掉，所以异常一律吞掉并记日志。
WebSocket 推送同理（ws.manager.push_to_dids 内部已保证不抛）。
"""
import logging

from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from core.middleware import current_trace_id
from core.response import iso, now_cst
from modules.notice.model import SysNotice

logger = logging.getLogger(__name__)


def _table_missing(exc: Exception) -> bool:
    """sys_notice 表不存在（存量库未跑迁移）→ MySQL 1146。"""
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ()) or ()
    return bool(args) and args[0] == 1146

# 一次扇出的收件人上限。防止角色被误配成几千人时一条申请写爆库
MAX_FANOUT = 200


def _to_item(n: SysNotice) -> dict:
    return {
        "id": n.id,
        "category": n.category,
        "level": n.level,
        "title": n.title,
        "content": n.content,
        "link": n.link,
        "refType": n.ref_type,
        "refId": n.ref_id,
        "actorDid": n.actor_did,
        "actorName": n.actor_name,
        "status": n.status,
        "readAt": iso(n.read_at),
        "createdAt": iso(n.created_at),
        "traceId": n.trace_id,
    }


# ---------------------------------------------------------------- 写

def notify_dids(db: Session, dids, *, category: str, title: str, content: str | None = None,
                level: str = "info", link: str | None = None, ref_type: str | None = None,
                ref_id: str | None = None, actor_did: str | None = None,
                actor_name: str | None = None, names: dict | None = None) -> list[dict]:
    """给一组 DID 各写一条消息并即时推送。返回写入的消息（推送用）。

    调用方应当**在自己的事务提交之后**调用：消息里的链接指向的业务数据必须已经可查，
    否则用户点开铃铛会看到一条指向不存在记录的消息。
    """
    items: list[dict] = []
    try:
        targets = [d for d in dict.fromkeys(dids or []) if d][:MAX_FANOUT]
        if not targets:
            return []
        trace_id = current_trace_id.get()
        rows = [
            SysNotice(
                recipient_did=d, recipient_name=(names or {}).get(d),
                category=category, level=level, title=title, content=content,
                link=link, ref_type=ref_type, ref_id=ref_id,
                actor_did=actor_did, actor_name=actor_name,
                status="unread", trace_id=trace_id,
            )
            for d in targets
        ]
        db.add_all(rows)
        db.commit()
        # 推送要按收件人定向，所以把 (did, item) 一起带出去；对外只返回 item
        delivery = [(r.recipient_did, _to_item(r)) for r in rows]
        items = [item for _, item in delivery]
    except Exception as exc:  # noqa: BLE001  通知失败绝不影响业务
        logger.warning("站内消息写入失败 category=%s：%s", category, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []

    _push(delivery)
    return items


def notify_roles(db: Session, roles, *, exclude_did: str | None = None, **kwargs) -> list[dict]:
    """给持有指定角色的所有在册用户发消息（例如把权限申请发给全部 sys_admin）。"""
    try:
        dids, names = _dids_of_roles(db, roles, exclude_did=exclude_did)
    except Exception as exc:  # noqa: BLE001
        logger.warning("按角色查收件人失败 roles=%s：%s", roles, exc)
        return []
    return notify_dids(db, dids, names=names, **kwargs)


def _dids_of_roles(db: Session, roles, *, exclude_did: str | None = None):
    """角色 → 有 DID 且在用的用户。没绑 DID 的账号收不到站内信，这是 DID 体系的既定前提。"""
    from modules.auth.model import SysUser, SysUserRole

    rows = db.execute(
        select(SysUser.did, SysUser.real_name, SysUser.username)
        .join(SysUserRole, SysUserRole.user_id == SysUser.id)
        .where(SysUserRole.role_code.in_(list(roles)),
               SysUser.status == "active",
               SysUser.did.is_not(None))
    ).all()
    dids, names = [], {}
    for did, real_name, username in rows:
        if not did or did == exclude_did or did in names:
            continue
        dids.append(did)
        names[did] = real_name or username
    return dids, names


def _push(delivery: list[tuple[str, dict]]) -> None:
    """把刚写入的消息按收件人定向推 WebSocket。失败不影响已落库的消息。"""
    if not delivery:
        return
    try:
        from ws.manager import push_to_dids
    except Exception:  # noqa: BLE001  WebSocket 模块未装载（如单测）时静默跳过
        return
    for did, item in delivery:
        push_to_dids([did], "notice", item)


# ---------------------------------------------------------------- 读

def list_notices(db: Session, did: str, page: int, size: int, *,
                 status: str | None = None, category: str | None = None):
    stmt = select(SysNotice).where(SysNotice.recipient_did == did)
    if status:
        stmt = stmt.where(SysNotice.status == status)
    if category:
        stmt = stmt.where(SysNotice.category == category)

    try:
        total = db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()
        rows = db.execute(
            stmt.order_by(SysNotice.id.desc()).offset((page - 1) * size).limit(size)
        ).scalars().all()
    except (ProgrammingError, OperationalError) as exc:
        # 铃铛是附属功能，不该因存量库缺 sys_notice 表（未跑迁移）就把首页/页头打成 500。
        # 降级返回空，记一条 warning 提示去执行 sql/05_migrate_20260823.sql。
        if not _table_missing(exc):
            raise
        db.rollback()
        logger.warning("sys_notice 表不存在，站内消息降级为空——请执行 backend/sql/05_migrate_20260823.sql")
        return [], 0
    return [_to_item(r) for r in rows], int(total)


def unread_count(db: Session, did: str) -> int:
    if not did:
        return 0
    try:
        return int(db.execute(
            select(func.count()).select_from(SysNotice)
            .where(SysNotice.recipient_did == did, SysNotice.status == "unread")
        ).scalar_one())
    except (ProgrammingError, OperationalError) as exc:
        if not _table_missing(exc):
            raise
        db.rollback()
        logger.warning("sys_notice 表不存在，未读数降级为 0")
        return 0


def mark_read(db: Session, did: str, ids: list[int]) -> int:
    """只能标记自己的消息。where 里带 recipient_did 就是越权防线，别去掉。"""
    if not ids:
        return 0
    n = db.execute(
        update(SysNotice)
        .where(SysNotice.recipient_did == did,
               SysNotice.id.in_(ids),
               SysNotice.status == "unread")
        .values(status="read", read_at=now_cst().replace(tzinfo=None))
    ).rowcount
    db.commit()
    return int(n or 0)


def mark_all_read(db: Session, did: str) -> int:
    n = db.execute(
        update(SysNotice)
        .where(SysNotice.recipient_did == did, SysNotice.status == "unread")
        .values(status="read", read_at=now_cst().replace(tzinfo=None))
    ).rowcount
    db.commit()
    return int(n or 0)
