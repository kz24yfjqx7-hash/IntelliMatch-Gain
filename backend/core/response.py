"""统一响应包装。契约 1.1 / 1.4 / 1.5。

所有 /api/v1/** 接口（含错误）必须返回 {code, message, data, traceId}。
"""
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi.responses import JSONResponse

from core.exceptions import CODE_TO_HTTP, ErrorCode

# 契约 1.5：所有时间字段为 ISO 8601 带时区字符串，项目统一用东八区
CST = timezone(timedelta(hours=8))


def now_cst() -> datetime:
    """当前东八区时间，**秒级精度**。

    微秒必须在这里就抹掉，不能留到落库时再说。原因是所有 DATETIME 列都没有小数秒位，
    MySQL 写入时会把小数秒**四舍五入**（不是截断）——`01:23:45.678` 进库变成 `01:23:46`。
    存证块的哈希 = SM3(prevHash + payloadHash + 时间戳)，写入时用的是 45 秒，
    校验时从库里读回来是 46 秒，于是链校验必然失败，而且是随机一半的概率失败。

    这个坑在 SQLite 上完全看不见（它原样保存微秒），只有真 MySQL 才现形。
    """
    return datetime.now(CST).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    """datetime -> '2026-08-17T14:23:05+08:00'。库里取出的 naive 时间按东八区解释。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.isoformat(timespec="seconds")


def get_trace_id() -> str:
    """取当前请求的 traceId（由中间件写入 contextvar）。"""
    from core.middleware import current_trace_id

    return current_trace_id.get()


def ok(data: Any = None, message: str = "ok") -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"code": ErrorCode.OK, "message": message, "data": data, "traceId": get_trace_id()},
    )


def fail(code: int, message: str, data: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=CODE_TO_HTTP.get(code, 500),
        content={"code": code, "message": message, "data": data, "traceId": get_trace_id()},
    )


def page(items: list, total: int, page_no: int, size: int) -> dict:
    """契约 1.4：分页响应的 data 固定为 {items, total, page, size}。"""
    return {"items": items, "total": total, "page": page_no, "size": size}


class PageQuery:
    """分页参数。契约 1.4：page 从 1 开始默认 1，size 默认 20 最大 200。

    树莓派内存约束：size 上限强制 200，禁止一次性全表加载。
    """

    def __init__(self, page: int = 1, size: int = 20):
        self.page = max(1, page)
        self.size = min(max(1, size), 200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size
