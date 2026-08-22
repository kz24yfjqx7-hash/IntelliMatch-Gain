"""审计告警 ORM 模型。

审计日志表 audit_log_YYYYMM 是按月动态创建的，没法用固定的 ORM 模型表达，
所以走 core/audit/service.py 里的原生 SQL；这里只放结构固定的告警表。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, BigIntPK


class AuditAlert(Base):
    __tablename__ = "audit_alert"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(String(64), unique=True)
    rule_code: Mapped[str] = mapped_column(String(32))
    rule_name: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(16), default="high")
    message: Mapped[str] = mapped_column(String(512))
    actor_did: Mapped[str | None] = mapped_column(String(128))
    actor_name: Mapped[str | None] = mapped_column(String(128))
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="open")
    acked_by: Mapped[str | None] = mapped_column(String(128))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime)
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
