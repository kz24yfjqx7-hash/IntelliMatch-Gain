"""算法代理层请求体。契约 2.9 ~ 2.12。"""
from pydantic import BaseModel, Field


class DpConfig(BaseModel):
    enabled: bool = True
    epsilon: float = Field(default=1.0, gt=0, le=10)
    delta: float = Field(default=1e-5, gt=0, lt=1)


class TopkConfig(BaseModel):
    enabled: bool = True
    ratio: float = Field(default=0.1, gt=0, le=1)


class FlTaskCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    nodeIds: list[str] = Field(min_length=1, max_length=32)
    rounds: int = Field(default=10, ge=1, le=100)
    dp: DpConfig = Field(default_factory=DpConfig)
    topk: TopkConfig = Field(default_factory=TopkConfig)


class DispatchTaskCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    nodeIds: list[str] = Field(min_length=1, max_length=32)
    timeWindow: str | None = Field(default=None, max_length=64)


class DispatchIssueRequest(BaseModel):
    signature: str | None = Field(
        default=None, max_length=256,
        description="对 signPayload 的 SM2 签名；留空则用平台托管私钥代签",
    )


class DispatchAckRequest(BaseModel):
    nodeId: str = Field(max_length=32)
    accepted: bool = True
    detail: str | None = Field(default=None, max_length=255)


class AiAnalyzeRequest(BaseModel):
    scene: str = Field(pattern="^(dispatch|risk|data|qa|audit)$")
    context: dict = Field(default_factory=dict)
    question: str = Field(min_length=1, max_length=1024)


class RiskAssessRequest(BaseModel):
    nodeId: str = Field(max_length=32)
    features: dict = Field(default_factory=dict)
