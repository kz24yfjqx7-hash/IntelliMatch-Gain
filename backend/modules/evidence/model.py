"""存证记录 ORM 模型。"""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, BigIntPK
from core.response import now_naive  # 时间列统一走应用侧东八区时钟，见 B-023


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
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())


class ChainEvidenceBackup(Base):
    """篡改演示的原始快照备份（B-001 / B-025）。

    原来备份只写 Redis：按 evidenceId 覆盖、还带 TTL，连续演示两次或 Redis 一清，
    原始快照就永久丢了，链停在 broken 只能重建库。落库之后：
    - 同一条存证已有备份时**不覆盖**，保住的永远是最初那份；
    - restore 成功后删除该行，下一轮演示重新记录。

    注意 sql/03_migrate_20260822.sql 里的建表语句在存量库上可能还没执行，
    所以 service 层对这张表的所有读写都要能捕获异常并降级回 Redis。
    """

    __tablename__ = "chain_evidence_backup"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    evidence_id: Mapped[str] = mapped_column(String(64), unique=True)
    payload_snapshot: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(80))
    block_hash: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
