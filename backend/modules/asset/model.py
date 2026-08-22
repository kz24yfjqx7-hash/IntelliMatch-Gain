"""能源数据资产 ORM 模型。"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, BigIntPK


class EnergyAsset(Base):
    __tablename__ = "energy_asset"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    data_type: Mapped[str] = mapped_column(String(16))
    source_did: Mapped[str] = mapped_column(String(128))
    owner_did: Mapped[str | None] = mapped_column(String(128))
    level: Mapped[str] = mapped_column(String(4), default="L2")
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(512))
    record_count: Mapped[int] = mapped_column(Integer, default=1)
    auth_status: Mapped[str] = mapped_column(String(16), default="unauthorized")
    classify_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 3))
    classify_reason: Mapped[str | None] = mapped_column(String(512))
    chain_tx_id: Mapped[str | None] = mapped_column(String(64))
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EnergyAssetLineage(Base):
    __tablename__ = "energy_asset_lineage"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(16))
    actor_did: Mapped[str | None] = mapped_column(String(128))
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str | None] = mapped_column(String(80))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
