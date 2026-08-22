"""WebSocket 连接管理与广播。契约 2.13。

六种消息类型：node_status / fl_progress / dispatch_progress / audit_alert / log / evidence_written。
统一格式 {"type": ..., "ts": ..., "traceId": ..., "payload": {...}}。

难点在于**业务代码是同步的，而 WebSocket 广播是异步的**。
解决办法是启动时把事件循环存下来，同步侧调用 push() 时用
run_coroutine_threadsafe 把广播任务丢回循环里执行，不阻塞业务线程。
"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from core.response import iso, now_cst
from core.security import decode_token, is_revoked

logger = logging.getLogger(__name__)

# 契约 2.13 定义的六种消息类型
MESSAGE_TYPES = frozenset({
    "node_status", "fl_progress", "dispatch_progress", "audit_alert", "log", "evidence_written",
})

# 契约 2.13：node_status「节点状态与实时指标，每 5 秒推送」
NODE_STATUS_INTERVAL = 5.0

_loop: asyncio.AbstractEventLoop | None = None
_node_task: asyncio.Task | None = None


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[WebSocket, dict] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, principal: dict) -> None:
        await ws.accept()
        async with self._lock:
            self._connections[ws] = principal
        logger.info("WebSocket 已连接：%s（当前 %d 个连接）",
                    principal.get("username"), len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            principal = self._connections.pop(ws, None)
        if principal:
            logger.info("WebSocket 已断开：%s（剩余 %d 个连接）",
                        principal.get("username"), len(self._connections))

    async def broadcast(self, message: dict) -> None:
        """向所有连接推送。发送失败的连接直接剔除，不让死连接拖慢广播。"""
        if not self._connections:
            return
        payload = json.dumps(message, ensure_ascii=False, default=_json_default)
        dead = []
        for ws in list(self._connections):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


def _json_default(obj):
    if isinstance(obj, datetime):
        return iso(obj)
    from decimal import Decimal

    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)


manager = ConnectionManager()


def set_loop(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """启动时记录事件循环，供同步业务代码回推消息。"""
    global _loop
    try:
        _loop = loop or asyncio.get_running_loop()
    except RuntimeError:
        _loop = None


def push(message_type: str, payload: dict, trace_id: str | None = None) -> None:
    """同步侧的推送入口。**永远不抛异常**——推送失败不能影响业务。"""
    if message_type not in MESSAGE_TYPES:
        logger.warning("未知的 WebSocket 消息类型：%s", message_type)
        return
    if manager.count == 0:
        return

    if trace_id is None:
        from core.middleware import current_trace_id

        trace_id = current_trace_id.get()

    message = {
        "type": message_type,
        "ts": iso(now_cst()),
        "traceId": trace_id,
        "payload": payload,
    }

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    try:
        if running is not None:
            running.create_task(manager.broadcast(message))
        elif _loop is not None:
            asyncio.run_coroutine_threadsafe(manager.broadcast(message), _loop)
    except Exception as exc:  # noqa: BLE001
        logger.warning("WebSocket 推送失败 type=%s：%s", message_type, exc)


# ---------------------------------------------------------------- node_status 周期广播（契约 2.13）

def _collect_node_status() -> list[dict]:
    """在工作线程里跑：步进一次实时指标并取回全部节点的 payload。

    SessionLocal 必须在函数内取——测试夹具会把 core.database.SessionLocal 换成 SQLite 工厂，
    模块级 import 会把旧对象钉死。
    """
    from core.database import SessionLocal
    from modules.node.service import tick_node_metrics

    with SessionLocal() as db:
        return tick_node_metrics(db)


async def broadcast_node_status_once() -> int:
    """广播一轮 node_status，返回推出去的消息条数。

    没有客户端连着时直接返回 0：**不开数据库连接、不改数据**，避免空转打库。
    """
    if manager.count == 0:
        return 0
    payloads = await asyncio.to_thread(_collect_node_status)
    for payload in payloads:
        await manager.broadcast({
            "type": "node_status",
            "ts": iso(now_cst()),
            "traceId": None,
            "payload": payload,
        })
    return len(payloads)


async def _node_status_loop() -> None:
    logger.info("node_status 周期广播已启动，间隔 %.1f 秒", NODE_STATUS_INTERVAL)
    try:
        while True:
            await asyncio.sleep(NODE_STATUS_INTERVAL)
            try:
                await broadcast_node_status_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  单次失败不能让整个任务退出
                logger.warning("node_status 广播失败：%s", exc)
    except asyncio.CancelledError:
        logger.info("node_status 周期广播已停止")
        raise


def start_node_status_task() -> asyncio.Task | None:
    """应用启动时调用。已经在跑就不重复起。"""
    global _node_task
    if _node_task is not None and not _node_task.done():
        return _node_task
    try:
        _node_task = asyncio.get_running_loop().create_task(_node_status_loop())
    except RuntimeError:       # 没有事件循环（例如同步脚本里 import），静默跳过
        _node_task = None
    return _node_task


async def stop_node_status_task() -> None:
    """应用关闭时调用，等任务真正结束再返回。"""
    global _node_task
    task, _node_task = _node_task, None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


def register_ws_endpoint(app: FastAPI) -> None:
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """契约 2.13：token 走 query 参数，鉴权失败立即以 4001 关闭。

        注意必须**先 accept 再 close(4001)**：在 accept 之前 close，ASGI 服务器
        （uvicorn/hypercorn）只能把它降级成 HTTP 403 握手拒绝，浏览器端拿不到 4001，
        没法区分「token 失效」和「网络不可达」。多一次握手换客户端一个明确的关闭码。
        """
        token = websocket.query_params.get("token")
        if not token:
            await websocket.accept()
            await websocket.close(code=4001, reason="缺少 token")
            return
        try:
            claims = decode_token(token)
            if is_revoked(claims):
                raise ValueError("token 已失效")
        except Exception:  # noqa: BLE001
            await websocket.accept()
            await websocket.close(code=4001, reason="鉴权失败")
            return

        principal = {
            "userId": claims.get("sub"),
            "username": claims.get("username"),
            "roles": claims.get("roles", []),
            "did": claims.get("did"),
        }
        await manager.connect(websocket, principal)
        set_loop()

        try:
            # 连上先推一条欢迎日志，前端能立刻确认通道是活的
            await websocket.send_text(json.dumps({
                "type": "log",
                "ts": iso(now_cst()),
                "traceId": None,
                "payload": {"level": "info", "module": "ws",
                            "content": f"{principal['username']} 已接入实时通道",
                            "traceId": None},
            }, ensure_ascii=False))

            while True:
                raw = await websocket.receive_text()
                # 客户端心跳，契约要求 30 秒一次
                if raw and "ping" in raw:
                    await websocket.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            await manager.disconnect(websocket)
        except Exception as exc:  # noqa: BLE001
            logger.warning("WebSocket 异常断开：%s", exc)
            await manager.disconnect(websocket)

    # 周期广播任务挂在应用生命周期上。写在这里而不是 main.py，
    # 是为了让 WebSocket 的全部逻辑（含后台任务）留在本模块内，main.py 只管注册。
    @app.on_event("startup")
    async def _ws_startup():           # pragma: no cover - 由 uvicorn 触发
        set_loop()
        start_node_status_task()

    @app.on_event("shutdown")
    async def _ws_shutdown():          # pragma: no cover - 由 uvicorn 触发
        await stop_node_status_task()
