"""算法业务表 ORM 模型。

算法本身跑在乙的 algo-service 里，本模块只负责代理调用、结果落库、
上链存证、审计埋点和 WebSocket 推进度。
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Float, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, BigIntPK
from core.response import now_naive  # 时间列统一走应用侧东八区时钟，见 B-023


class AlgoFlTask(Base):
    __tablename__ = "algo_fl_task"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    node_ids: Mapped[list] = mapped_column(JSON)
    rounds: Mapped[int] = mapped_column(Integer, default=10)
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="created")
    dp_enabled: Mapped[int] = mapped_column(Integer, default=1)
    dp_epsilon: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    dp_delta: Mapped[float | None] = mapped_column(Float)
    dp_epsilon_spent: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=0)
    topk_enabled: Mapped[int] = mapped_column(Integer, default=1)
    topk_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 3))
    compression_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    model_version: Mapped[str | None] = mapped_column(String(32))
    creator_did: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, onupdate=now_naive, server_default=func.now())


class AlgoFlRound(Base):
    __tablename__ = "algo_fl_round"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32))
    round: Mapped[int] = mapped_column(Integer)
    # B-011：原 DECIMAL(10,6)，算法返回的 loss 是 kW² 量级 MSE，≥10000 时溢出，
    # 导致整轮训练进度既不落库也不上链（日志里刷 562 次 Out of range）。改 DOUBLE。
    loss: Mapped[float | None] = mapped_column(Float)
    acc: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    compression_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    epsilon_spent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    gradient_hash: Mapped[str | None] = mapped_column(String(80))
    node_contributions: Mapped[list | None] = mapped_column(JSON)
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())


class AlgoModelVersion(Base):
    __tablename__ = "algo_model_version"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    task_id: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(128))
    metrics: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    publisher_did: Mapped[str | None] = mapped_column(String(128))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    model_hash: Mapped[str | None] = mapped_column(String(80))
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())


class AlgoDispatchTask(Base):
    __tablename__ = "algo_dispatch_task"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    time_window: Mapped[str | None] = mapped_column(String(64))
    node_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="created")
    strategy: Mapped[dict | None] = mapped_column(JSON)
    total_reward: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    q_table: Mapped[list | None] = mapped_column(JSON)
    explanation: Mapped[str | None] = mapped_column(Text)
    explanation_source: Mapped[str] = mapped_column(String(8), default="rule")
    issued: Mapped[int] = mapped_column(Integer, default=0)
    command_id: Mapped[str | None] = mapped_column(String(32))
    signer_did: Mapped[str | None] = mapped_column(String(128))
    signature: Mapped[str | None] = mapped_column(String(256))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime)
    ack_status: Mapped[str] = mapped_column(String(8), default="none")
    ack_detail: Mapped[list | None] = mapped_column(JSON)
    creator_did: Mapped[str | None] = mapped_column(String(128))
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, onupdate=now_naive, server_default=func.now())


class AlgoAiAnalysis(Base):
    __tablename__ = "algo_ai_analysis"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    scene: Mapped[str] = mapped_column(String(16))
    context: Mapped[dict | None] = mapped_column(JSON)
    question: Mapped[str | None] = mapped_column(String(1024))
    answer: Mapped[str | None] = mapped_column(Text)
    reasoning: Mapped[list | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(8), default="rule")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    actor_did: Mapped[str | None] = mapped_column(String(128))
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())


class AlgoRiskAssessment(Base):
    __tablename__ = "algo_risk_assessment"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(32))
    risk_score: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    level: Mapped[str] = mapped_column(String(16))
    features: Mapped[dict | None] = mapped_column(JSON)
    factors: Mapped[list | None] = mapped_column(JSON)
    suggestion: Mapped[str | None] = mapped_column(String(512))
    actor_did: Mapped[str | None] = mapped_column(String(128))
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
