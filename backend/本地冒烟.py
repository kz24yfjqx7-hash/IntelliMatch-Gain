#!/usr/bin/env python
"""起完服务后跑这个，十几秒证明整条链路是通的。

和 pytest 的区别：pytest 跑在 SQLite 内存库 + 假 Redis 上，这个跑在真 MySQL + 真 Redis 上。
表结构对不对、种子数据全不全、Redis 缓存生不生效，只有这个测得出来。
用法：.venv/bin/python 本地冒烟.py
"""
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ok = fail = 0


def check(名称, 条件, 详情=""):
    global ok, fail
    if 条件:
        ok += 1
        print(f"  \033[32m✓\033[0m {名称}")
    else:
        fail += 1
        print(f"  \033[31m✗\033[0m {名称}  {详情}")


def login(u, p):
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"username": u, "password": p}, timeout=15)
    body = r.json()
    if body.get("code") != 0:
        raise SystemExit(f"登录 {u} 失败：{body}")
    return {"Authorization": "Bearer " + body["data"]["token"]}


print("\n\033[1m一、服务活着没有\033[0m")
try:
    r = httpx.get(f"{BASE}/health", timeout=10)
except Exception as e:  # noqa: BLE001
    raise SystemExit(f"\n连不上 {BASE}——先跑 ./本地起服务.sh\n{e}")
check("GET /health 返回 200", r.status_code == 200, r.text[:120])

print("\n\033[1m二、认证\033[0m")
r = httpx.get(f"{BASE}/api/v1/users", timeout=10)
check("不带 token 打接口 → 1002", r.json().get("code") == 1002, r.text[:120])
r = httpx.post(f"{BASE}/api/v1/auth/login", json={"username": "admin", "password": "错的"}, timeout=10)
check("密码错 → 1002", r.json().get("code") == 1002, r.text[:120])
admin = login("admin", "admin123")
check("admin 登录拿到 token", True)
subject = login("subject", "subject123")

print("\n\033[1m三、种子数据在不在（前端要靠它才不空页）\033[0m")
for 名称, 路径, 键 in [
    ("用户列表", "/api/v1/users?page=1&pageSize=10", "total"),
    ("DID 身份", "/api/v1/did?page=1&pageSize=10", "total"),
    ("数据资产", "/api/v1/assets?page=1&pageSize=10", "total"),
    ("存证记录", "/api/v1/evidence?page=1&pageSize=10", "total"),
    ("审计日志", "/api/v1/audit/logs?page=1&pageSize=10", "total"),
    ("节点列表", "/api/v1/nodes?page=1&pageSize=10", "total"),
]:
    r = httpx.get(BASE + 路径, headers=admin, timeout=15)
    b = r.json()
    n = (b.get("data") or {}).get(键)
    check(f"{名称} 有 {n} 条", bool(n), f"{r.status_code} {r.text[:150]}")

print("\n\033[1m四、越权挡不挡得住\033[0m")
r = httpx.get(f"{BASE}/api/v1/users", headers=subject, timeout=10)
check("能源主体查用户列表 → 1003", r.json().get("code") == 1003, r.text[:150])

# 调度下发要拿真实任务 id，随便编一个会先撞上 1005 而不是权限判断
任务 = httpx.get(f"{BASE}/api/v1/dispatch/tasks?page=1&pageSize=1", headers=admin, timeout=15).json()
项 = ((任务.get("data") or {}).get("items") or [None])[0]
if 项:
    tid = 项.get("id") or 项.get("taskId")
    r = httpx.post(f"{BASE}/api/v1/dispatch/tasks/{tid}/issue", headers=subject,
                   json={"signature": "deadbeef"}, timeout=10)
    check(f"能源主体下发调度任务 {tid} → 1003", r.json().get("code") == 1003, r.text[:150])
    r = httpx.post(f"{BASE}/api/v1/dispatch/tasks/{tid}/issue", headers=admin,
                   json={"signature": "deadbeef"}, timeout=10)
    check("管理员拿伪造签名下发 → 1004（和越权的 1003 区分开）",
          r.json().get("code") == 1004, r.text[:150])
else:
    check("调度任务种子数据存在", False, "GET /dispatch/tasks 没返回任何任务")

print("\n\033[1m五、存证链没被动过\033[0m")
r = httpx.get(f"{BASE}/api/v1/evidence/chain/status", headers=admin, timeout=30)
d = r.json().get("data") or {}
check(f"哈希链完整 intact={d.get('intact')} 共 {d.get('total')} 块", d.get("intact") is True, r.text[:200])

print("\n\033[1m六、响应信封与 traceId\033[0m")
r = httpx.get(f"{BASE}/api/v1/不存在的接口", headers=admin, timeout=10)
b = r.json()
check("404 也是统一信封", set(b) == {"code", "message", "data", "traceId"}, str(b)[:150])
check("X-Trace-Id 与 body.traceId 一致", r.headers.get("X-Trace-Id") == b.get("traceId"),
      f"{r.headers.get('X-Trace-Id')} vs {b.get('traceId')}")

print("\n\033[1m七、算法服务（乙还没交，预期不通）\033[0m")
任务 = httpx.get(f"{BASE}/api/v1/fl/tasks?page=1&pageSize=1", headers=admin, timeout=15).json()
项 = ((任务.get("data") or {}).get("items") or [None])[0]
if 项:
    tid = 项.get("id") or 项.get("taskId")
    r = httpx.post(f"{BASE}/api/v1/fl/tasks/{tid}/start", headers=admin, json={}, timeout=30)
    c = r.json().get("code")
    check(f"联邦训练接口返回 {c}（1006=算法服务不可用，属正常；0=乙的服务已经起了）",
          c in (0, 1006, 1008), r.text[:200])
else:
    check("联邦任务种子数据存在", False, "GET /fl/tasks 没返回任何任务")

print(f"\n\033[1m通过 {ok} 项，失败 {fail} 项\033[0m\n")
sys.exit(1 if fail else 0)
