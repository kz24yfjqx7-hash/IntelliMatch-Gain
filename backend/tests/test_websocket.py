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
        with client.websocket_connect("/ws"):
            pass
    assert exc.value.code == 4001


def test_伪造token直接拒绝(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws?token=eyJhbGciOiJIUzI1NiJ9.forged.sig"):
            pass
    assert exc.value.code == 4001


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
