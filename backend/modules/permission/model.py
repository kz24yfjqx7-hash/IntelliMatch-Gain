"""权限中心的 ORM 模型：权限矩阵、申请、授权、变更留痕。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, BigIntPK


class SysRolePermission(Base):
    """RBAC 四层模型的落点：主体(角色) - 资源 - 操作 - 数据范围。"""

    __tablename__ = "sys_role_permission"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    role_code: Mapped[str] = mapped_column(String(32))
    resource_type: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(8), default="all")  # all | own
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PermApplication(Base):
    __tablename__ = "perm_application"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    applicant_did: Mapped[str] = mapped_column(String(128))
    applicant_name: Mapped[str | None] = mapped_column(String(128))
    resource_type: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    expire_at: Mapped[datetime | None] = mapped_column(DateTime)
    approver_did: Mapped[str | None] = mapped_column(String(128))
    approver_name: Mapped[str | None] = mapped_column(String(128))
    approve_reason: Mapped[str | None] = mapped_column(String(512))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PermGrant(Base):
    __tablename__ = "perm_grant"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    application_id: Mapped[int | None] = mapped_column(BigInteger)
    grantee_did: Mapped[str] = mapped_column(String(128))
    grantee_name: Mapped[str | None] = mapped_column(String(128))
    resource_type: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="active")
    granted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expire_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoke_reason: Mapped[str | None] = mapped_column(String(255))
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PermChangeLog(Base):
    """权限变更留痕，同时供 R03 高频权限变更规则统计。"""

    __tablename__ = "perm_change_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    target_did: Mapped[str] = mapped_column(String(128))
    change_type: Mapped[str] = mapped_column(String(16))
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str | None] = mapped_column(String(32))
    operator_did: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[str | None] = mapped_column(String(512))
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
