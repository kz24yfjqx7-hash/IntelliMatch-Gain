"""用户与角色的 ORM 模型。表结构见 contract/DB-SCHEMA.md 与 backend/sql/01_schema.sql。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, BigIntPK
from core.response import now_naive  # 时间列统一走应用侧东八区时钟，见 B-023


class SysUser(Base):
    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    real_name: Mapped[str] = mapped_column(String(64))
    org_name: Mapped[str | None] = mapped_column(String(128))
    did: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, onupdate=now_naive, server_default=func.now())


class SysRole(Base):
    __tablename__ = "sys_role"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(255))
    is_builtin: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, onupdate=now_naive, server_default=func.now())


class SysUserRole(Base):
    __tablename__ = "sys_user_role"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    role_code: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_naive, server_default=func.now())
