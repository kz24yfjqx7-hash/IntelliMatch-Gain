"""业务异常。错误码严格对齐 contract/API-CONTRACT.md 1.1 节，不得自创。"""


class ErrorCode:
    OK = 0
    PARAM_ERROR = 1001        # HTTP 400
    UNAUTHORIZED = 1002       # HTTP 401 未登录 / Token 失效
    NO_PERMISSION = 1003      # HTTP 403 RBAC 拒绝
    DID_INVALID = 1004        # HTTP 403 DID 校验失败 / 签名无效
    NOT_FOUND = 1005          # HTTP 404
    CONFLICT = 1006           # HTTP 409
    RATE_LIMITED = 1007       # HTTP 429
    ALGO_UNAVAILABLE = 2001   # HTTP 502
    CHAIN_UNAVAILABLE = 2002  # HTTP 502
    INTERNAL_ERROR = 5000     # HTTP 500


# 业务码 -> HTTP 状态码，契约要求「HTTP 状态码始终与 code 语义一致」
CODE_TO_HTTP = {
    ErrorCode.OK: 200,
    ErrorCode.PARAM_ERROR: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.NO_PERMISSION: 403,
    ErrorCode.DID_INVALID: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.ALGO_UNAVAILABLE: 502,
    ErrorCode.CHAIN_UNAVAILABLE: 502,
    ErrorCode.INTERNAL_ERROR: 500,
}


class BizError(Exception):
    """业务异常基类。抛出后由全局异常处理器包装成统一响应。"""

    code = ErrorCode.INTERNAL_ERROR
    message = "服务器内部错误"

    def __init__(self, message: str | None = None, *, code: int | None = None, data=None):
        self.message = message or self.message
        if code is not None:
            self.code = code
        self.data = data
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return CODE_TO_HTTP.get(self.code, 500)


class ParamError(BizError):
    code = ErrorCode.PARAM_ERROR
    message = "参数错误"


class UnauthorizedError(BizError):
    code = ErrorCode.UNAUTHORIZED
    message = "未登录或登录已失效"


class NoPermissionError(BizError):
    code = ErrorCode.NO_PERMISSION
    message = "无权限"


class DidInvalidError(BizError):
    code = ErrorCode.DID_INVALID
    message = "DID 校验失败或签名无效"


class NotFoundError(BizError):
    code = ErrorCode.NOT_FOUND
    message = "资源不存在"


class ConflictError(BizError):
    code = ErrorCode.CONFLICT
    message = "状态冲突"


class RateLimitedError(BizError):
    code = ErrorCode.RATE_LIMITED
    message = "触发风控限流"


class AlgoUnavailableError(BizError):
    code = ErrorCode.ALGO_UNAVAILABLE
    message = "算法服务不可用"


class ChainUnavailableError(BizError):
    code = ErrorCode.CHAIN_UNAVAILABLE
    message = "链服务不可用"
