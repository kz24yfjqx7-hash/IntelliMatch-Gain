"""权限中心请求体。契约 2.5。"""
from pydantic import BaseModel, Field

RESOURCE_PATTERN = "^(asset|model|dispatch|evidence|algo|user)$"
ACTION_PATTERN = "^(read|write|execute|issue|export|manage)$"


class PermissionCheckRequest(BaseModel):
    did: str | None = Field(default=None, description="留空则校验当前登录主体")
    resourceType: str = Field(pattern=RESOURCE_PATTERN)
    resourceId: str | None = None
    action: str = Field(pattern=ACTION_PATTERN)


class RoleGrantItem(BaseModel):
    resourceType: str = Field(pattern=RESOURCE_PATTERN)
    action: str = Field(pattern=ACTION_PATTERN)
    scope: str = Field(default="all", pattern="^(all|own)$")


class RoleCreateRequest(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern="^[a-z][a-z0-9_]*$")
    name: str = Field(max_length=64)
    description: str | None = Field(default=None, max_length=255)
    grants: list[RoleGrantItem] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    grants: list[RoleGrantItem] | None = None


class PermissionApplyRequest(BaseModel):
    resourceType: str = Field(pattern="^(asset|model|dispatch|evidence|algo)$")
    resourceId: str = Field(max_length=64)
    action: str = Field(pattern="^(read|write|execute|issue|export)$")
    reason: str = Field(min_length=1, max_length=512, description="申请理由，会进审计留痕")
    expireAt: str | None = Field(default=None, description="ISO 8601；留空默认 30 天")


class ApprovalRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=512)
    expireAt: str | None = None


class RevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=255, description="回收原因，必填")
