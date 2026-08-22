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


def login_once(_):
    t0 = time.perf_counter()
    with httpx.Client(base_url=BASE, timeout=30) as cc:
        r = cc.post("/auth/login", json={"username": "admin", "password": "admin123"})
    return (time.perf_counter() - t0) * 1000, r.status_code, (r.json().get("code") if r.headers.get("content-type", "").startswith("application/json") else None)


t0 = time.perf_counter()
with ThreadPoolExecutor(20) as ex:
    res = list(ex.map(login_once, range(20)))
wall = (time.perf_counter() - t0) * 1000
codes = [x[2] for x in res]
s = {"concurrency": 20, "wall_ms": round(wall, 1), "p95_ms": round(p95([x[0] for x in res]), 1), "max_ms": round(max(x[0] for x in res), 1), "codes": sorted(set(codes))}
C("API-PERF-06", "20 并发登录", "ThreadPool 20 并发 POST /auth/login", "全部 code 0，无 5xx/1007；P95 < 3000ms", [(codes == [0] * 20, f"codes={codes}"), (s["p95_ms"] < 3000, str(s))], s)
R.save()
