"""节点与历史指标 ORM 模型。字段与 raspi/public/data/mock.json 对齐。"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, BigIntPK


class NodeInfo(Base):
    __tablename__ = "node_info"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="offline")
    model: Mapped[str] = mapped_column(String(32))
    did: Mapped[str | None] = mapped_column(String(128))
    location: Mapped[str | None] = mapped_column(String(128))
    capacity_kw: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    pv_output: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    storage_output: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    load_kw: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    soc: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NodeMetric(Base):
    __tablename__ = "node_metric"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(DateTime)
    pv_output: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    storage_output: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    load_kw: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    soc: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    price: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
