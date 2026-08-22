"""数据资产请求体。契约 2.4。"""
from pydantic import BaseModel, Field

DATA_TYPE = "^(pv|wind|storage|load|dispatch)$"
LEVEL = "^(L1|L2|L3|L4)$"


class AssetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    dataType: str = Field(pattern=DATA_TYPE)
    sourceDid: str = Field(max_length=128)
    level: str | None = Field(default=None, pattern=LEVEL,
                              description="留空则调用算法服务自动分级")
    payload: dict = Field(description="原始数据，存 MySQL；链上只存 SM3 摘要")
    description: str | None = Field(default=None, max_length=512)
    recordCount: int = Field(default=1, ge=1)


class ClassifyRecord(BaseModel):
    dataType: str = Field(pattern=DATA_TYPE)
    fields: list[str] = Field(default_factory=list)
    freq: str = Field(default="minute")
    volume: int = Field(default=1, ge=0)


class ClassifyRequest(BaseModel):
    records: list[ClassifyRecord] = Field(min_length=1, max_length=200)
