"""认证与密码学工具：JWT 签发校验、密码哈希、traceId 生成。"""
import secrets
from datetime import timedelta

import jwt
from passlib.context import CryptContext

from core.config import settings
from core.exceptions import UnauthorizedError
from core.response import now_cst

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------- 密码

def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd_ctx.verify(plain, hashed)
    except Exception:  # noqa: BLE001  哈希串损坏时按验证失败处理
        return False


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
