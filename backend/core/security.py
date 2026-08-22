"""认证与密码学工具：JWT 签发校验、密码哈希、traceId 生成。"""
import secrets
from datetime import timedelta

import jwt
from passlib.context import CryptContext

from core.config import settings
from core.exceptions import UnauthorizedError
from core.response import now_cst

# bcrypt 代价因子（B-022）。sql/02_seed.sql 里的六个演示账号哈希是 cost=10 生成的，
# 而 passlib 的默认值是 12——不显式钉住的话，新建用户的哈希比演示账号贵 4 倍，
# 同样一次登录耗时从 ~63ms 涨到 ~253ms（本机实测）。这里统一到 10：
# 校验时代价因子是从哈希串自身（`$2b$NN$`）读出来的，所以老哈希照样验得过，只影响新生成的。
BCRYPT_ROUNDS = 10

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=BCRYPT_ROUNDS)


# ---------------------------------------------------------------- 密码

def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码。

    这是 CPU 密集操作，但**不需要**再往线程池里塞一层：
    /auth/login 是同步路由（`def`，不是 `async def`），FastAPI 已经把它整个丢进
    anyio 的工作线程里跑，事件循环不会被 bcrypt 堵住；且 bcrypt 4.x 是 Rust 实现，
    计算期间释放 GIL——本机 30 个线程并发校验总墙钟 340ms，没有排队现象。
    30 并发登录的尾延迟真正卡在数据库连接池上，见 core/database.py 的说明。
    """
    try:
        return _pwd_ctx.verify(plain, hashed)
    except Exception:  # noqa: BLE001  哈希串损坏时按验证失败处理
        return False


async def verify_password_async(plain: str, hashed: str) -> bool:
    """给异步调用方用的版本：把 bcrypt 挪进线程池，别在事件循环里算。

    目前登录走的是同步路由，用不到这个入口；留给以后可能出现的 `async def` 鉴权路径，
    免得有人在协程里直接调 verify_password 把整个循环堵死。
    """
    import anyio.to_thread

    return await anyio.to_thread.run_sync(verify_password, plain, hashed)


# ---------------------------------------------------------------- traceId

def new_trace_id() -> str:
    """契约 1.2：格式 tr-YYYYMMDD-<8位hex>。"""
    return f"tr-{now_cst():%Y%m%d}-{secrets.token_hex(4)}"


# ---------------------------------------------------------------- JWT

def create_token(*, user_id: int, username: str, roles: list[str], did: str | None) -> tuple[str, int]:
    """签发 JWT。契约 1.3：payload 含 sub / username / roles / did / exp，有效期 8 小时。

    返回 (token, expires_in_seconds)。
    """
    expire_seconds = settings.JWT_EXPIRE_SECONDS
    issued_at = now_cst()
    payload = {
        "sub": str(user_id),
        "username": username,
        "roles": roles,
        "did": did,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(seconds=expire_seconds)).timestamp()),
        # jti 用于登出黑名单
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, expire_seconds


def decode_token(token: str) -> dict:
    """校验并解析 JWT。失败一律抛 UnauthorizedError（code 1002）。"""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Token 无效") from exc


# ---------------------------------------------------------------- 登出黑名单

_BLACKLIST_PREFIX = "auth:blacklist:"


def revoke_token(payload: dict) -> None:
    """登出：把 jti 拉黑到原 exp 时刻为止。"""
    from core import redis_client

    jti = payload.get("jti")
    if not jti:
        return
    ttl = max(1, int(payload.get("exp", 0) - now_cst().timestamp()))
    redis_client.safe_set(f"{_BLACKLIST_PREFIX}{jti}", "1", ex=ttl)


def is_revoked(payload: dict) -> bool:
    from core import redis_client

    jti = payload.get("jti")
    if not jti:
        return False
    return redis_client.safe_exists(f"{_BLACKLIST_PREFIX}{jti}")
