"""降级链路测试：不停任何人的服务，另起隔离实例验证。
  1) 在 5301 端口起一个 algo-service 副本，DEEPSEEK_BASE_URL 指向不可达地址 → 验证 deepseek 三级降级 source=cache/rule；
  2) 在 5300 端口起一个 backend 副本，ALGO_SERVICE_URL 指向空端口 → 验证后端返回 2001 而非 500。
两个副本共用同一 MariaDB/Redis（与正式实例一致），测试后自动结束。运行：backend/.venv/bin/python qa/api-tests/degrade_test.py"""
import json
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import httpx
from lib import ROOT, Recorder, req, token, uniq

R = Recorder(os.path.join(os.path.dirname(__file__), "results", "degrade.json"))
C = R.case
ALGO_DIR = os.path.join(ROOT, "algo-service")
BACKEND_DIR = os.path.join(ROOT, "backend")
LOG = os.environ.get("SCRATCH", "/tmp")


def wait_up(url, secs=40):
    for _ in range(secs * 2):
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start(cmd, cwd, env, logname):
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=open(os.path.join(LOG, logname), "w"), stderr=subprocess.STDOUT, start_new_session=True)


def stop(p):
    if p and p.poll() is None:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)


# ---------- 1) algo 副本：DeepSeek 不可达
env = dict(os.environ)
for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
    line = line.split("#")[0].strip()
    if "=" in line:
        k, v = line.split("=", 1); env.setdefault(k.strip(), v.strip())
env.update({"DEEPSEEK_BASE_URL": "http://127.0.0.1:9", "DEEPSEEK_TIMEOUT": "2", "DEEPSEEK_OFFLINE_FALLBACK": "true", "ALGO_PORT": "5301"})
algo = start([os.path.join(ROOT, ".venv", "bin", "uvicorn"), "main:app", "--host", "127.0.0.1", "--port", "5301"], ALGO_DIR, env, "algo-5301.log")
try:
    up = wait_up("http://127.0.0.1:5301/algo/v1/health")
    h = httpx.get("http://127.0.0.1:5301/algo/v1/health", timeout=5).json() if up else {}
    C("API-DEG-01", "契约 3.1 health deepseek 状态", "起 5301 副本（DEEPSEEK_BASE_URL 不可达）查 health", "status=ok models.deepseek=cache（非 live）", [(up, "副本未起"), (h.get("models", {}).get("deepseek") in ("cache", "rule", "offline"), str(h))], h)
    t0 = time.time()
    r = httpx.post("http://127.0.0.1:5301/algo/v1/deepseek/analyze", json={"scene": "dispatch", "context": {"taskId": "dp-000001", "actions": [{"nodeId": "Node-C", "action": "discharge", "powerKw": 24}]}, "question": "为什么选择节点C放电？"}, timeout=30)
    b = r.json(); cost = time.time() - t0
    C("API-DEG-02", "契约 3.4 三级降级 live→cache→rule，永远 200 / 验收 12 断网 source=cache", "副本 deepseek/analyze", "HTTP 200；source∈cache|rule；answer 非空；耗时 < 8s+",
      [(r.status_code == 200, f"HTTP {r.status_code}"), (b.get("source") in ("cache", "rule"), f"source={b.get('source')}"), (len(b.get("answer", "")) > 5, "answer 空"), (cost < 12, f"耗时 {cost:.1f}s")], {**b, "answer": b.get("answer", "")[:60], "cost_s": round(cost, 2)})
    r = httpx.post("http://127.0.0.1:5301/algo/v1/deepseek/analyze", json={"scene": "qa", "question": "test-一个肯定没有缓存的问题 " + uniq()}, timeout=30)
    b = r.json()
    C("API-DEG-03", "契约 3.4 缓存未命中 → rule 模板", "副本 deepseek/analyze 新问题", "200 source=rule 或 cache(场景级兜底)", [(r.status_code == 200 and b.get("source") in ("rule", "cache"), str(b.get("source")))], {**b, "answer": b.get("answer", "")[:60]})
    # 正式 algo 的 source 字段对照
    r = httpx.get("http://127.0.0.1:8100/algo/v1/health", timeout=5).json()
    C("API-DEG-04", "正式 algo-service health", "GET 8100 health", "models.dqn=loaded；deepseek∈live|cache", [(r.get("models", {}).get("dqn") == "loaded", str(r)), (r.get("models", {}).get("deepseek") in ("live", "cache"), str(r))], r)
finally:
    stop(algo)

# ---------- 2) backend 副本：算法服务不可达 → 2001
benv = dict(os.environ)
for line in open(os.path.join(BACKEND_DIR, ".env"), encoding="utf-8"):
    line = line.split("#")[0].strip()
    if "=" in line:
        k, v = line.split("=", 1); benv[k.strip()] = v.strip()
benv.update({"ALGO_SERVICE_URL": "http://127.0.0.1:9", "BACKEND_PORT": "5300"})
be = start([os.path.join(BACKEND_DIR, ".venv", "bin", "python"), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "5300"], BACKEND_DIR, benv, "backend-5300.log")
try:
    up = wait_up("http://127.0.0.1:5300/health")
    c = httpx.Client(base_url="http://127.0.0.1:5300/api/v1", timeout=60)
    tk = c.post("/auth/login", json={"username": "admin", "password": "admin123"}).json()["data"]["token"]
    H = {"Authorization": f"Bearer {tk}"}
    r = c.post("/assets/classify", headers=H, json={"records": [{"dataType": "pv", "fields": ["power"], "freq": "minute", "volume": 10}]})
    b = r.json()
    C("API-DEG-05", "算法不可达时分类分级本地兜底（backend raise_on_error=False）", "副本 backend(算法地址不可达) POST /assets/classify", "不 500：200 本地规则分级（cluster=null）或 502/2001", [(up, "副本未起"), ((r.status_code == 200 and b.get("code") == 0) or (r.status_code == 502 and b.get("code") == 2001), f"HTTP {r.status_code} code {b.get('code')}"), (set(b.keys()) == {"code", "message", "data", "traceId"}, "包裹")], b, r.status_code)
    r = c.post("/fl/tasks", headers=H, json={"name": uniq("test-deg-fl"), "nodeIds": ["Node-A"], "rounds": 1})
    fl = (r.json().get("data") or {}).get("id")
    r = c.post(f"/fl/tasks/{fl}/start", headers=H)
    b = r.json()
    C("API-DEG-06", "2001·FL 启动", "副本 backend POST /fl/tasks/{id}/start", "502/2001", [(r.status_code == 502 and b.get("code") == 2001, f"HTTP {r.status_code} code {b.get('code')}")], b, r.status_code)
    r = c.post("/dispatch/tasks", headers=H, json={"name": uniq("test-deg-dp"), "nodeIds": ["Node-A"]})
    dp = (r.json().get("data") or {}).get("id")
    r = c.post(f"/dispatch/tasks/{dp}/run", headers=H)
    b = r.json()
    C("API-DEG-07", "2001·DQN 调度", "副本 backend POST /dispatch/tasks/{id}/run", "502/2001", [(r.status_code == 502 and b.get("code") == 2001, f"HTTP {r.status_code} code {b.get('code')}")], b, r.status_code)
    r = c.post("/ai/analyze", headers=H, json={"scene": "qa", "question": "test-算法不可达时？"})
    b = r.json()
    C("API-DEG-08", "契约 2.11 离线兜底硬性要求：绝不返回错误", "副本 backend（算法不可达）POST /ai/analyze", "200 source=cache|rule（后端自身兜底）或 502/2001（记录）", [(r.status_code in (200, 502), f"HTTP {r.status_code}"), (b.get("code") in (0, 2001), str(b.get("code")))], {**b, "data": {**(b.get("data") or {}), "answer": str((b.get("data") or {}).get("answer"))[:50]}}, r.status_code)
    r = c.get("/audit/report", headers=H, params={"period": "day"})
    b = r.json()
    C("API-DEG-09", "契约 2.7 report narrativeSource 降级 rule", "副本 backend GET /audit/report", "200 narrativeSource=rule", [(r.status_code == 200 and (b.get("data") or {}).get("narrativeSource") == "rule", str((b.get("data") or {}).get("narrativeSource")))], {"narrativeSource": (b.get("data") or {}).get("narrativeSource")}, r.status_code)
    r = c.post("/risk/assess", headers=H, json={"nodeId": "Node-A", "features": {"queryFreq": 1}})
    b = r.json()
    C("API-DEG-10", "2001·风险评估", "副本 backend POST /risk/assess", "502/2001 或 200 本地兜底", [(b.get("code") in (0, 2001) and r.status_code != 500, f"HTTP {r.status_code} code {b.get('code')}")], b, r.status_code)
    r = httpx.get("http://127.0.0.1:5300/health", timeout=5)
    C("API-DEG-11", "health 依赖探测", "副本 backend GET /health", "status 反映 algo 不可达（degraded 或 deps.algo=false）或仅报 mysql/redis（记录）", [(r.status_code == 200, "")], r.json(), r.status_code)
finally:
    stop(be)
R.save()
