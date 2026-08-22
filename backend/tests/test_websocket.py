"""WebSocket 测试。契约 2.13。

重点验两件事：
1. 鉴权失败必须以 4001 关闭，不能让未登录的连接挂在那里收广播
2. 同步的业务代码能把消息推到异步的 WebSocket 通道（这是本项目最容易写错的一处）
"""
import json

import pytest
from starlette.websockets import WebSocketDisconnect


def _token(client, username="admin", password="admin123"):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return r.json()["data"]["token"]


def test_没有token直接拒绝(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws") as ws:
            ws.receive_text()
    assert exc.value.code == 4001


def test_伪造token直接拒绝(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws?token=eyJhbGciOiJIUzI1NiJ9.forged.sig") as ws:
            ws.receive_text()
    assert exc.value.code == 4001


def test_B024_鉴权失败先完成握手再以4001关闭(client):
    """B-024：accept 之前 close 会被 ASGI 服务器降级成 HTTP 403，客户端拿不到 4001。

    这里断言握手确实完成了（拿得到 websocket.accept），随后才是 4001 关闭帧。
    """
    with client.websocket_connect("/ws?token=bad") as ws:
        # TestClient 在 accept 之前 close 时会抛 WebSocketDenialResponse，
        # 能走到这里就说明服务端先 accept 了
        msg = ws.receive()
    assert msg["type"] == "websocket.close"
    assert msg["code"] == 4001


def test_连上先收到欢迎日志(client):
    token = _token(client)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "log"
        assert "已接入实时通道" in msg["payload"]["content"]
        assert msg["payload"]["module"] == "ws"


def test_心跳(client):
    token = _token(client)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_text()                      # 欢迎消息
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text())["type"] == "pong"


def test_同步业务代码能推消息到通道(client, login):
    """签发一个 DID 会写存证，存证服务应当推 evidence_written 出来。"""
    token = _token(client)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_text()                      # 欢迎消息

        client.post("/api/v1/did/register", headers={"Authorization": f"Bearer {token}"},
                    json={"subjectType": "device", "subjectName": "WS 推送测试设备",
                          "custody": True})

        # 一次注册会产生若干条消息（存证 + 日志），找出 evidence_written 那条
        found = None
        for _ in range(6):
            msg = json.loads(ws.receive_text())
            if msg["type"] == "evidence_written":
                found = msg
                break
        assert found is not None, "没有收到 evidence_written 消息"
        assert found["payload"]["category"] == "identity"
        assert found["payload"]["evidenceId"].startswith("ev-")
        assert isinstance(found["payload"]["blockHeight"], int)
        assert found["traceId"].startswith("tr-")


def test_风控告警实时推送(client, login, monkeypatch):
    """答辩演示第 5 步：越权被拦 → 审计告警实时弹出。"""
    from core import redis_client

    store, counters = {}, {}
    monkeypatch.setattr(redis_client, "safe_set",
                        lambda k, v, ex=None: store.__setitem__(k, v) or True)
    monkeypatch.setattr(redis_client, "safe_get", lambda k: store.get(k))
    monkeypatch.setattr(redis_client, "safe_exists", lambda k: k in store)
    monkeypatch.setattr(redis_client, "safe_incr_window",
                        lambda k, w: counters.__setitem__(k, counters.get(k, 0) + 1)
                        or counters[k])

    token = _token(client)
    vpp = {"Authorization": f"Bearer {_token(client, 'vpp', 'vpp123')}"}

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_text()

        for _ in range(3):
            client.get("/api/v1/users", headers=vpp)

        alert = None
        for _ in range(12):
            msg = json.loads(ws.receive_text())
            if msg["type"] == "audit_alert":
                alert = msg
                break
        assert alert is not None, "越权三次没有推出告警"
        assert alert["payload"]["ruleCode"] == "R01_UNAUTHORIZED"
        assert alert["payload"]["riskLevel"] == "high"
        assert alert["payload"]["hitCount"] >= 3


def test_未知消息类型不会被推出去(client):
    from ws import manager

    # 不在契约六种类型里的一律丢弃，避免前端收到没法处理的消息
    manager.push("不存在的类型", {"x": 1})     # 不抛异常即为通过


def test_没有连接时推送是空操作():
    from ws import manager

    assert manager.manager.count == 0
    manager.push("log", {"level": "info", "module": "test", "content": "x"})


# ---------------------------------------------------------------- B-021 node_status 周期广播

def test_B021_广播间隔是契约要求的5秒():
    from ws import manager

    assert manager.NODE_STATUS_INTERVAL == 5.0


def test_B021_没有连接时不打库也不改数(db):
    """空转保护：没有客户端连着，一轮广播既不开会话也不动 node_info。"""
    import asyncio

    from modules.node.model import NodeInfo
    from ws import manager

    calls = []
    original = manager._collect_node_status
    manager._collect_node_status = lambda: calls.append(1) or []
    try:
        before = {n.id: (n.pv_output, n.soc) for n in db.query(NodeInfo).all()}
        assert manager.manager.count == 0
        assert asyncio.run(manager.broadcast_node_status_once()) == 0
        assert calls == [], "没有连接时不应该去查数据库"
        db.expire_all()
        after = {n.id: (n.pv_output, n.soc) for n in db.query(NodeInfo).all()}
        assert before == after
    finally:
        manager._collect_node_status = original


def test_B021_指标步进落库且数值真的在变(db):
    """推出去的值就是库里存着的值：先 tick 再读库，两者必须一致且与上一轮不同。"""
    from modules.node.model import NodeInfo
    from modules.node.service import tick_node_metrics

    first = tick_node_metrics(db)
    assert first, "种子里应当有节点"
    second = tick_node_metrics(db)

    online = [p for p in second if p["status"] != "offline"]
    assert online, "种子里应当有在线节点"

    # 契约 2.13 payload 形状：{nodeId, status, metrics{pvOutput,storageOutput,load,soc}}
    for payload in second:
        assert set(payload) == {"nodeId", "status", "metrics"}
        assert set(payload["metrics"]) == {"pvOutput", "storageOutput", "load", "soc"}
        for value in payload["metrics"].values():
            assert isinstance(value, (int, float))

    before = {p["nodeId"]: p["metrics"] for p in first}
    assert any(before[p["nodeId"]] != p["metrics"] for p in online), "两轮之间数值应当有变化"

    # 广播出去的值 == 数据库里的值
    db.expire_all()
    for payload in online:
        node = db.get(NodeInfo, payload["nodeId"])
        assert float(node.pv_output) == payload["metrics"]["pvOutput"]
        assert float(node.soc) == payload["metrics"]["soc"]
        assert 5.0 <= payload["metrics"]["soc"] <= 100.0
        assert payload["metrics"]["pvOutput"] >= 0


def test_B021_已连接客户端能收到node_status(client):
    """跑一轮真实广播，已连接的客户端必须收到契约形状的 node_status。"""
    import asyncio

    from ws import manager

    token = _token(client)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_text()                       # 欢迎日志
        # 复用 ws 端点里记下的应用事件循环，直接触发一轮周期广播
        sent = asyncio.run_coroutine_threadsafe(
            manager.broadcast_node_status_once(), manager._loop).result(timeout=15)
        assert sent >= 1

        got = []
        for _ in range(sent):
            msg = json.loads(ws.receive_text())
            if msg["type"] == "node_status":
                got.append(msg)
        assert got, "没有收到 node_status"
        first = got[0]
        assert set(first) == {"type", "ts", "traceId", "payload"}
        assert set(first["payload"]) == {"nodeId", "status", "metrics"}
        assert set(first["payload"]["metrics"]) == {"pvOutput", "storageOutput", "load", "soc"}


def test_B021_后台任务能随应用生命周期启动与关闭():
    import asyncio

    from ws import manager

    async def scenario():
        task = manager.start_node_status_task()
        assert task is not None and not task.done()
        assert manager.start_node_status_task() is task, "重复启动应当复用同一个任务"
        await manager.stop_node_status_task()
        assert task.done()
        assert manager._node_task is None
        # 关掉之后再关一次不应该报错
        await manager.stop_node_status_task()

    asyncio.run(scenario())
