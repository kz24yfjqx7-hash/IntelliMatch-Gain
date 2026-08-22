"""WebSocket 测试：鉴权、ping/pong、六类消息格式、FL/调度触发后是否收到推送。运行：backend/.venv/bin/python qa/api-tests/ws_test.py"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import websockets
from lib import Recorder, WS_URL, req, token, uniq, iso_ok

OUT = os.path.join(os.path.dirname(__file__), "results")
R = Recorder(os.path.join(OUT, "ws.json"))
C = R.case
TYPES = {"node_status", "fl_progress", "dispatch_progress", "audit_alert", "log", "evidence_written"}


async def close_code(url):
    try:
        async with websockets.connect(url, open_timeout=5) as ws:
            await asyncio.wait_for(ws.recv(), timeout=3)
            return "open"
    except websockets.exceptions.ConnectionClosed as e:
        return e.code
    except Exception as e:  # noqa: BLE001
        sc = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
        return f"http{sc}" if sc else type(e).__name__


async def main():
    c = await close_code(WS_URL)
    C("API-WS-01", "契约 2.13 鉴权失败关闭码 4001", "ws://…/ws 不带 token", "WS 关闭码 4001（契约 2.13）", [(c == 4001, f"实际握手被拒 {c}（后端在 accept 前 close(4001)，Starlette 转为 HTTP 403，浏览器侧 onclose.code=1006）")], {"close": c})
    c = await close_code(WS_URL + "?token=bad.token.x")
    C("API-WS-02", "契约 2.13 鉴权", "token 伪造", "WS 关闭码 4001；至少不得建立连接", [(c != "open", "连接被接受"), (c == 4001, f"实际 {c}")], {"close": c})
    r, b = req("POST", "/auth/login", None, {"username": "grid", "password": "grid123"})
    t = b["data"]["token"]
    req("POST", "/auth/logout", None, raw_token=t)
    c = await close_code(WS_URL + "?token=" + t)
    C("API-WS-03", "安全·登出后 token 不能建 WS", "用已登出 token 连接", "WS 关闭码 4001；至少不得建立连接", [(c != "open", "连接被接受"), (c == 4001, f"实际 {c}")], {"close": c})

    msgs = []
    async with websockets.connect(WS_URL + "?token=" + token("admin"), open_timeout=5) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        msgs.append(first)
        C("API-WS-04", "契约 2.13 消息格式 {type,ts,traceId,payload}", "连接后首条消息", "type=log 且四字段齐全 ts ISO8601",
          [(set(first.keys()) == {"type", "ts", "traceId", "payload"}, f"keys={list(first.keys())}"), (first.get("type") == "log", f"type={first.get('type')}"), iso_ok(first.get("ts"))], first)
        await ws.send(json.dumps({"type": "ping"}))
        pong = None
        t0 = time.time()
        while time.time() - t0 < 5:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if m.get("type") == "pong":
                pong = m; break
            msgs.append(m)
        C("API-WS-05", "契约 2.13 ping/pong", "发送 {type:ping}", "收到 {type:pong}", [(pong is not None, "无 pong")], pong)

        async def collect(seconds):
            end = time.time() + seconds
            while time.time() < end:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time())))
                    msgs.append(m)
                except asyncio.TimeoutError:
                    break

        # 触发：注册 DID → evidence_written；vpp 越权 3 次 → audit_alert/log
        tid = f"tr-20260822-{uniq('')[-8:]}"
        req("POST", "/did/register", "admin", {"subjectType": "device", "subjectName": uniq("test-ws-dev")}, trace=tid)
        uname = uniq("test-ws-user")
        req("POST", "/users", "admin", {"username": uname, "password": "Ws-123456", "realName": "WS越权测试", "roles": ["vpp_operator"]})
        rr, bb = req("POST", "/auth/login", None, {"username": uname, "password": "Ws-123456"})
        ut = bb["data"]["token"]
        for _ in range(3):
            req("GET", "/users", None, raw_token=ut)
        await collect(4)
        ev = [m for m in msgs if m.get("type") == "evidence_written"]
        C("API-WS-06", "契约 2.13 evidence_written {evidenceId,category,blockHeight}；traceId 沿用请求", "注册 DID 后监听", "收到 evidence_written，payload 字段齐全，traceId==请求 X-Trace-Id",
          [(bool(ev), "无 evidence_written"), (bool(ev) and {"evidenceId", "category", "blockHeight"} <= set(ev[0]["payload"].keys()), str(ev[:1])), (any(m.get("traceId") == tid for m in ev), "traceId 未沿用")], ev[:1])
        al = [m for m in msgs if m.get("type") == "audit_alert"]
        C("API-WS-07", "契约 2.13 audit_alert {alertId,ruleCode,riskLevel,message,actorDid} / 验收 6", "vpp 连续越权后监听", "收到 audit_alert R01 字段齐全",
          [(bool(al), "无 audit_alert（R01 窗口内可能已告警过）"), (bool(al) and {"alertId", "ruleCode", "riskLevel", "message", "actorDid"} <= set(al[0]["payload"].keys()), str(al[:1]))], al[:1])
        lg = [m for m in msgs if m.get("type") == "log" and m is not first]
        C("API-WS-08", "契约 2.13 log {level,module,content,traceId}", "高危操作滚动日志", "收到 log 且字段齐全", [(bool(lg), "无 log"), (bool(lg) and {"level", "module", "content", "traceId"} <= set(lg[0]["payload"].keys()), str(lg[:1]))], lg[:1])
        # FL
        r, b = req("POST", "/fl/tasks", "admin", {"name": uniq("test-ws-fl"), "nodeIds": ["Node-A", "Node-B"], "rounds": 2})
        fl = b["data"]["id"]
        req("POST", f"/fl/tasks/{fl}/start", "admin")
        await collect(25)
        fp = [m for m in msgs if m.get("type") == "fl_progress" and m["payload"].get("taskId") == fl]
        C("API-WS-09", "契约 2.13 fl_progress {taskId,round,totalRounds,loss,acc,compressionRatio,epsilonSpent} / 验收 7", "启动 2 轮 FL 后监听", "每轮一条，字段齐全",
          [(len(fp) >= 2, f"收到 {len(fp)} 条"), (bool(fp) and {"taskId", "round", "totalRounds", "loss", "acc", "compressionRatio", "epsilonSpent"} <= set(fp[0]["payload"].keys()), str(fp[:1]))], fp[:1])
        # dispatch
        r, b = req("POST", "/dispatch/tasks", "admin", {"name": uniq("test-ws-dp"), "nodeIds": ["Node-B"]})
        dp = b["data"]["id"]
        req("POST", f"/dispatch/tasks/{dp}/run", "admin")
        req("POST", f"/dispatch/tasks/{dp}/issue", "admin", {})
        req("POST", f"/dispatch/tasks/{dp}/ack", "admin", {"nodeId": "Node-B", "accepted": True})
        await collect(3)
        dpm = [m for m in msgs if m.get("type") == "dispatch_progress" and m["payload"].get("taskId") == dp]
        stages = [m["payload"].get("stage") for m in dpm]
        C("API-WS-10", "契约 2.13 dispatch_progress stage aggregating/computing/explaining/issued/acked", "run→issue→ack 后监听", "stage 依次包含 computing…issued、acked",
          [(bool(dpm), "无 dispatch_progress"), (set(stages) <= {"aggregating", "computing", "explaining", "issued", "acked"}, f"stages={stages}"), ("issued" in stages and "acked" in stages, f"stages={stages}")], stages)
        await collect(6)
        ns = [m for m in msgs if m.get("type") == "node_status"]
        C("API-WS-11a", "契约 2.13 node_status 周期推送（5 秒一次）", "静候 ≥6 秒不触发任何节点操作", "收到周期性 node_status", [(bool(ns), "6 秒内无 node_status（后端仅在设备上线时推送，无周期任务）")], {"n": len(ns)})
        ctx = json.load(open(os.path.join(OUT, "ctx.json"))) if os.path.exists(os.path.join(OUT, "ctx.json")) else {}
        ed = ctx.get("edge_d") or {}
        if ed.get("privateKey"):
            from lib import gm
            nonce = uniq("test-ws-nonce")
            req("POST", "/nodes/Node-D/online", "admin", {"did": ed["did"], "nonce": nonce, "signature": gm.sign(nonce, ed["privateKey"], ed["publicKey"])})
            await collect(3)
            ns = [m for m in msgs if m.get("type") == "node_status"]
        C("API-WS-11", "契约 2.13 node_status {nodeId,status,metrics}", "设备上线后监听", "收到 node_status 字段齐全",
          [(bool(ns), "无 node_status"), (bool(ns) and {"nodeId", "status", "metrics"} <= set(ns[0]["payload"].keys()), str(ns[:1]))], ns[:1])
        bad = [m for m in msgs if m.get("type") not in TYPES | {"pong"} or set(m.keys()) != {"type", "ts", "traceId", "payload"}]
        C("API-WS-12", "契约 2.13 仅六类消息且格式统一", f"检查全部 {len(msgs)} 条收到的消息", "type 均在六类内，四字段齐全", [(not bad, str(bad[:2]))], {"types": sorted({m.get('type') for m in msgs})})
    R.save()

asyncio.run(main())
