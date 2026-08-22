"""能源可信数据空间平台 · 后端安全内核 入口。

职责：注册横切中间件链、全局异常处理器、各业务模块路由、WebSocket 端点。
"""
import importlib
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from core.config import settings
from core.database import wait_for_db
from core.exceptions import BizError, ErrorCode
from core.middleware import AuthMiddleware, TraceMiddleware, current_trace_id
from core.response import iso, now_cst

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("backend")

app = FastAPI(
    title="能源可信数据空间平台 · 后端安全内核",
    description="可信身份 / 数据可信存证 / 细粒度权限控制 / 安全审计 + 算法代理",
    version="1.0.0",
    docs_url="/docs",
)

# 中间件注册顺序与执行顺序相反：后注册的先执行。
# 期望执行顺序是 TraceMiddleware -> AuthMiddleware -> 业务，所以先注册 Auth 再注册 Trace。
app.add_middleware(AuthMiddleware)
app.add_middleware(TraceMiddleware)


# ---------------------------------------------------------------- 全局异常处理
# 契约 1.1：所有 /api/v1/** 响应（包括错误）都必须是统一包装

def _envelope(code: int, message: str, data=None, status: int = 200,
              request: Request | None = None) -> JSONResponse:
    # B-014：未捕获异常的处理器跑在 ServerErrorMiddleware 里（比 TraceMiddleware 更外层），
    # 优先从 request.state 取 traceId，contextvar 只作兜底，绝不能返回全零串。
    trace_id = getattr(getattr(request, "state", None), "trace_id", None) or current_trace_id.get()
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "data": data, "traceId": trace_id},
    )


@app.exception_handler(BizError)
async def biz_error_handler(request: Request, exc: BizError):
    logger.warning("业务异常 %s %s code=%s msg=%s", request.method, request.url.path, exc.code, exc.message)
    await _feed_risk_rules(request, exc)
    return _envelope(exc.code, exc.message, exc.data, exc.http_status, request)


async def _feed_risk_rules(request: Request, exc: BizError) -> None:
    """所有被拒绝的请求在这里汇总喂给风控引擎。

    放在全局处理器而不是各个装饰器里，是为了保证 @require_permission、
    require_roles 依赖、service 层直接抛的 1003，三条路径一个都不漏，
    同时又不会出现同一次拒绝被重复计数。
    """
    if exc.code not in (ErrorCode.NO_PERMISSION, ErrorCode.DID_INVALID):
        return
    try:
        import asyncio

        from core.middleware import current_principal
        from modules.audit.rules import fire
    except ImportError:
        return

    principal = current_principal.get()
    path = f"{request.method} {request.url.path}"
    if exc.code == ErrorCode.NO_PERMISSION:
        await asyncio.to_thread(
            fire, "R01_UNAUTHORIZED", principal=principal,
            message=f"越权访问被拦截：{path}（{exc.message}）",
        )
    else:
        await asyncio.to_thread(
            fire, "R02_ABNORMAL_DID", principal=principal, immediate=True,
            message=f"身份校验失败：{path}（{exc.message}）",
        )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(x) for x in first.get("loc", [])[1:])
    message = f"参数错误：{loc} {first.get('msg', '')}".strip()
    return _envelope(ErrorCode.PARAM_ERROR, message, None, 400, request)


# HTTP 层异常（404 / 405 / 401 …）也必须套统一包装，
# 否则前端会在少数路径上拿到 {"detail": "..."} 这种不一致的结构。
_HTTP_TO_CODE = {
    400: ErrorCode.PARAM_ERROR,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.NO_PERMISSION,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.PARAM_ERROR,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.PARAM_ERROR,
    429: ErrorCode.RATE_LIMITED,
    502: ErrorCode.ALGO_UNAVAILABLE,
}


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    if not request.url.path.startswith("/api/v1"):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    code = _HTTP_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    message = {
        404: "接口或资源不存在",
        405: "请求方法不被允许",
        401: "未登录或登录已失效",
    }.get(exc.status_code, str(exc.detail))
    return _envelope(code, message, None, exc.status_code, request)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception("未捕获异常 %s %s", request.method, request.url.path)
    return _envelope(ErrorCode.INTERNAL_ERROR, "服务器内部错误", None, 500, request)


# ---------------------------------------------------------------- 路由注册
# 按开发顺序逐步补齐；尚未实现的模块自动跳过，保证任何阶段都能起服务。

_MODULES = [
    "auth",
    "did",
    "key",
    "permission",
    "asset",
    "evidence",
    "audit",
    "node",
    "algo",
]


def register_routers() -> None:
    for name in _MODULES:
        try:
            mod = importlib.import_module(f"modules.{name}.router")
        except ModuleNotFoundError:
            logger.info("模块 %s 尚未实现，跳过", name)
            continue
        app.include_router(mod.router, prefix="/api/v1")
        logger.info("已注册模块路由：%s", name)


def register_websocket() -> None:
    try:
        from ws.manager import register_ws_endpoint
    except ModuleNotFoundError:
        logger.info("WebSocket 模块尚未实现，跳过")
        return
    register_ws_endpoint(app)
    logger.info("已注册 WebSocket 端点：/ws")


register_routers()
register_websocket()


# ---------------------------------------------------------------- 健康检查

@app.get("/health", tags=["运维"])
def health():
    """供 docker healthcheck 与安装脚本自检使用，不走统一包装、不需要登录。"""
    from sqlalchemy import text

    from core import redis_client
    from core.database import engine

    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("健康检查：MySQL 不可用 %s", exc)

    redis_ok = redis_client.ping()
    return {
        "status": "ok" if db_ok else "degraded",
        "service": "backend",
        "version": "1.0.0",
        "time": iso(now_cst()),
        "deps": {"mysql": db_ok, "redis": redis_ok},
    }


# ---------------------------------------------------------------- 生命周期

@app.on_event("startup")
def on_startup():
    logger.info("后端安全内核启动中……")

    # 记录事件循环，同步的业务代码要靠它把 WebSocket 消息推回去
    try:
        from ws.manager import set_loop

        set_loop()
    except ModuleNotFoundError:
        pass

    wait_for_db()
    # 审计日志按月分表：启动时确保当月表存在（审计模块实现后生效）
    try:
        from modules.audit.service import ensure_current_month_table

        ensure_current_month_table()
    except (ModuleNotFoundError, ImportError):
        pass
    logger.info("后端安全内核已就绪，监听端口 %s", settings.BACKEND_PORT)


# ---------------------------------------------------------------- OpenAPI 文档
# 鉴权走 AuthMiddleware 直接读 Authorization 头，没有用 FastAPI 的 Security 依赖，
# 于是 /docs 上不会出现 Authorize 按钮，只能手工拼 curl。
# 这里把 bearer 方案补进文档，纯文档元数据，不改任何接口行为，
# 但答辩现场能直接在 Swagger 上点着演示，省掉一堆复制粘贴。
_PUBLIC_PATHS = {"/api/v1/auth/login"}


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(title=app.title, version=app.version,
                         description=app.description, routes=app.routes)
    schema.setdefault("components", {})["securitySchemes"] = {
        "BearerAuth": {
            "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
            "description": "先调 POST /api/v1/auth/login 拿 data.token，把它粘进来即可。",
        }
    }
    # 除了登录本身，所有接口都要带 token
    for 路径, 方法们 in schema["paths"].items():
        if 路径 in _PUBLIC_PATHS:
            continue
        for 操作 in 方法们.values():
            if isinstance(操作, dict):
                操作.setdefault("security", [{"BearerAuth": []}])
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
