"""站内消息（铃铛）的 ORM 模型。

一条 notice = 一个收件人 + 一件事。按收件人 DID 扇出而不是按角色存一条，
理由是「已读」是每个人各自的状态：管理员甲点掉了，管理员乙的铃铛不该跟着清零。
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, BigIntPK
from core.response import now_naive  # 时间列统一走应用侧东八区时钟，见 B-023


class SysNotice(Base):
    __tablename__ = "sys_notice"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    # 收件人。DID 是平台内主体的唯一标识，用户名会改、DID 不会
    recipient_did: Mapped[str] = mapped_column(String(128))
    recipient_name: Mapped[str | None] = mapped_column(String(128))
    # permission_apply（有人申请，发给审批人） / permission_result（审批结果，发给申请人）
    # / permission_revoke（授权被回收，发给被回收人）
    category: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), default="info")   # info | success | warning
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str | None] = mapped_column(String(512))
    # 点击消息跳到哪，前端路由，例如 /permission?tab=applications&id=12
    link: Mapped[str | None] = mapped_column(String(255))
    ref_type: Mapped[str | None] = mapped_column(String(32))         # permission_application / perm_grant
    ref_id: Mapped[str | None] = mapped_column(String(64))
    # 发起人，用于「谁申请的 / 谁审批的」
    actor_did: Mapped[str | None] = mapped_column(String(128))
    actor_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="unread")  # unread | read
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())

    __table_args__ = (
        # 铃铛的两个高频查询：某人的未读数、某人的消息列表倒序
        Index("idx_recipient_status", "recipient_did", "status"),
        Index("idx_recipient_created", "recipient_did", "created_at"),
    )
