"""
对抗/边界运行时行为（test-algo）：并发、任务字典上限、X-Trace-Id、DQN 确定性与约束、DeepSeek 降级与缓存自愈。
"""
import json
import threading
import time

import pytest

from conftest import PREFIX, start_job, wait_job

FOUR = [{"id": "Node-A"}, {"id": "Node-B"}, {"id": "Node-C"}, {"id": "Node-D"}]


# ---------------- 并发 / 取消
def test_concurrent_jobs_and_cancel(client):
    ids = [f"adv-conc-{time.time_ns()}-{i}" for i in range(10)]
    codes = []
    ths = [threading.Thread(target=lambda j: codes.append(start_job(client, j, rounds=30, nodes=FOUR, dp={"enabled": True}, topk={"enabled": True}).status_code), args=(j,)) for j in ids]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert all(c == 200 for c in codes), codes
    # 训练中并发 cancel 前 5 个，同时大量轮询
    lat = []

    def poll():
        for _ in range(30):
            t0 = time.perf_counter()
            assert client.get(f"{PREFIX}/fl/jobs/{ids[0]}").status_code == 200
            lat.append(time.perf_counter() - t0)

    ps = [threading.Thread(target=poll) for _ in range(5)]
    [t.start() for t in ps]
    cs = [threading.Thread(target=lambda j: client.post(f"{PREFIX}/fl/jobs/{j}/cancel"), args=(j,)) for j in ids[:5]]
    [t.start() for t in cs]
    [t.join() for t in cs]
    [t.join() for t in ps]
    assert max(lat) < 2.0, max(lat)
    for j in ids[:5]:
        assert wait_job(client, j)["status"] == "cancelled"
    for j in ids[5:]:
        assert wait_job(client, j)["status"] == "success"
    # 取消后线程必须退出
    import main

    for j in ids[:5]:
        th = main.JOBS[j].thread
        th.join(timeout=5)
        assert not th.is_alive()
    # 重复 cancel 幂等；对已 success 任务 cancel 不改状态
    assert client.post(f"{PREFIX}/fl/jobs/{ids[0]}/cancel").json()["status"] == "cancelled"
    assert client.post(f"{PREFIX}/fl/jobs/{ids[9]}/cancel").json()["status"] == "success"
    # 取消后可重提同 id
    assert start_job(client, ids[0], rounds=1, nodes=FOUR[:1]).status_code == 200
    assert wait_job(client, ids[0])["status"] == "success"


def test_immediate_cancel_after_start(client):
    j = f"adv-race-{time.time_ns()}"
    assert start_job(client, j, rounds=50, nodes=FOUR).status_code == 200
    assert client.post(f"{PREFIX}/fl/jobs/{j}/cancel").json()["status"] == "cancelled"
    time.sleep(0.5)
    assert client.get(f"{PREFIX}/fl/jobs/{j}").json()["status"] == "cancelled"


def test_job_registry_is_bounded(client, monkeypatch):
    import config
    import main

    monkeypatch.setattr(config, "FL_MAX_JOBS", 30)
    for i in range(60):
        assert start_job(client, f"adv-cap-{i}", rounds=1, nodes=[{"id": "Node-A", "samples": 16}]).status_code == 200
        wait_job(client, f"adv-cap-{i}")
    assert len(main.JOBS) <= 30
    # 运行中的任务不会被淘汰
    assert client.get(f"{PREFIX}/fl/jobs/adv-cap-59").status_code == 200


def test_max_running_returns_429(client, monkeypatch):
    import config

    monkeypatch.setattr(config, "FL_MAX_RUNNING", 2)
    ids = [f"adv-429-{time.time_ns()}-{i}" for i in range(4)]
    codes = [start_job(client, j, rounds=100, nodes=FOUR, dp={"enabled": True}).status_code for j in ids]
    assert codes.count(429) >= 1 and codes[0] == 200, codes
    r = client.post(f"{PREFIX}/fl/train", json={"jobId": ids[-1] + "x", "rounds": 1, "nodes": FOUR})
    if r.status_code == 429:
        assert "error" in r.json()
    for j in ids:
        client.post(f"{PREFIX}/fl/jobs/{j}/cancel")


# ---------------- X-Trace-Id
def test_trace_id_generated_when_missing_or_invalid(client):
    r = client.get(f"{PREFIX}/health")
    assert r.headers["x-trace-id"].startswith("tr-")
    assert client.get(f"{PREFIX}/health", headers={"X-Trace-Id": "tr-20260821-abcdef01"}).headers["x-trace-id"] == "tr-20260821-abcdef01"
    assert client.get(f"{PREFIX}/health", headers={"X-Trace-Id": "abc-DEF_123.4:5/6"}).headers["x-trace-id"] == "abc-DEF_123.4:5/6"
    for bad in (b"", b"a b", b"x" * 129, "tr-中文".encode("utf-8"), b"tr-\xe9\xff"):
        tid = client.get(f"{PREFIX}/health", headers={"X-Trace-Id": bad}).headers["x-trace-id"]
        assert tid.encode("latin-1", "replace") != bad and tid.startswith("tr-") and len(tid) <= 128, (bad, tid)
    # 400 响应也带 trace id
    r = client.post(f"{PREFIX}/classify", json={"records": []}, headers={"X-Trace-Id": "tr-x1"})
    assert r.status_code == 400 and r.headers["x-trace-id"] == "tr-x1"


# ---------------- DQN 边界 / 确定性
def _dispatch(client, nodes):
    r = client.post(f"{PREFIX}/dqn/dispatch", json={"taskId": "t", "nodes": nodes})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("soc", [0, 19.9, 20, 20.1, 50, 94.9, 95, 95.1, 100])
@pytest.mark.parametrize("pv,load", [(0, 0), (0, 300), (300, 0), (100, 100)])
@pytest.mark.parametrize("price", [0, 0.3, 1.05, 100])
def test_dqn_constraints_hold_on_grid(client, soc, pv, load, price):
    out = _dispatch(client, [{"id": "a", "pv": pv, "load": load, "soc": soc, "price": price, "hour": 12}])
    a = out["actions"][0]
    assert a["action"] in ("charge", "idle", "discharge")
    assert 0 <= a["powerKw"] <= 30
    assert a["action"] != "idle" or a["powerKw"] == 0
    if soc <= 20:
        assert a["action"] != "discharge"
    if soc >= 95:
        assert a["action"] != "charge"
    viol = out["constraintsChecked"]["violations"]
    assert (len(viol) >= 1) == (soc <= 20 or soc >= 95)
    for v in viol:
        assert set(v) == {"nodeId", "constraint", "attempted", "applied", "detail"}
        assert v["constraint"] in ("socMin", "socMax", "maxPowerKw")
    json.dumps(out, allow_nan=False)


@pytest.mark.parametrize("n", [1, 20])
@pytest.mark.parametrize("soc", [20, 95])
def test_dqn_all_nodes_on_boundary(client, n, soc):
    out = _dispatch(client, [{"id": f"n{i}", "pv": 10 * i, "load": 100, "soc": soc, "price": 0.62, "hour": 12} for i in range(n)])
    assert len(out["actions"]) == n == len(out["qTable"])
    assert len(out["constraintsChecked"]["violations"]) == n
    assert all(0 <= a["powerKw"] <= 30 for a in out["actions"])


def test_dqn_deterministic(client):
    nodes = [{"id": f"n{i}", "pv": 3.3 * i, "load": 120 - i, "soc": 30 + 3 * i, "price": 0.62, "hour": 15} for i in range(20)]
    outs = {json.dumps(_dispatch(client, nodes), sort_keys=True) for _ in range(10)}
    assert len(outs) == 1


def test_dqn_dispatcher_sanitizes_direct_input():
    """直接调用（绕过 HTTP 校验）时 1e308/NaN 也不产生 inf/nan"""
    import config
    from algorithms.dqn import DQNDispatcher

    d = DQNDispatcher(config.DQN_MODEL_PATH)
    out = d.dispatch([{"id": "a", "pv": 1e308, "load": float("nan"), "soc": 500, "price": float("inf")}, {"id": "b"}])
    json.dumps(out, allow_nan=False)
    assert all(0 <= a["powerKw"] <= 30 for a in out["actions"])


# ---------------- classify / risk
def test_classify_100_records_fast_and_degenerate(client):
    recs = [{"dataType": "pv", "fields": ["gps"], "freq": "minute", "volume": 100}] * 100
    t0 = time.perf_counter()
    r = client.post(f"{PREFIX}/classify", json={"records": recs})
    assert r.status_code == 200 and time.perf_counter() - t0 < 2.0
    b = r.json()
    assert len(b["results"]) == 100 and len(b["clusterCenters"]) == 3
    assert len({x["level"] for x in b["results"]}) == 1
    for n in (1, 2, 5, 6, 7):
        rr = client.post(f"{PREFIX}/classify", json={"records": recs[:n]}).json()
        assert len(rr["results"]) == n and len(rr["clusterCenters"]) == 3


def test_classify_and_risk_level_monotone_in_score(client):
    import itertools

    recs = [{"dataType": t, "fields": f, "freq": fr, "volume": v} for t, f, fr, v in itertools.product(["pv", "load", "dispatch"], [["gps"], ["power"], []], ["second", "hour", "day"], [0, 1440, 10**6])]
    res = client.post(f"{PREFIX}/classify", json={"records": recs}).json()["results"]
    order = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}
    srt = sorted(res, key=lambda x: x["score"])
    assert all(order[a["level"]] <= order[b["level"]] for a, b in zip(srt, srt[1:]))
    lv = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    pts = []
    for qf, g, ef, er in itertools.product([0, 10, 50], ["second", "hour", "zzz"], [0, 10], [0, 1.0]):
        b = client.post(f"{PREFIX}/risk/assess", json={"nodeId": "n", "features": {"queryFreq": qf, "dataGranularity": g, "exposedFields": ef, "epsilonRemaining": er}}).json()
        pts.append((b["riskScore"], lv[b["level"]]))
    pts.sort()
    assert all(a[1] <= b[1] for a, b in zip(pts, pts[1:]))


# ---------------- DeepSeek：不可达 / 超时 / 格式异常 / 缓存损坏
@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    import config

    p = tmp_path / "cache.json"
    monkeypatch.setattr(config, "DEEPSEEK_CACHE_PATH", p)
    return p


def test_deepseek_unreachable_degrades_fast(client, monkeypatch, tmp_cache):
    import config

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "dummy")
    monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", "http://127.0.0.1:9")
    t0 = time.perf_counter()
    r = client.post(f"{PREFIX}/deepseek/analyze", json={"scene": "qa", "question": "联邦学习"})
    el = time.perf_counter() - t0
    assert r.status_code == 200 and r.json()["source"] == "rule" and el < 8.0
    assert r.json()["latencyMs"] <= int(el * 1000) + 50


def test_deepseek_timeout_within_budget_and_concurrent(client, monkeypatch, tmp_cache):
    import http.server
    import threading as th

    import config

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            time.sleep(30)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    th.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "dummy")
        monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        monkeypatch.setattr(config, "DEEPSEEK_TIMEOUT", 1.0)
        outs = []
        t0 = time.perf_counter()
        ts = [th.Thread(target=lambda i: outs.append(client.post(f"{PREFIX}/deepseek/analyze", json={"scene": "qa", "question": f"q{i}"})), args=(i,)) for i in range(5)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        el = time.perf_counter() - t0
        assert el < 6.0, el
        assert all(r.status_code == 200 and r.json()["source"] == "rule" and r.json()["latencyMs"] >= 900 for r in outs)
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("body", [b"<html>oops</html>", b'{"id":"x"}', b'{"choices":[]}', b'{"choices":[{"message":{"content":123}}]}', b'{"choices":[{"message":{"content":""}}]}'])
def test_deepseek_malformed_live_response(client, monkeypatch, tmp_cache, body):
    import http.server
    import threading as th

    import config

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    th.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "dummy")
        monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        r = client.post(f"{PREFIX}/deepseek/analyze", json={"scene": "dispatch", "context": {"nodes": [{"id": "a", "load": 1}]}})
        assert r.status_code == 200 and r.json()["source"] == "rule" and r.json()["answer"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_deepseek_corrupt_cache_self_heals(client, tmp_cache):
    from adapters import deepseek

    tmp_cache.write_text('{"broken": ', encoding="utf-8")
    assert deepseek.load_cache() == {}
    assert json.loads(tmp_cache.read_text()) == {}  # 已重写为合法 JSON
    assert any(p.name.startswith("cache.corrupt-") for p in tmp_cache.parent.iterdir())
    tmp_cache.write_text("[1,2]", encoding="utf-8")  # 根不是对象也视为损坏
    assert deepseek.load_cache() == {}
    r = client.post(f"{PREFIX}/deepseek/analyze", json={"scene": "qa", "question": "dqn"})
    assert r.status_code == 200 and r.json()["source"] == "rule"


def test_deepseek_cache_bounded(tmp_cache, monkeypatch):
    import config
    from adapters import deepseek

    monkeypatch.setattr(config, "DEEPSEEK_CACHE_MAX", 5)
    deepseek.save_cache_entry("scene:qa", {"answer": "x", "template": "rule"})
    for i in range(20):
        deepseek.save_cache_entry(f"k{i}", {"answer": "x", "cachedAt": f"2026-01-01T00:00:{i:02d}"})
    data = json.loads(tmp_cache.read_text())
    assert len(data) <= 5 and "scene:qa" in data and "k19" in data
