"""DID 与密钥管理的请求体。契约 2.2 / 2.3。"""
from pydantic import BaseModel, Field

SUBJECT_TYPE = "^(user|device|org|edge)$"


class DidRegisterRequest(BaseModel):
    subjectType: str = Field(pattern=SUBJECT_TYPE)
    subjectName: str = Field(min_length=1, max_length=128)
    orgName: str | None = Field(default=None, max_length=128)
    controllerDid: str | None = Field(default=None, max_length=128)
    metadata: dict | None = None
    custody: bool = Field(
        default=True,
        description="true=私钥由平台 SM4 加密托管；false=私钥仅本次返回，平台不留存",
    )


class DidStatusRequest(BaseModel):
    action: str = Field(pattern="^(freeze|unfreeze|revoke)$")
    reason: str | None = Field(default=None, max_length=255)


class DidRotateKeyRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)
    custody: bool = True


class DidVerifyRequest(BaseModel):
    did: str = Field(max_length=128)
    message: str
    signature: str = Field(max_length=256)


class DidResolveRequest(BaseModel):
    dids: list[str] = Field(min_length=1, max_length=100)


class KeyCreateRequest(BaseModel):
    did: str = Field(max_length=128)
    algorithm: str = Field(default="SM2", pattern="^(SM2|ECC|RSA)$")
    purpose: str = Field(default="sign", pattern="^(sign|encrypt)$")
    custody: bool = True
    expireDays: int = Field(default=730, ge=1, le=3650)


class KeyStatusRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)
