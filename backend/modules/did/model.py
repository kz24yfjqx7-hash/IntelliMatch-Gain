"""DID 身份与密钥的 ORM 模型。"""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, BigIntPK
# 时间列一律走应用侧东八区时钟：数据库服务器时区可能是 UTC，
# 若 created_at 用 now_cst()、updated_at 用 CURRENT_TIMESTAMP，两者会差 8 小时（B-023）
from core.response import now_naive


class DidIdentity(Base):
    __tablename__ = "did_identity"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    did: Mapped[str] = mapped_column(String(128), unique=True)
    subject_type: Mapped[str] = mapped_column(String(16))
    subject_name: Mapped[str] = mapped_column(String(128))
    org_name: Mapped[str | None] = mapped_column(String(128))
    controller_did: Mapped[str | None] = mapped_column(String(128))
    did_document: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="active")
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, onupdate=now_naive, server_default=func.now())


class DidKey(Base):
    __tablename__ = "did_key"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    did: Mapped[str] = mapped_column(String(128))
    algorithm: Mapped[str] = mapped_column(String(8), default="SM2")
    public_key: Mapped[str] = mapped_column(String(512))
    key_hash: Mapped[str] = mapped_column(String(80))
    private_key_enc: Mapped[str | None] = mapped_column(String(1024))
    custody: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    purpose: Mapped[str] = mapped_column(String(32), default="sign")
    bound_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
    expire_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, onupdate=now_naive, server_default=func.now())


class DidKeyRotationLog(Base):
    __tablename__ = "did_key_rotation_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    did: Mapped[str] = mapped_column(String(128))
    key_id: Mapped[int] = mapped_column(BigInteger)
    old_public_key: Mapped[str | None] = mapped_column(String(512))
    new_public_key: Mapped[str] = mapped_column(String(512))
    old_version: Mapped[int] = mapped_column(Integer, default=0)
    new_version: Mapped[int] = mapped_column(Integer, default=1)
    reason: Mapped[str | None] = mapped_column(String(255))
    operator_did: Mapped[str | None] = mapped_column(String(128))
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())


class DidDeviceBinding(Base):
    __tablename__ = "did_device_binding"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    did: Mapped[str] = mapped_column(String(128))
    device_id: Mapped[str] = mapped_column(String(64), unique=True)
    device_model: Mapped[str | None] = mapped_column(String(64))
    bound_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
    last_online_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="bound")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
