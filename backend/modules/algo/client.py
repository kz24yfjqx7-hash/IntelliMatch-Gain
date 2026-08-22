"""算法服务客户端。

算法本身由乙实现，跑在独立进程 algo-service:8100。后端只做代理，
但代理层要负责三件事：透传 traceId、超时兜底、把算法服务的原始 JSON
翻译成契约第二部分定义的响应结构。

契约第三部分规定算法服务**不使用**统一包装，直接返回业务 JSON，
失败返回 4xx/5xx + {"error": "..."}。
"""
import logging

import httpx

from core.config import settings
from core.exceptions import AlgoUnavailableError
from core.middleware import current_trace_id

logger = logging.getLogger(__name__)

BASE_PATH = "/algo/v1"


def _url(path: str) -> str:
    return f"{settings.ALGO_SERVICE_URL.rstrip('/')}{BASE_PATH}{path}"


def _headers() -> dict:
    # 契约 1.2：调用算法服务必须透传 X-Trace-Id，这是全链路追踪的基础
    return {"X-Trace-Id": current_trace_id.get(), "Content-Type": "application/json"}


def call(method: str, path: str, *, json: dict | None = None,
         timeout: float | None = None, raise_on_error: bool = True) -> dict | None:
    """调用算法服务。

    raise_on_error=False 时失败返回 None，供「算法不可用也要能继续」的场景使用
    （比如资产登记时的自动分级，算法挂了就退回规则化分级，不能因此拒绝登记）。
    """
    url = _url(path)
    try:
        with httpx.Client(timeout=timeout or settings.ALGO_TIMEOUT) as client:
            resp = client.request(method, url, json=json, headers=_headers())
        if resp.status_code >= 400:
            detail = _error_detail(resp)
            logger.warning("算法服务返回 %s：%s %s", resp.status_code, url, detail)
            if raise_on_error:
                raise AlgoUnavailableError(f"算法服务返回错误：{detail}")
            return None
        return resp.json()
    except AlgoUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001  连接失败 / 超时 / JSON 解析失败
        logger.warning("算法服务不可达 %s：%s", url, exc)
        if raise_on_error:
            raise AlgoUnavailableError(f"算法服务不可用：{exc}") from exc
        return None


def health() -> dict | None:
    return call("GET", "/health", timeout=3.0, raise_on_error=False)


def classify(records: list[dict], *, raise_on_error: bool = True) -> dict | None:
    return call("POST", "/classify", json={"records": records}, raise_on_error=raise_on_error)


def _error_detail(resp: httpx.Response) -> str:
    try:
        return resp.json().get("error", resp.text[:200])
    except Exception:  # noqa: BLE001
        return resp.text[:200]
