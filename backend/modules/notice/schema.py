"""站内消息请求体。"""
from pydantic import BaseModel, Field


class MarkReadRequest(BaseModel):
    """批量标记已读。ids 为空表示「全部已读」，与 /notices/read-all 等价。"""

    ids: list[int] = Field(default_factory=list, max_length=200)
