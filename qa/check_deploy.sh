#!/usr/bin/env bash
# qa：compose config / bash -n / 环境变量名对照契约 / 挂载 / 内存限制 / install.sh 九步要点
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
FAIL=0
ok() { echo "[ OK ] $*"; }
bad() { echo "[FAIL] $*"; FAIL=1; }
warn() { echo "[WARN] $*"; }

echo "== 1. docker compose config =="
cp .env.example .env.qa
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  if docker compose --env-file .env.qa -f docker-compose.yml config -q 2>/tmp/qa-compose.err; then ok "docker-compose.yml config 通过"; else bad "docker-compose.yml config 失败：$(cat /tmp/qa-compose.err)"; fi
  if [ -f docker-compose.dev.yml ]; then
    if docker compose --env-file .env.qa -f docker-compose.dev.yml config -q 2>/tmp/qa-compose.err; then ok "docker-compose.dev.yml config 通过"; else bad "docker-compose.dev.yml config 失败：$(cat /tmp/qa-compose.err)"; fi
  fi
else
  warn "本机无 docker compose，改用 python yaml 语法检查"
  if .venv/bin/python -c "import yaml,sys; yaml.safe_load(open('docker-compose.yml'))" 2>/dev/null; then ok "docker-compose.yml YAML 语法通过"; else warn "无 pyyaml 或语法错误，无法校验"; fi
fi
rm -f .env.qa

echo "== 2. bash -n 全部 .sh =="
while IFS= read -r f; do
  if bash -n "$f" 2>/tmp/qa-sh.err; then ok "bash -n $f"; else bad "bash -n $f：$(cat /tmp/qa-sh.err)"; fi
done < <(find deploy packaging qa -name '*.sh' -type f | sort)
if command -v shellcheck >/dev/null 2>&1; then
  find deploy packaging -name '*.sh' -type f -exec shellcheck -S warning {} + && ok "shellcheck 通过" || warn "shellcheck 有告警"
else warn "shellcheck 未安装，跳过"; fi

echo "== 3. compose 环境变量名 ⊆ 契约第四部分 =="
CONTRACT_VARS=$(sed -n '/^## 第四部分/,/^## 第五部分/p' contract/API-CONTRACT.md | grep -oE '^[A-Z_]+=' | tr -d '=' | sort -u)
# 必须来自 .env 的变量（无内联默认值）必须在契约内；带 `:-默认值` 的内部可选变量（如 FL_ROUND_DELAY）仅提示
REQ_VARS=$(grep -oE '\$\{[A-Z_]+\}' docker-compose.yml | tr -d '${}' | sort -u)
OPT_VARS=$(grep -oE '\$\{[A-Z_]+:-' docker-compose.yml | sed 's/[${:-]//g' | sort -u)
EXTRA=""
for v in $REQ_VARS; do echo "$CONTRACT_VARS" | grep -qx "$v" || EXTRA="$EXTRA $v"; done
[ -z "$EXTRA" ] && ok "compose 必填变量全部在契约内" || bad "compose 引用了契约外必填变量：$EXTRA"
for v in $OPT_VARS; do echo "$CONTRACT_VARS" | grep -qx "$v" || warn "compose 含契约外可选变量（带默认值，不要求写入 .env）：$v"; done
ENV_VARS=$(grep -oE '^[A-Z_]+=' .env.example | tr -d '=' | sort -u)
MISSING=""; for v in $CONTRACT_VARS; do echo "$ENV_VARS" | grep -qx "$v" || MISSING="$MISSING $v"; done
[ -z "$MISSING" ] && ok ".env.example 覆盖契约全部变量" || bad ".env.example 缺少：$MISSING"
# 甲 backend/core/config.py 读取但契约第四部分没有的变量：允许出现在 .env.example，但该行必须注明"非契约变量"
BACKEND_EXTRA="REDIS_DB JWT_ALGORITHM KEY_CUSTODY_SECRET ALGO_TIMEOUT DEBUG DB_WAIT_TIMEOUT"
EXTRA2=""; for v in $ENV_VARS; do
  if echo "$BACKEND_EXTRA" | tr ' ' '\n' | grep -qx "$v"; then
    grep -qE "^$v=.*非契约变量" .env.example || bad ".env.example $v 未注明“非契约变量”"
  else echo "$CONTRACT_VARS" | grep -qx "$v" || EXTRA2="$EXTRA2 $v"; fi
done
[ -z "$EXTRA2" ] && ok ".env.example 无自创变量（backend 非契约变量已注明）" || bad ".env.example 自创变量：$EXTRA2"

echo "== 4. compose 结构 =="
grep -qE '\./backend/sql:/docker-entrypoint-initdb\.d:ro' docker-compose.yml && ok "backend/sql initdb 挂载存在" || bad "缺少 ./backend/sql:/docker-entrypoint-initdb.d:ro 挂载"
for s in mysql redis backend algo-service frontend; do grep -qE "^  $s:" docker-compose.yml && ok "服务 $s 存在" || bad "服务 $s 缺失"; done
N_MEM=$(grep -cE '^\s+memory:\s*[0-9]+[MmGg]' docker-compose.yml)
[ "$N_MEM" -ge 5 ] && ok "内存限制 $N_MEM 处（≥5）" || bad "内存限制仅 $N_MEM 处"
N_HC=$(grep -c 'healthcheck:' docker-compose.yml)
[ "$N_HC" -ge 4 ] && ok "healthcheck $N_HC 处" || bad "healthcheck 仅 $N_HC 处"
grep -q 'ALGO_SERVICE_URL' docker-compose.yml && ok "backend 注入 ALGO_SERVICE_URL" || bad "backend 缺 ALGO_SERVICE_URL"
grep -qE 'VITE_USE_MOCK' docker-compose.yml && ok "frontend 构建参数含 VITE_USE_MOCK" || bad "frontend 缺 VITE_USE_MOCK build-arg"

echo "== 5. Dockerfile / nginx =="
[ -f algo-service/Dockerfile ] && grep -q 'python:3.11-slim' algo-service/Dockerfile && ok "algo Dockerfile 基于 python:3.11-slim" || bad "algo Dockerfile 缺失或基础镜像不对"
[ -f algo-service/Dockerfile ] && grep -q 'dqn.npz' algo-service/Dockerfile && ok "algo Dockerfile 兜底 dqn.npz" || bad "algo Dockerfile 未兜底 dqn.npz"
[ -f frontend/Dockerfile ] && grep -q 'BUILDPLATFORM' frontend/Dockerfile && grep -q 'nginx:alpine' frontend/Dockerfile && ok "frontend 多阶段 Dockerfile" || bad "frontend Dockerfile 不符合多阶段约定"
[ -f frontend/nginx.conf ] && grep -q 'backend:8000' frontend/nginx.conf && grep -q 'Upgrade' frontend/nginx.conf && grep -q 'try_files' frontend/nginx.conf && ok "nginx.conf /api /ws SPA 齐全" || bad "nginx.conf 缺 /api 或 /ws Upgrade 或 try_files"
grep -q 'location = /health ' deploy/nginx.conf && grep -qE 'proxy_pass +\$backend_upstream/health;' deploy/nginx.conf && ok "deploy/nginx.conf /health 反代到 backend /health" || bad "deploy/nginx.conf 缺 location = /health 反代"
grep -q 'location = /healthz' deploy/nginx.conf && ok "deploy/nginx.conf /healthz 仍为 nginx 自身" || bad "deploy/nginx.conf 缺 /healthz"
# 传输与部署安全头（测试文档-接口与安全 §6）
for f in deploy/nginx.conf frontend/nginx.conf; do
  grep -qE '^\s*server_tokens\s+off;' "$f" && ok "$f server_tokens off" || bad "$f 缺 server_tokens off"
  grep -q 'Content-Security-Policy' "$f" && ok "$f 有 CSP" || bad "$f 缺 Content-Security-Policy"
  grep -q 'map \$scheme \$hsts_header' "$f" && grep -q 'Strict-Transport-Security \$hsts_header' "$f" \
    && ok "$f HSTS 按 \$scheme 条件化（仅 https 下发）" || bad "$f 缺条件化 HSTS"
  grep -qE "connect-src 'self'" "$f" && ok "$f CSP 放行同源 /api 与 /ws" || bad "$f CSP 缺 connect-src 'self'"
  grep 'add_header Content-Security-Policy' "$f" | grep -qE "script-src[^;]*unsafe-(inline|eval)" && bad "$f CSP 的 script-src 放开了 unsafe-inline/eval" || ok "$f CSP script-src 未放开 unsafe-*"
  # SPA 回退不能带 $uri/：/assets 路由会被 301 进静态目录再 404
  grep -q 'try_files \$uri \$uri/ /index.html' "$f" && bad "$f SPA try_files 带了 \$uri/，/assets 路由会 404" || ok "$f SPA try_files 未带 \$uri/"
done
if diff -q deploy/nginx.conf frontend/nginx.conf >/dev/null 2>&1; then ok "frontend/nginx.conf 与 deploy/nginx.conf 一致"; else echo "WARN frontend/nginx.conf 与 deploy/nginx.conf 不一致（frontend 镜像 COPY 的是 frontend/nginx.conf，需 frontend Agent 同步：cp deploy/nginx.conf frontend/nginx.conf）"; fi
grep -q "/health'" docker-compose.yml && ! grep -q "api/v1/health'" docker-compose.yml && ok "backend healthcheck 探根路径 /health" || bad "backend healthcheck 未探根路径 /health"
for v in REDIS_DB JWT_ALGORITHM KEY_CUSTODY_SECRET ALGO_TIMEOUT DEBUG DB_WAIT_TIMEOUT; do grep -qE "^\s+$v: " docker-compose.yml && grep -qE "^$v=" .env.example && ok "backend 非契约变量 $v 已传递（compose + .env.example）" || bad "backend 非契约变量 $v 未传递"; done

echo "== 6. install.sh 九步要点 =="
I=packaging/install.sh
if [ -f "$I" ]; then
  grep -q 'armv7l' "$I" && ok "armv7l 拒绝" || bad "缺 armv7l 拒绝"
  grep -qE 'openssl rand|/dev/urandom' "$I" && ok "随机密钥（openssl rand）" || bad "缺随机密钥生成"
  grep -q '180' "$I" && grep -qE 'FRONTEND_PORT\}/health"' "$I" && ok "180 秒健康轮询（/health）" || bad "缺 180s /health 轮询"
  grep -qE 'HEALTH_URL=.*api/v1/health' "$I" && bad "install.sh 仍轮询 /api/v1/health（backend 该路径 404，应为根路径 /health）"
  grep -q 'KEY_CUSTODY_SECRET=' "$I" && ok "随机生成 KEY_CUSTODY_SECRET" || bad "install.sh 未生成 KEY_CUSTODY_SECRET"
  for a in 'admin / admin123' 'grid / grid123' 'vpp / vpp123' 'subject / subject123' 'regulator / reg123' 'edge / edge123'; do grep -q "$a" "$I" && ok "输出账号 $a" || bad "缺演示账号输出 $a"; done
  grep -qE 'EUID|id -u' "$I" && ok "root 检查" || bad "缺 root 检查"
  grep -qE '3\.5|3584|3500' "$I" && ok "内存 3.5GB 检查" || bad "缺内存检查"
  grep -qE '80|8000|8100|3306|6379' "$I" && grep -qiE 'ss -|netstat|lsof' "$I" && ok "端口占用检查" || bad "缺端口占用检查"
  grep -q 'docker load' "$I" && grep -qiE 'sha256' "$I" && ok "docker load + SHA 校验" || bad "缺 docker load / SHA 校验"
  grep -q 'systemctl' "$I" && grep -q 'enable' "$I" && ok "systemd enable" || bad "缺 systemd enable"
  grep -q 'logs --tail=50' "$I" && ok "超时打印 logs --tail=50" || bad "超时未打印 logs --tail=50"
else bad "packaging/install.sh 缺失"; fi
U=packaging/uninstall.sh
[ -f "$U" ] && grep -q -- '--purge' "$U" && ok "uninstall.sh --purge" || bad "uninstall.sh 缺失或无 --purge"
B=packaging/build.sh
[ -f "$B" ] && grep -q -- '--skip-backend' "$B" && grep -qE 'amd64\|arm64\|all|amd64.*arm64.*all' "$B" && ok "build.sh --arch/--skip-backend" || bad "build.sh 缺 --arch 或 --skip-backend"
for f in energy-tds.service energy-tds-kiosk.service mysql-amd64.cnf mysql-arm64.cnf; do [ -f packaging/assets/$f ] && ok "assets/$f" || bad "assets/$f 缺失"; done

[ "$FAIL" = 0 ] && echo "== deploy check: ALL PASS ==" || echo "== deploy check: FAILED =="
exit $FAIL
