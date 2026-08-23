"""性能抽样：登录 / 资产列表 / 存证校验各 50 次 P95；20 并发登录。运行：backend/.venv/bin/python qa/api-tests/perf.py"""
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
import httpx
from lib import BASE, Recorder, hdr, token

R = Recorder(os.path.join(os.path.dirname(__file__), "results", "perf.json"))
C = R.case


def p95(xs):
    xs = sorted(xs)
    return xs[int(round(0.95 * (len(xs) - 1)))]


def timed(fn, n=50):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter(); fn(); ts.append((time.perf_counter() - t0) * 1000)
    return {"n": n, "p50": round(statistics.median(ts), 1), "p95": round(p95(ts), 1), "max": round(max(ts), 1), "avg": round(sum(ts) / n, 1)}


c = httpx.Client(base_url=BASE, timeout=30)
H = hdr("admin")
s = timed(lambda: c.post("/auth/login", json={"username": "admin", "password": "admin123"}))
C("API-PERF-01", "性能抽样（bcrypt 登录）", "POST /auth/login ×50 串行", "P95 < 1000ms（bcrypt 成本，记录实际值）", [(s["p95"] < 1000, str(s))], s)
s = timed(lambda: c.get("/assets", headers=H, params={"page": 1, "size": 20}))
C("API-PERF-02", "性能抽样", "GET /assets?size=20 ×50", "P95 < 300ms", [(s["p95"] < 300, str(s))], s)
s = timed(lambda: c.post("/evidence/verify", headers=H, json={"evidenceId": "ev-000100"}))
C("API-PERF-03", "性能抽样（SM3 重算）", "POST /evidence/verify ×50", "P95 < 300ms", [(s["p95"] < 300, str(s))], s)
s = timed(lambda: c.get("/evidence/chain/status", headers=H), n=20)
C("API-PERF-04", "性能抽样（全链 SM3 校验）", "GET /evidence/chain/status ×20", "P95 < 2000ms（全链重算，记录实际值）", [(s["p95"] < 2000, str(s))], s)
s = timed(lambda: c.get("/audit/logs", headers=H, params={"page": 1, "size": 20}), n=30)
C("API-PERF-05", "性能抽样（跨月 UNION）", "GET /audit/logs ×30", "P95 < 500ms", [(s["p95"] < 500, str(s))], s)


# 20 条独立连接（每线程一个 Client）在计时**之外**先建好：httpx.Client() 构造要建 SSL
# 上下文，单次约 80-90 ms 且持 GIL，若放在计时区内由 20 个线程各建一个，会被串行成
# 1.7-1.9 s 的纯客户端开销计入"登录耗时"（2026-08-23 实测：curl -P20 真实服务端 P95
# 仅 ~280 ms，而本脚本量到 2.9-3.0 s 在阈值 3000 附近抖动）。
clients = [httpx.Client(base_url=BASE, timeout=30) for _ in range(20)]


def login_once(i):
    t0 = time.perf_counter()
    r = clients[i].post("/auth/login", json={"username": "admin", "password": "admin123"})
    return (time.perf_counter() - t0) * 1000, r.status_code, (r.json().get("code") if r.headers.get("content-type", "").startswith("application/json") else None)


t0 = time.perf_counter()
with ThreadPoolExecutor(20) as ex:
    res = list(ex.map(login_once, range(20)))
wall = (time.perf_counter() - t0) * 1000
for cc in clients:
    cc.close()
codes = [x[2] for x in res]
s = {"concurrency": 20, "wall_ms": round(wall, 1), "p95_ms": round(p95([x[0] for x in res]), 1), "max_ms": round(max(x[0] for x in res), 1), "codes": sorted(set(codes))}
C("API-PERF-06", "20 并发登录", "ThreadPool 20 并发 POST /auth/login", "全部 code 0，无 5xx/1007；P95 < 3000ms", [(codes == [0] * 20, f"codes={codes}"), (s["p95_ms"] < 3000, str(s))], s)
R.save()
