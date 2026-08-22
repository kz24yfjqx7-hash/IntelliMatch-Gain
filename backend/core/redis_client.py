"""Redis 客户端。

用途：
1. JWT 登出黑名单
2. 风控规则的滑动窗口计数（R01~R05）
3. 高危审计日志副本，供实时告警面板秒级读取
4. DID nonce 防重放

Redis 不可用时平台必须仍能运行（降级为「无风控计数、无黑名单」），
因此所有调用都包在 safe_* 里，不让缓存层拖垮主流程。
"""
import logging

import redis

from core.config import settings

logger = logging.getLogger(__name__)

_pool = redis.ConnectionPool.from_url(
    settings.redis_url,
    decode_responses=True,
    max_connections=16,
    socket_connect_timeout=2,
    socket_timeout=2,
)

client = redis.Redis(connection_pool=_pool)


def ping() -> bool:
    try:
        return bool(client.ping())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 不可用：%s", exc)
        return False


def safe_incr_window(key: str, window_seconds: int) -> int:
    """滑动窗口计数：第一次写入时设置过期时间，返回当前窗口内累计次数。

    Redis 挂掉时返回 0，等价于「不触发风控」，保证主流程不受影响。
    """
    try:
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = pipe.execute()
        return int(count)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 计数失败 key=%s：%s", key, exc)
        return 0


def safe_set(key: str, value: str, ex: int | None = None) -> bool:
    try:
        client.set(key, value, ex=ex)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 写入失败 key=%s：%s", key, exc)
        return False


def safe_get(key: str) -> str | None:
    try:
        return client.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 读取失败 key=%s：%s", key, exc)
        return None


def safe_delete(key: str) -> bool:
    """删除一个键。缓存失效（冻结身份、停用账号）必须走它，不能直接碰 client。"""
    try:
        return bool(client.delete(key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 删除失败 key=%s：%s", key, exc)
        return False


def safe_exists(key: str) -> bool:
    try:
        return bool(client.exists(key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 判存失败 key=%s：%s", key, exc)
        return False


def safe_lpush_trim(key: str, value: str, max_len: int = 500) -> None:
    """高危日志环形缓冲，只保留最近 max_len 条，避免树莓派内存被吃满。"""
    try:
        pipe = client.pipeline()
        pipe.lpush(key, value)
        pipe.ltrim(key, 0, max_len - 1)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 列表写入失败 key=%s：%s", key, exc)


def safe_lrange(key: str, start: int = 0, end: int = -1) -> list[str]:
    try:
        return client.lrange(key, start, end)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 列表读取失败 key=%s：%s", key, exc)
        return []
