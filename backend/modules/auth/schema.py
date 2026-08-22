"""认证与用户管理的请求体定义。字段名严格照 contract/API-CONTRACT.md 2.1。"""
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    realName: str = Field(max_length=64)
    orgName: str | None = Field(default=None, max_length=128)
    roles: list[str] = Field(min_length=1, description="角色码列表")
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=128)
    bindDid: bool = Field(default=True, description="是否同时签发一个 user 类型的 DID")


class UserUpdateRequest(BaseModel):
    password: str | None = Field(default=None, min_length=6, max_length=128)
    realName: str | None = Field(default=None, max_length=64)
    orgName: str | None = Field(default=None, max_length=128)
    roles: list[str] | None = None
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
