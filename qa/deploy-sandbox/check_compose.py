#!/usr/bin/env python3
"""严格校验 docker-compose.yml / docker-compose.dev.yml 是否符合契约与《安装包规范》。
用法：python3 check_compose.py <repo根目录>；任何一条不符打印 FAIL 并以 1 退出。"""
import re
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
fails = []


def fail(msg):
    fails.append(msg)
    print("FAIL", msg)


def ok(msg):
    print("ok  ", msg)


# ---------- 契约第四部分变量 ----------
contract = (root / "contract/API-CONTRACT.md").read_text(encoding="utf-8")
sec4 = contract.split("## 第四部分")[1].split("## 第五部分")[0]
CONTRACT_VARS = set(re.findall(r"^([A-Z_]+)=", sec4, re.M))
EXEMPT = {"FL_ROUND_DELAY"}  # qa 已豁免的 algo 内部变量（带默认值）
# 甲 backend/core/config.py 读取但契约第四部分没有的变量（compose 里必须带 ${VAR:-默认}）
BACKEND_EXTRA = {"REDIS_DB", "JWT_ALGORITHM", "KEY_CUSTODY_SECRET", "ALGO_TIMEOUT", "DEBUG", "DB_WAIT_TIMEOUT"}
EXEMPT |= BACKEND_EXTRA

env_example = (root / ".env.example").read_text(encoding="utf-8")
ENV_VARS = set(re.findall(r"^([A-Z_]+)=", env_example, re.M))
if ENV_VARS - BACKEND_EXTRA != CONTRACT_VARS:
    fail(f".env.example 变量集合 != 契约：多 {ENV_VARS - CONTRACT_VARS - BACKEND_EXTRA} 少 {CONTRACT_VARS - ENV_VARS}")
else:
    ok(".env.example 变量集合 = 契约第四部分 + backend 非契约变量 " + ",".join(sorted(BACKEND_EXTRA)))
for v in BACKEND_EXTRA:
    if v not in ENV_VARS:
        fail(f".env.example 缺 backend 非契约变量 {v}")
    elif not re.search(r"^" + v + r"=.*非契约变量", env_example, re.M):
        fail(f".env.example {v} 行须注明“非契约变量”")
if not re.search(r"^KEY_CUSTODY_SECRET=.*(install\.sh|随机)", env_example, re.M):
    fail(".env.example KEY_CUSTODY_SECRET 须注明由 install.sh 随机生成")
# .env.example 默认值与契约一致
contract_defaults = dict(re.findall(r"^([A-Z_]+)=(.*)$", sec4, re.M))
for k, v in re.findall(r"^([A-Z_]+)=([^#\n]*)", env_example, re.M):
    if k in BACKEND_EXTRA:
        continue
    if contract_defaults.get(k, "").strip() != v.strip():
        fail(f".env.example {k} 默认值 {v.strip()!r} != 契约 {contract_defaults.get(k)!r}")

# ---------- 根 compose ----------
text = (root / "docker-compose.yml").read_text(encoding="utf-8")
d = yaml.safe_load(text)
svcs = d["services"]

EXPECT = {
    "mysql": dict(container="energy-tds-mysql", image="mysql:8.0", mem="512M"),
    "redis": dict(container="energy-tds-redis", image="redis:7-alpine", mem="96M"),
    "backend": dict(container="energy-tds-backend", image="energy-tds/backend:1.0", mem="300M", build="./backend"),
    "algo-service": dict(container="energy-tds-algo", image="energy-tds/algo-service:1.0", mem="400M", build="./algo-service"),
    "frontend": dict(container="energy-tds-frontend", image="energy-tds/frontend:1.0", mem="64M", build="./frontend"),
}
if set(svcs) != set(EXPECT):
    fail(f"服务集合 {set(svcs)} != {set(EXPECT)}")

for name, exp in EXPECT.items():
    s = svcs.get(name)
    if s is None:
        continue
    if s.get("container_name") != exp["container"]:
        fail(f"{name} container_name={s.get('container_name')} 期望 {exp['container']}")
    if s.get("image") != exp["image"]:
        fail(f"{name} image={s.get('image')} 期望 {exp['image']}")
    if "build" in exp:
        if s.get("build", {}).get("context") != exp["build"]:
            fail(f"{name} build.context={s.get('build')} 期望 {exp['build']}")
    elif "build" in s:
        fail(f"{name} 不应有 build:")
    if s.get("restart") != "unless-stopped":
        fail(f"{name} restart={s.get('restart')} 期望 unless-stopped")
    mem = s.get("deploy", {}).get("resources", {}).get("limits", {}).get("memory")
    if mem != exp["mem"]:
        fail(f"{name} 内存上限 {mem} 期望 {exp['mem']}")
    if "healthcheck" not in s or "test" not in s["healthcheck"]:
        fail(f"{name} 缺 healthcheck.test")
    if s.get("networks") != ["energy-net"]:
        fail(f"{name} networks={s.get('networks')}")
ok("五服务：容器名 / 镜像名 / build context / restart / 内存上限 / healthcheck / 网络")

# 端口：只有 frontend 对外映射；backend/algo 仅 expose
if svcs["frontend"].get("ports") != ["${FRONTEND_PORT}:80"]:
    fail(f"frontend ports={svcs['frontend'].get('ports')} 期望 ['${{FRONTEND_PORT}}:80']")
for n in ("mysql", "redis", "backend", "algo-service"):
    if "ports" in svcs[n]:
        fail(f"{n} 不应向宿主机映射端口（契约：前端只经 nginx 访问 backend）")
if svcs["backend"].get("expose") != ["${BACKEND_PORT}"]:
    fail("backend expose 应为 ${BACKEND_PORT}")
if svcs["algo-service"].get("expose") != ["${ALGO_PORT}"]:
    fail("algo-service expose 应为 ${ALGO_PORT}")
ok("端口：仅 frontend 映射 ${FRONTEND_PORT}:80，backend/algo 仅 expose")

# depends_on condition
dep = svcs["backend"].get("depends_on", {})
if dep.get("mysql", {}).get("condition") != "service_healthy" or dep.get("redis", {}).get("condition") != "service_healthy":
    fail("backend depends_on mysql/redis 必须 service_healthy")
if "algo-service" not in dep:
    fail("backend depends_on 缺 algo-service")
if svcs["frontend"].get("depends_on", {}).get("backend") is None:
    fail("frontend depends_on 缺 backend")
ok("depends_on condition")

# 卷挂载
mv = svcs["mysql"].get("volumes", [])
for need in ("./backend/sql:/docker-entrypoint-initdb.d:ro", "./deploy/mysql/mysql.cnf:/etc/mysql/conf.d/energy.cnf:ro", "mysql-data:/var/lib/mysql"):
    if need not in mv:
        fail(f"mysql 缺挂载 {need}")
if "redis-data:/data" not in svcs["redis"].get("volumes", []):
    fail("redis 缺 redis-data:/data")
for n in ("backend", "algo-service", "frontend"):
    if svcs[n].get("volumes"):
        fail(f"{n} 不应挂载卷（镜像自包含，非 root 可写目录在镜像内）")
if d.get("volumes", {}).get("mysql-data", {}).get("name") != "energy-tds-mysql-data":
    fail("mysql-data 卷名应为 energy-tds-mysql-data")
if d.get("volumes", {}).get("redis-data", {}).get("name") != "energy-tds-redis-data":
    fail("redis-data 卷名应为 energy-tds-redis-data")
if d.get("networks", {}).get("energy-net", {}).get("name") != "energy-tds-net":
    fail("网络名应为 energy-tds-net")
ok("卷 / 网络命名与挂载路径")

# 环境变量：所有 ${VAR} 引用 ⊆ 契约（豁免 FL_ROUND_DELAY，且必须带默认值）
refs = set(re.findall(r"\$\{([A-Z_]+)(?::-[^}]*)?\}", text))
bad = refs - CONTRACT_VARS - EXEMPT
if bad:
    fail(f"compose 引用契约外变量：{bad}")
for v in EXEMPT & refs:
    if not re.search(r"\$\{" + v + r":-", text):
        fail(f"豁免变量 {v} 必须带内联默认值")
ok("compose 引用的变量 ⊆ 契约第四部分（+已豁免 FL_ROUND_DELAY 带默认值）")
# 每个服务 environment 的 key 也必须是契约变量或 TZ
for n, s in svcs.items():
    for k in (s.get("environment") or {}):
        if k not in CONTRACT_VARS and k not in EXEMPT and k != "TZ":
            fail(f"{n}.environment 含契约外 key {k}")
# backend 必须拿到它需要的全部变量
need_backend = {"MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD", "REDIS_HOST", "REDIS_PORT",
                "BACKEND_PORT", "JWT_SECRET", "JWT_EXPIRE_SECONDS", "ALGO_SERVICE_URL"} | BACKEND_EXTRA
miss = need_backend - set(svcs["backend"]["environment"])
if miss:
    fail(f"backend.environment 缺 {miss}")
be = svcs["backend"]["environment"]
if be.get("KEY_CUSTODY_SECRET") == be.get("JWT_SECRET"):
    fail("KEY_CUSTODY_SECRET 不得与 JWT_SECRET 同源")
need_algo = {"ALGO_PORT", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_TIMEOUT", "DEEPSEEK_OFFLINE_FALLBACK"}
miss = need_algo - set(svcs["algo-service"]["environment"])
if miss:
    fail(f"algo-service.environment 缺 {miss}")
need_mysql = {"MYSQL_ROOT_PASSWORD", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD"}
miss = need_mysql - set(svcs["mysql"]["environment"])
if miss:
    fail(f"mysql.environment 缺 {miss}")
if "env_file" in svcs["backend"]:
    fail("backend 不应有 env_file（MSG-qa-to-deploy-001）")
ok("各服务 environment 完整且无自创 key")

# healthcheck 细节
hc = " ".join(svcs["mysql"]["healthcheck"]["test"])
if "$${MYSQL_ROOT_PASSWORD}" not in hc or "mysqladmin ping" not in hc:
    fail(f"mysql healthcheck 应用 $${{MYSQL_ROOT_PASSWORD}}（容器内展开）：{hc}")
hc = " ".join(svcs["algo-service"]["healthcheck"]["test"])
if "curl" in hc or "urllib" not in hc:
    fail(f"algo healthcheck 不得依赖 curl（python:slim 无 curl），应用 urllib：{hc}")
if "/algo/v1/health" not in hc or "${ALGO_PORT}" not in hc:
    fail("algo healthcheck URL 应为 http://127.0.0.1:${ALGO_PORT}/algo/v1/health")
hc = " ".join(svcs["backend"]["healthcheck"]["test"])
if "urllib" not in hc or "${BACKEND_PORT}/health'" not in hc or "/api/v1/health" in hc:
    fail("backend healthcheck 应用 urllib 探根路径 /health（/api/v1/health 在后端为 404）")
if "curl" in hc:
    fail("backend healthcheck 不得依赖 curl（python:slim 无 curl）")
hc = " ".join(svcs["frontend"]["healthcheck"]["test"])
if "wget" not in hc:
    fail("frontend healthcheck 应用 busybox wget（nginx:alpine 无 curl）")
if " ".join(svcs["redis"]["healthcheck"]["test"]) != "CMD redis-cli ping":
    fail("redis healthcheck 应为 redis-cli ping")
ok("healthcheck：mysql 密码变量 / algo·backend 用 urllib / frontend 用 wget")

# frontend build args
args = svcs["frontend"]["build"].get("args", {})
for k in ("VITE_API_BASE", "VITE_WS_BASE", "VITE_USE_MOCK"):
    if args.get(k) != "${%s}" % k:
        fail(f"frontend build.args.{k}={args.get(k)}")
ok("frontend build args VITE_*")

# mysql command 不应含 mariadb 不识别的参数
cmd = " ".join(svcs["mysql"].get("command", []))
if "default-authentication-plugin" in cmd:
    fail("mysql command 含 --default-authentication-plugin（切 mariadb 时不识别）")

# ---------- dev compose ----------
dev = yaml.safe_load((root / "docker-compose.dev.yml").read_text(encoding="utf-8"))
ds = dev["services"]
if set(ds) != {"algo-service", "frontend-mock"}:
    fail(f"dev compose 服务集合 {set(ds)}")
if ds["frontend-mock"].get("profiles") != ["mock"]:
    fail("frontend-mock 应在 mock profile")
if str(ds["frontend-mock"]["build"]["args"].get("VITE_USE_MOCK")).lower() != "true":
    fail("frontend-mock VITE_USE_MOCK 应为 true")
if ds["frontend-mock"].get("image") != "energy-tds/frontend:1.0-mock":
    fail("frontend-mock 镜像名应为 energy-tds/frontend:1.0-mock")
dev_text = (root / "docker-compose.dev.yml").read_text(encoding="utf-8")
bad = set(re.findall(r"\$\{([A-Z_]+)", dev_text)) - CONTRACT_VARS - EXEMPT
if bad:
    fail(f"dev compose 引用契约外变量 {bad}")
ok("dev compose：algo-service + mock profile 前端")

print()
print("FAIL %d" % len(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
