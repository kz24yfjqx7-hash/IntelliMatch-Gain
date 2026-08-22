"""存证记录 ORM 模型。"""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, BigIntPK


class ChainEvidence(Base):
    __tablename__ = "chain_evidence"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    evidence_id: Mapped[str] = mapped_column(String(64), unique=True)
    category: Mapped[str] = mapped_column(String(16))
    ref_id: Mapped[str] = mapped_column(String(64))
    actor_did: Mapped[str | None] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(80))
    prev_hash: Mapped[str] = mapped_column(String(80))
    block_hash: Mapped[str] = mapped_column(String(80))
    block_height: Mapped[int] = mapped_column(BigInteger, unique=True)
    tx_id: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    payload_snapshot: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
