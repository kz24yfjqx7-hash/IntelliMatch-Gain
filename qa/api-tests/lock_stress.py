"""并发写压测：专找「同一请求内业务会话与写链会话互等行锁」这类自锁尾延迟。

背景：本项目两天内已修两条同族缺陷——B-027（审计上链路径，回收授权曾挂 200 秒）、
B-028（R03 告警上链路径，回收/驳回曾挂 9 秒）。两者都只在真库并发下暴露，
单测测不到。本脚本用多线程同时打所有会写存证链的路径，看还有没有第三条。

判据（自锁的指纹）：
  - 单个请求墙钟耗时**偶发**落到 3s / 6s / 9s 这几个整数档（= 会话级 innodb_lock_wait_timeout=3s × 重试次数），
    而不是平滑上升的尾延迟；
  - 服务端日志出现「存证上链失败」（重试用尽，静默丢弃）——出现一次即为真问题；
  - 事后一致性：高危审计日志有 evidence_id、告警有 evidence_id、告警自增无空洞、链 intact。

用法：
  cd energy-tds
  backend/.venv/bin/python qa/api-tests/lock_stress.py [并发数=20] [秒数=90]
不改任何业务代码；创建的实体带 test-lockstress- 前缀；结束自动 restore-all。
"""
import os
import random
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict

import httpx

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
WORKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
DURATION = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
TS = str(int(time.time()))
PREFIX = f"test-lockstress-{TS}"


def login(u, p):
    r = httpx.post(f"{BASE}/auth/login", json={"username": u, "password": p}, timeout=30)
    return r.json()["data"]["token"]


def h(tok):
    return {"Authorization": f"Bearer {tok}"}


print(f"== 并发写压测 · {WORKERS} 线程 × {DURATION:.0f}s ==")
ADMIN = login("admin", "admin123")
SUBJECT = login("subject", "subject123")
VPP = login("vpp", "vpp123")

# 造一条 admin 名下的资产，供 subject 反复申请 / 审批 / 回收
did = httpx.post(f"{BASE}/did/register", headers=h(ADMIN),
                 json={"subjectType": "device", "subjectName": f"{PREFIX}-dev", "custody": True},
                 timeout=30).json()["data"]["did"]
ASSET = httpx.post(f"{BASE}/assets", headers=h(ADMIN),
                   json={"name": f"{PREFIX}-asset", "dataType": "pv", "sourceDid": did,
                         "level": "L3", "payload": {"pv": 1.0}, "recordCount": 10},
                   timeout=30).json()["data"]["id"]
SOURCE_DID = did
print(f"资产 {ASSET} 就绪；开始加压……")

# 每个线程一个独立 httpx.Client（独立连接），在计时区外预建——
# 教训来自 API-PERF-06：把 Client 构造（SSL 上下文，持 GIL）算进请求耗时会误报。
clients = [httpx.Client(base_url=BASE, timeout=60) for _ in range(WORKERS)]

records = []            # (action, ms, code)
records_lock = threading.Lock()
stop_at = None


def timed(cli, method, path, tok, json_body=None):
    t0 = time.time()
    try:
        r = cli.request(method, path, headers=h(tok), json=json_body)
        code = r.status_code
        body_code = None
        try:
            body_code = r.json().get("code")
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        code = f"EXC:{type(exc).__name__}"
        body_code = None
    ms = (time.time() - t0) * 1000
    return ms, code, body_code


def act_vpp_denied(cli):
    # vpp 查用户列表 → 1003 → denied high 审计上链 + R01 计数
    return timed(cli, "GET", "/users?size=5", VPP)


def act_evidence_write(cli):
    # 直接写一条存证（纯链写入）
    return timed(cli, "POST", "/evidence", ADMIN,
                 {"category": "data", "refId": f"{PREFIX}-{random.randint(0, 1 << 30)}",
                  "payload": {"v": random.random()}, "actorDid": SOURCE_DID})


def act_asset(cli):
    # 资产登记 → 分类分级 → 上链
    return timed(cli, "POST", "/assets", ADMIN,
                 {"name": f"{PREFIX}-a{random.randint(0, 1 << 30)}", "dataType": "pv",
                  "sourceDid": SOURCE_DID, "level": "L3", "payload": {"pv": random.random()},
                  "recordCount": 10})


def act_apply_reject(cli):
    # subject 申请 → admin 驳回：evidence + 审计 + 留痕（R03 计数），走 reject 事务
    ms1, c1, _ = timed(cli, "POST", "/permissions/apply", SUBJECT,
                       {"resourceType": "asset", "resourceId": str(ASSET), "action": "read",
                        "reason": f"{PREFIX}-r"})
    try:
        aid = cli.post("/permissions/apply", headers=h(SUBJECT),
                       json={"resourceType": "asset", "resourceId": str(ASSET),
                             "action": "read", "reason": f"{PREFIX}-r2"}).json()["data"]["id"]
    except Exception:  # noqa: BLE001
        return ms1, c1, None
    return timed(cli, "POST", f"/permissions/applications/{aid}/reject", ADMIN, {"reason": "stress"})


def act_grant_revoke(cli):
    # 申请 → 审批（建授权）→ 回收：回收正是 B-027 的现场路径
    try:
        aid = cli.post("/permissions/apply", headers=h(SUBJECT),
                       json={"resourceType": "asset", "resourceId": str(ASSET),
                             "action": "read", "reason": f"{PREFIX}-g"}).json()["data"]["id"]
        gid = cli.post(f"/permissions/applications/{aid}/approve", headers=h(ADMIN),
                       json={"reason": "stress-grant"}).json()["data"].get("grantId")
    except Exception:  # noqa: BLE001
        return None
    if not gid:
        return None
    return timed(cli, "POST", f"/permissions/grants/{gid}/revoke", ADMIN, {"reason": "stress-revoke"})


ACTIONS = [
    ("vpp_denied", act_vpp_denied),
    ("evidence_write", act_evidence_write),
    ("asset", act_asset),
    ("apply_reject", act_apply_reject),
    ("grant_revoke", act_grant_revoke),
]


def worker(cli):
    while time.time() < stop_at:
        name, fn = random.choice(ACTIONS)
        res = fn(cli)
        if res is None:
            continue
        ms, code, _ = res
        with records_lock:
            records.append((name, ms, code))


stop_at = time.time() + DURATION
threads = [threading.Thread(target=worker, args=(clients[i],), daemon=True) for i in range(WORKERS)]
t_start = time.time()
for t in threads:
    t.start()
for t in threads:
    t.join()
wall = time.time() - t_start
for c in clients:
    c.close()

# ---- 分析 ----
print(f"\n== 结果（墙钟 {wall:.1f}s，共 {len(records)} 次请求）==")
by_action = defaultdict(list)
for name, ms, code in records:
    by_action[name].append((ms, code))

print(f"{'动作':<16}{'次数':>6}{'P50':>9}{'P95':>9}{'P99':>9}{'最大':>9}  非2xx")
for name, fn in ACTIONS:
    rows = by_action.get(name, [])
    if not rows:
        continue
    lat = sorted(m for m, _ in rows)
    codes = Counter(c for _, c in rows)
    bad = {k: v for k, v in codes.items() if not (isinstance(k, int) and 200 <= k < 300)}
    p = lambda q: lat[min(len(lat) - 1, int(len(lat) * q))]
    print(f"{name:<16}{len(rows):>6}{p(.5):>9.0f}{p(.95):>9.0f}{p(.99):>9.0f}{max(lat):>9.0f}  {dict(bad)}")

# 自锁指纹：>=2900ms 的离群，及它们落在哪个整数秒档
all_lat = [ms for _, ms, _ in records]
outliers = sorted(ms for ms in all_lat if ms >= 2900)
print(f"\n>=2900ms 的离群请求：{len(outliers)} 个" + (f"（最大 {max(all_lat):.0f}ms）" if all_lat else ""))
if outliers:
    buckets = Counter(round(ms / 1000) for ms in outliers)
    print(f"  落档分布（秒）：{dict(sorted(buckets.items()))}")
    print(f"  样例：{[round(x) for x in outliers[:10]]}")
    print("  ⚠️ 若集中在 3/6/9s 整数档 → 疑似锁超时×重试的自锁；否则可能是排队尾延迟")
else:
    print("  ✅ 没有 3/6/9s 档的自锁离群")

print(f"\n下一步核对（需人工看）：")
print(f"  服务端日志： grep -c '存证上链失败' backend.log   （出现即真问题）")
print(f"              grep -c 'Lock wait timeout' backend.log  （少量正常，会被重试兜住）")
print(f"  实体前缀： {PREFIX}")

# ---- 收尾：还原被 asset 分类演示之外的东西（本脚本不篡改链，仅确保 restore-all）----
try:
    httpx.post(f"{BASE}/evidence/demo/restore-all", headers=h(ADMIN), json={}, timeout=30)
    st = httpx.get(f"{BASE}/evidence/chain/status", headers=h(ADMIN), timeout=30).json()["data"]
    print(f"\n链状态：intact={st['intact']} height={st['height']} brokenAt={st['brokenAt']}")
except Exception as exc:  # noqa: BLE001
    print(f"\n链状态查询失败：{exc}")
