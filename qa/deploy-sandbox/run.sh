#!/usr/bin/env bash
# ============================================================
# 部署脚本沙箱实测：在「无 root、无 docker.sock」的机器上，用假命令（fakebin/）跑通
# packaging/build.sh、install.sh、uninstall.sh 的全部分支，并静态校验 compose / nginx / systemd / Dockerfile / 文档。
#
#   bash qa/deploy-sandbox/run.sh            # 跑全部场景，末尾输出 PASS/FAIL 表，任一 FAIL 退出码 1
#   bash qa/deploy-sandbox/run.sh -v         # 失败时打印该场景完整输出
#   SANDBOX_WORK=/path bash qa/deploy-sandbox/run.sh   # 指定工作目录（默认 mktemp，结束后保留供排查）
#
# 原理：
#   - PATH 只包含 fakebin/（假 docker/systemctl/free/df/uname/ss/curl/id/...）+ 沙箱 bin/ + realbin/（真 tar/sed/openssl 等软链）
#   - install.sh / uninstall.sh / build.sh 的系统路径用 ENERGY_TDS_* 环境变量重定向到沙箱目录
#   - 假命令把每次调用写进 $FAKE_LOG，断言据此验证「调用顺序 / 是否调用」
# ============================================================
set -uo pipefail

SB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SB_DIR}/../.." && pwd)"
VERBOSE=0; [[ "${1:-}" == "-v" ]] && VERBOSE=1
WORK="${SANDBOX_WORK:-$(mktemp -d -t energy-tds-sandbox.XXXXXX)}"
mkdir -p "${WORK}"
REALBIN="${WORK}/realbin"
FAKEBIN="${SB_DIR}/fakebin"
FAKEBIN_ARM="${SB_DIR}/fakebin-arm"

# ---------- 结果表 ----------
declare -a RESULTS=()
PASS_N=0; FAIL_N=0
CUR=""; CUR_OUT=""; CUR_FAILS=""
begin() { CUR="$1"; CUR_OUT="${WORK}/out/$(echo "$1" | tr ' /:' '___').log"; CUR_FAILS=""; mkdir -p "${WORK}/out"; : > "${CUR_OUT}"; }
fail()  { CUR_FAILS+="    - $*"$'\n'; }
finish() {
  if [[ -z "${CUR_FAILS}" ]]; then RESULTS+=("PASS  ${CUR}"); PASS_N=$((PASS_N+1))
  else RESULTS+=("FAIL  ${CUR}"$'\n'"${CUR_FAILS%$'\n'}"); FAIL_N=$((FAIL_N+1))
       [[ ${VERBOSE} -eq 1 ]] && { echo "----- 输出（${CUR}）-----"; cat "${CUR_OUT}"; echo "-----"; }
  fi
}
# 断言工具
a_grep()    { grep -qE -- "$1" "${2:-${CUR_OUT}}" || fail "期望输出含 /$1/"; }
a_nogrep()  { grep -qE -- "$1" "${2:-${CUR_OUT}}" && fail "不应出现 /$1/"; return 0; }
a_rc()      { [[ "$1" -eq "$2" ]] || fail "退出码期望 $2，实际 $1"; }
a_file()    { [[ -e "$1" ]] || fail "缺少文件 $1"; }
a_nofile()  { [[ -e "$1" ]] && fail "不应存在 $1"; return 0; }
a_log()     { grep -qE -- "$1" "${FAKE_LOG}" || fail "假命令日志缺少 /$1/"; }
a_nolog()   { grep -qE -- "$1" "${FAKE_LOG}" && fail "假命令日志不应含 /$1/"; return 0; }

# ---------- realbin：只放真实需要的工具 ----------
mkdir -p "${REALBIN}"
for t in bash sh grep cut sed awk tr tail head mkdir install tar rm cp mv chmod sha256sum openssl od seq mktemp dirname basename \
         cat printf date gzip gunzip du find ls touch sort env readlink python3 xargs wc tee true false test diff stat ln; do
  p="$(command -v "$t" 2>/dev/null)"; [[ -n "$p" ]] && ln -sf "$p" "${REALBIN}/$t"
done

# ---------- 每个场景独立的沙箱环境 ----------
# sandbox_env NAME  → 设置 SBX（沙箱根）、FAKE_LOG、FAKE_STATE、ENERGY_TDS_* 变量并导出
sandbox_env() {
  SBX="${WORK}/sbx/$1"; rm -rf "${SBX}"; mkdir -p "${SBX}/state" "${SBX}/bin" "${SBX}/systemd" "${SBX}/opt" "${SBX}/plugins"
  export FAKE_LOG="${SBX}/calls.log" FAKE_STATE="${SBX}/state"
  : > "${FAKE_LOG}"
  export ENERGY_TDS_INSTALL_DIR="${SBX}/opt/energy-tds" ENERGY_TDS_SYSTEMD_DIR="${SBX}/systemd" ENERGY_TDS_BIN_DIR="${SBX}/bin" \
         ENERGY_TDS_CLI_PLUGIN_DIRS="${SBX}/plugins" ENERGY_TDS_DPHYS_SWAPFILE="${SBX}/dphys-swapfile"
  # 默认：x86_64、内存 3900MB、磁盘 100G、docker 已装且守护进程可用、compose 可用、端口空闲、健康第 1 次即通过
  export FAKE_ARCH=x86_64 FAKE_MEM_MB=3900 FAKE_DISK_GB=100 FAKE_DOCKER_DAEMON=1 FAKE_COMPOSE=1 FAKE_BUSY_PORTS="" \
         FAKE_HEALTH_OK_AT=1 FAKE_UID=0 FAKE_OLD_RUNNING=0 FAKE_LOAD_FAIL=0 FAKE_COMPOSE_UP_FAIL=0 FAKE_USERS="pi stu" \
         FAKE_NO_ARM64_IMAGES="" FAKE_IMAGETOOLS_UNAVAILABLE=0 FAKE_NO_ARM64_BUILDER=0 FAKE_OFFLINE=0 FAKE_BUILD_FAIL=0 SUDO_USER=pi
  unset ENERGY_TDS_HEALTH_TIMEOUT
}
# 运行命令并捕获输出到 CUR_OUT；$1 = "nodocker" 时 PATH 不含假 docker（模拟目标机没装 Docker），$1 = "arm" 时附加 chromium
run_in() {
  local mode="$1"; shift
  local path="${FAKEBIN}:${SBX}/bin:${REALBIN}"
  [[ "${mode}" == nodocker ]] && { mkdir -p "${SBX}/fakebin-nodocker"; for f in "${FAKEBIN}"/*; do [[ "$(basename "$f")" == docker ]] || ln -sf "$f" "${SBX}/fakebin-nodocker/"; done; path="${SBX}/fakebin-nodocker:${SBX}/bin:${REALBIN}"; }
  [[ "${mode}" == arm ]] && path="${FAKEBIN_ARM}:${path}"
  PATH="${path}" "$@" >> "${CUR_OUT}" 2>&1
}

echo "工作目录：${WORK}"
echo

# ============================================================
# A. 静态检查
# ============================================================
begin "A1 bash -n / shellcheck 全部脚本"
for f in "${ROOT_DIR}"/deploy/*.sh "${ROOT_DIR}"/packaging/*.sh "${ROOT_DIR}"/packaging/assets/*.sh "${SB_DIR}"/run.sh; do
  bash -n "$f" >> "${CUR_OUT}" 2>&1 || fail "bash -n 失败：$f"
done
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S warning "${ROOT_DIR}"/deploy/*.sh "${ROOT_DIR}"/packaging/*.sh "${ROOT_DIR}"/packaging/assets/*.sh >> "${CUR_OUT}" 2>&1 || fail "shellcheck 有 warning 及以上告警"
else echo "shellcheck 未安装，跳过" >> "${CUR_OUT}"; fi
finish

begin "A2 compose 严格校验（python yaml ⨯ 契约第四部分）"
python3 "${SB_DIR}/check_compose.py" "${ROOT_DIR}" >> "${CUR_OUT}" 2>&1 || fail "check_compose.py 报错（见输出）"
finish

begin "A3 docker compose config（根 compose / dev compose，无 .env）"
if docker compose version >/dev/null 2>&1; then
  (cd "${ROOT_DIR}" && docker compose --env-file .env.example -f docker-compose.yml config -q) >> "${CUR_OUT}" 2>&1 || fail "docker-compose.yml config 失败"
  (cd "${ROOT_DIR}" && docker compose -f docker-compose.dev.yml config -q) >> "${CUR_OUT}" 2>&1 || fail "docker-compose.dev.yml config 失败"
  (cd "${ROOT_DIR}" && docker compose -f docker-compose.dev.yml --profile mock config -q) >> "${CUR_OUT}" 2>&1 || fail "dev compose --profile mock config 失败"
else echo "本机无 docker compose，跳过" >> "${CUR_OUT}"; fi
finish

begin "A4 nginx.conf 语法（nginx -t）与内容"
NG="${WORK}/nginx"; mkdir -p "${NG}/logs"
if command -v nginx >/dev/null 2>&1; then
  sed 's/listen       80;/listen 18080;/' "${ROOT_DIR}/frontend/nginx.conf" > "${NG}/default.conf"
  cat > "${NG}/nginx.conf" <<EOF
pid ${NG}/nginx.pid; error_log ${NG}/logs/error.log;
events {}
http {
  include /etc/nginx/mime.types; access_log ${NG}/logs/access.log;
  client_body_temp_path ${NG}/cb; proxy_temp_path ${NG}/pt; fastcgi_temp_path ${NG}/ft; uwsgi_temp_path ${NG}/ut; scgi_temp_path ${NG}/st;
  include ${NG}/default.conf;
}
EOF
  nginx -t -c "${NG}/nginx.conf" >> "${CUR_OUT}" 2>&1 || fail "nginx -t 失败"
else echo "本机无 nginx，跳过 -t" >> "${CUR_OUT}"; fi
# deploy/nginx.conf 是源（乙方 deploy Agent 维护），frontend/nginx.conf 是镜像 COPY 的副本，两者必须一致；
# 不一致时这里只警告并提示同步命令（frontend/ 归前端 Agent），内容断言以 deploy/nginx.conf 为准，并对它再跑一次 nginx -t
NC="${ROOT_DIR}/deploy/nginx.conf"
if command -v nginx >/dev/null 2>&1 && ! diff -q "${ROOT_DIR}/frontend/nginx.conf" "${NC}" >/dev/null; then
  sed 's/listen       80;/listen 18080;/' "${NC}" > "${NG}/default.conf"
  nginx -t -c "${NG}/nginx.conf" >> "${CUR_OUT}" 2>&1 || fail "deploy/nginx.conf nginx -t 失败"
fi
# /health：反代到 backend 根路径 GET /health（install.sh / kiosk.sh 轮询）；/healthz 仍为 nginx 自身
grep -qE 'location = /health \{' "$NC" || fail "缺 location = /health"
awk '/location = \/health \{/,/\}/' "$NC" | grep -qE 'proxy_pass +\$backend_upstream/health;' || fail "/health 未反代到 \$backend_upstream/health"
awk '/location = \/healthz \{/,/\}/' "$NC" | grep -q "return 200" || fail "/healthz 应仍由 nginx 自身返回 200"
grep -qE 'location /api/ \{' "$NC" && grep -qE 'proxy_pass +\$backend_upstream;' "$NC" || fail "/api/ 未用无 URI 的 proxy_pass（需保留 /api 前缀）"
grep -qE 'proxy_pass +http://backend:8000/' "$NC" && fail "/api/ proxy_pass 带尾部 / 会剥掉前缀"
awk '/location \/ws/,/\}/' "$NC" | grep -q 'Upgrade *\$http_upgrade' || fail "/ws 缺 Upgrade 头"
awk '/location \/ws/,/\}/' "$NC" | grep -q 'Connection *\$connection_upgrade' || fail "/ws 缺 Connection upgrade 头"
grep -q 'try_files \$uri /index.html' "$NC" || fail "缺 SPA try_files"
# $uri/ 会让 /assets（资产中心路由）301 到 /assets/ 再撞上静态目录 location 的 =404，实测页面打不开
grep -q 'try_files \$uri \$uri/ /index.html' "$NC" && fail "SPA try_files 不应带 \$uri/：会把 /assets 路由 301 进静态目录导致 404"
grep -q 'gzip_types' "$NC" && grep -q 'application/javascript' "$NC" && grep -q 'application/json' "$NC" || fail "gzip_types 不含 js/json"
# 安全头必须在每个自己写了 add_header 的 location 里重复（nginx add_header 不继承）
loc_block() { awk -v pat="$1" 'index($0,pat){f=1} f{print} f&&/^    \}/{f=0}' "$NC"; }
for loc in 'location / {' 'location /assets/ {' 'location = /mockServiceWorker.js {'; do
  loc_block "$loc" | grep -q 'X-Frame-Options' || fail "${loc} 内缺 X-Frame-Options（add_header 不跨层继承）"
  loc_block "$loc" | grep -q 'X-Content-Type-Options' || fail "${loc} 内缺 X-Content-Type-Options"
  loc_block "$loc" | grep -q 'Referrer-Policy' || fail "${loc} 内缺 Referrer-Policy"
  loc_block "$loc" | grep -q 'Content-Security-Policy' || fail "${loc} 内缺 Content-Security-Policy"
  loc_block "$loc" | grep -q 'Strict-Transport-Security' || fail "${loc} 内缺 Strict-Transport-Security"
done
# 传输与部署安全（测试文档-接口与安全 §6）
grep -qE '^\s*server_tokens\s+off;' "$NC" || fail "缺 server_tokens off（响应头会回显 nginx 版本号）"
grep -q 'Content-Security-Policy' "$NC" || fail "缺 Content-Security-Policy"
grep -qE "connect-src 'self'" "$NC" || fail "CSP 缺 connect-src（/api 与 /ws 会被拦）"
grep -qE "style-src 'self' 'unsafe-inline'" "$NC" || fail "CSP 的 style-src 缺 'unsafe-inline'（Element Plus 行内样式会被拦，页面白屏）"
grep -qE "script-src 'self'" "$NC" || fail "CSP 缺 script-src"
grep 'add_header Content-Security-Policy' "$NC" | grep -qE "script-src[^;]*unsafe-(inline|eval)" && fail "CSP 的 script-src 不应放开 unsafe-inline/unsafe-eval"
grep -qE "object-src 'none'" "$NC" || fail "CSP 缺 object-src 'none'"
grep -qE "frame-ancestors 'self'" "$NC" || fail "CSP 缺 frame-ancestors"
# HSTS 必须条件化：http 上不发（浏览器忽略且无意义），https 才发
grep -q 'map \$scheme \$hsts_header' "$NC" || fail "HSTS 未按 \$scheme 条件化（应通过 map \$scheme \$hsts_header 实现）"
awk '/map \$scheme \$hsts_header/,/^\}/' "$NC" | grep -q 'max-age=31536000' || fail "HSTS max-age 应为 31536000"
grep -q 'Strict-Transport-Security \$hsts_header' "$NC" || fail "HSTS 头未使用 \$hsts_header 变量"
diff -q "${ROOT_DIR}/frontend/nginx.conf" "${ROOT_DIR}/deploy/nginx.conf" >/dev/null \
  || echo "WARN deploy/nginx.conf 与 frontend/nginx.conf 不一致：frontend 镜像 COPY 的是 frontend/nginx.conf，需前端 Agent 执行 cp deploy/nginx.conf frontend/nginx.conf" | tee -a "${CUR_OUT}"
finish

begin "A5 systemd 单元（systemd-analyze verify）与 kiosk 逻辑"
SD="${WORK}/systemd"; mkdir -p "${SD}"
cp "${ROOT_DIR}/packaging/assets/energy-tds.service" "${ROOT_DIR}/packaging/assets/docker.service" "${SD}/"
sed 's/__KIOSK_USER__/pi/g' "${ROOT_DIR}/packaging/assets/energy-tds-kiosk.service" > "${SD}/energy-tds-kiosk.service"
if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify --man=no "${SD}/energy-tds.service" "${SD}/energy-tds-kiosk.service" "${SD}/docker.service" >> "${CUR_OUT}" 2>&1 || fail "systemd-analyze verify 失败"
else echo "无 systemd-analyze，跳过" >> "${CUR_OUT}"; fi
grep -q '^Requires=docker.service' "${SD}/energy-tds.service" || fail "energy-tds.service 缺 Requires=docker.service"
grep -q '^After=.*docker.service' "${SD}/energy-tds.service" || fail "energy-tds.service 缺 After=docker.service"
grep -q '^ExecStart=/usr/bin/docker compose up -d' "${SD}/energy-tds.service" || fail "energy-tds.service ExecStart 不对"
grep -q '^User=pi' "${SD}/energy-tds-kiosk.service" || fail "kiosk User 未替换"
grep -q 'graphical-session.target' "${SD}/energy-tds-kiosk.service" && fail "系统级 kiosk 单元不应引用用户会话 target graphical-session.target"
grep -q '\$(' "${SD}/energy-tds-kiosk.service" && fail "kiosk 单元 ExecStart 内含 \$( —— systemd 不做 shell 展开，逻辑应放到脚本文件"
KS="${ROOT_DIR}/packaging/assets/energy-tds-kiosk.sh"
grep -q '"${URL}/health"' "$KS" || fail "kiosk.sh 未等待 /health"
grep -q '/api/v1/health"' "$KS" && fail "kiosk.sh 仍探 /api/v1/health（后端 404）"
grep -q -- '--kiosk' "$KS" && grep -q -- '--disable-gpu-compositing' "$KS" || fail "kiosk.sh 缺 --kiosk / --disable-gpu-compositing"
# kiosk.sh 行为实测：健康第 3 次通过 → 只探测 3 次后拉起浏览器
sandbox_env kiosk; export FAKE_HEALTH_OK_AT=3 KIOSK_WAIT_SECONDS=60
run_in arm bash "$KS"; rc=$?; a_rc $rc 0
[[ "$(grep -c '^curl .*[0-9a-z]/health$' "${FAKE_LOG}")" -eq 3 ]] || fail "kiosk.sh 应探测 3 次后停止（实际 $(grep -c '^curl .*[0-9a-z]/health$' "${FAKE_LOG}")）"
a_grep '平台健康检查通过'
# 超时退路：永不健康 → 退回 /healthz 仍拉起
sandbox_env kiosk2; export FAKE_HEALTH_OK_AT=0 KIOSK_WAIT_SECONDS=20
run_in arm bash "$KS"; rc=$?; a_rc $rc 0; a_grep '后端仍未就绪'; a_log 'curl .*healthz'
unset KIOSK_WAIT_SECONDS
finish

begin "A6 Dockerfile / .dockerignore / npm lock 静态审查"
AD="${ROOT_DIR}/algo-service/Dockerfile"; FD="${ROOT_DIR}/frontend/Dockerfile"
grep -q '^FROM python:3.11-slim' "$AD" || fail "algo 基础镜像不是 python:3.11-slim"
[[ "$(grep -n 'COPY requirements.txt' "$AD" | cut -d: -f1)" -lt "$(grep -n '^COPY \. \.' "$AD" | cut -d: -f1)" ]] || fail "algo Dockerfile 应先 COPY requirements 再 COPY ."
grep -q 'chown -R algo:algo /app' "$AD" || fail "algo Dockerfile 未 chown /app（非 root 下 cache/ 不可写）"
grep -q '^USER algo' "$AD" || fail "algo 未以非 root 运行"
grep -q 'curl' "$AD" && fail "algo Dockerfile 不应依赖 curl"
for x in tests .venv __pycache__; do grep -qx "$x" "${ROOT_DIR}/algo-service/.dockerignore" || fail "algo .dockerignore 缺 $x"; done
for x in models data cache; do grep -qE "^/?${x}/?$" "${ROOT_DIR}/algo-service/.dockerignore" && fail "algo .dockerignore 不应排除 $x（镜像需要 dqn.npz/数据集/缓存）"; done
for x in node_modules dist tests; do grep -qx "$x" "${ROOT_DIR}/frontend/.dockerignore" || fail "frontend .dockerignore 缺 $x"; done
grep -q 'FROM --platform=\$BUILDPLATFORM node:20-alpine AS builder' "$FD" || fail "frontend builder 未用 \$BUILDPLATFORM"
grep -q '^FROM nginx:alpine' "$FD" || fail "frontend 运行阶段不是 nginx:alpine"
grep -q '^ARG VITE_USE_MOCK' "$FD" && grep -q 'VITE_USE_MOCK=\${VITE_USE_MOCK}' "$FD" || fail "VITE_USE_MOCK build-arg 未传给 ENV"
[[ "$(grep -n 'COPY package.json' "$FD" | cut -d: -f1)" -lt "$(grep -n '^COPY \. \.' "$FD" | cut -d: -f1)" ]] || fail "frontend 应先 COPY package*.json 再 COPY ."
grep -q 'npm ci' "$FD" || fail "frontend 未用 npm ci"
[[ -f "${ROOT_DIR}/frontend/package-lock.json" ]] || fail "frontend/package-lock.json 缺失"
grep -A3 '^  frontend:' "${ROOT_DIR}/docker-compose.yml" >/dev/null
if command -v npm >/dev/null 2>&1 && [[ -d "${ROOT_DIR}/frontend/node_modules" ]]; then
  (cd "${ROOT_DIR}/frontend" && npm ci --dry-run --ignore-scripts --no-audit --no-fund) >> "${CUR_OUT}" 2>&1 || fail "npm ci --dry-run 失败（lock 与 package.json 不一致）"
else echo "跳过 npm ci --dry-run（无 npm 或 node_modules）" >> "${CUR_OUT}"; fi
# 前端健康检查用的 wget 在 nginx:alpine（busybox）里有；algo healthcheck 用 python urllib
grep -A12 '^  algo-service:' "${ROOT_DIR}/docker-compose.yml" >/dev/null
finish

begin "A7 文档命令与脚本参数一致"
python3 - "${ROOT_DIR}" >> "${CUR_OUT}" 2>&1 <<'PY' || fail "文档中出现了脚本未定义的参数（见输出）"
import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
scripts = {"build.sh": "packaging/build.sh", "install.sh": "packaging/install.sh", "uninstall.sh": "packaging/uninstall.sh",
           "build-offline.sh": "deploy/build-offline.sh", "load-offline.sh": "deploy/load-offline.sh"}
defined = {k: set(re.findall(r"(--[a-z-]+)", (root / v).read_text(encoding="utf-8"))) for k, v in scripts.items()}
bad = 0
for doc in ["packaging/部署说明.md", "deploy/部署说明.md", "deploy/答辩演示剧本.md", "README.md", "docs/agent-notes/STATUS-deploy.md"]:
    t = (root / doc).read_text(encoding="utf-8")
    for m in re.finditer(r"(build-offline\.sh|load-offline\.sh|build\.sh|install\.sh|uninstall\.sh)((?:\s+(?:--[a-z-]+|-[a-z]|[A-Za-z0-9/.:_-]+))*)", t):
        name = m.group(1)
        for flag in re.findall(r"--[a-z-]+", m.group(2)):
            if flag not in defined[name]:
                print(f"FAIL {doc}: {name} {flag} 未在脚本中定义"); bad += 1
    print(f"ok   {doc}")
sys.exit(1 if bad else 0)
PY
grep -q -- '--skip-backend' "${ROOT_DIR}/README.md" || fail "README 未提 --skip-backend"
grep -q 'systemctl start|stop|status energy-tds' "${ROOT_DIR}/packaging/部署说明.md" || fail "安装包部署说明缺 systemctl 管理命令"
finish

# ============================================================
# B. build.sh（假 docker 跑完整十步）
# ============================================================
# 构造打包用源码树：只放 build.sh 需要的文件（不能在真仓库里创建 backend/ —— 那是甲方目录）
make_src_tree() {   # $1 = 目标根, $2 = with-backend|no-backend
  local t="$1"; rm -rf "$t"; mkdir -p "$t"
  cp -r "${ROOT_DIR}/packaging" "$t/"
  mkdir -p "$t/algo-service/models" "$t/algo-service/data" "$t/algo-service/cache" "$t/algo-service/scripts" "$t/frontend" "$t/deploy/mysql"
  cp "${ROOT_DIR}/algo-service/Dockerfile" "$t/algo-service/"; cp "${ROOT_DIR}/algo-service/requirements.txt" "$t/algo-service/"
  cp "${ROOT_DIR}"/algo-service/models/dqn.npz "$t/algo-service/models/" 2>/dev/null || echo fake > "$t/algo-service/models/dqn.npz"
  cp "${ROOT_DIR}"/algo-service/data/node_datasets.npz "$t/algo-service/data/" 2>/dev/null || echo fake > "$t/algo-service/data/node_datasets.npz"
  cp "${ROOT_DIR}/frontend/Dockerfile" "${ROOT_DIR}/frontend/nginx.conf" "$t/frontend/"
  cp "${ROOT_DIR}/docker-compose.yml" "${ROOT_DIR}/.env.example" "$t/"
  cp "${ROOT_DIR}/deploy/mysql/mysql.cnf" "$t/deploy/mysql/"
  if [[ "$2" == with-backend ]]; then
    mkdir -p "$t/backend/sql"; printf 'FROM python:3.11-slim\n' > "$t/backend/Dockerfile"
    echo 'CREATE TABLE t(id INT);' > "$t/backend/sql/01_schema.sql"; echo 'INSERT INTO t VALUES(1);' > "$t/backend/sql/02_seed.sql"
  fi
}
run_build() {   # $1 = 源码树, 其余 = build.sh 参数；BUILD/DIST 落在源码树下
  export ENERGY_TDS_BUILD_DIR="$1/build" ENERGY_TDS_DIST_DIR="$1/dist"
  local t="$1"; shift
  run_in normal bash "$t/packaging/build.sh" "$@"
}
# 校验一个解压后的包目录结构是否与规范 §三 一致
check_pkg_layout() {   # $1 = 包目录, $2 = arch(amd64|arm64), $3 = with-backend|no-backend
  local p="$1"
  for f in install.sh uninstall.sh VERSION 部署说明.md images/mysql.tar.gz images/redis.tar.gz images/algo-service.tar.gz images/frontend.tar.gz \
           images/SHA256SUMS docker/docker-$2.tgz docker/docker-compose-$2 docker/docker.service config/docker-compose.yml config/.env.example \
           config/mysql.cnf systemd/energy-tds.service; do a_file "$p/$f"; done
  [[ -d "$p/sql" ]] || fail "缺 sql/ 目录"
  [[ -x "$p/install.sh" && -x "$p/uninstall.sh" ]] || fail "install.sh / uninstall.sh 不可执行"
  if [[ "$3" == with-backend ]]; then a_file "$p/images/backend.tar.gz"; a_file "$p/sql/01_schema.sql"; a_file "$p/sql/02_seed.sql"
  else a_nofile "$p/images/backend.tar.gz"; fi
  if [[ "$2" == arm64 ]]; then a_file "$p/systemd/energy-tds-kiosk.service"; a_file "$p/systemd/energy-tds-kiosk.sh"
  else a_nofile "$p/systemd/energy-tds-kiosk.service"; fi
  # 多余文件检查：顶层只允许规范列出的条目
  for e in "$p"/* "$p"/.[!.]*; do [[ -e "$e" ]] || continue
    case "$(basename "$e")" in install.sh|uninstall.sh|VERSION|images|docker|config|sql|systemd|部署说明.md) ;; *) fail "包内多余顶层条目 $(basename "$e")" ;; esac
  done
  # VERSION
  grep -q "^ARCH=$2$" "$p/VERSION" || fail "VERSION 缺 ARCH=$2"
  grep -q '^VERSION=' "$p/VERSION" && grep -q '^BUILD_TIME=' "$p/VERSION" && grep -q '^DB_IMAGE=' "$p/VERSION" || fail "VERSION 缺 VERSION/BUILD_TIME/DB_IMAGE"
  [[ "$(grep -c '^IMAGE_SHA256 ' "$p/VERSION")" -eq "$(ls "$p"/images/*.tar.gz | wc -l)" ]] || fail "VERSION 的 IMAGE_SHA256 条数与镜像数不符"
  (cd "$p/images" && sha256sum -c --quiet SHA256SUMS) >> "${CUR_OUT}" 2>&1 || fail "images/SHA256SUMS 校验不过"
  # 包内 compose
  grep -q '^    build:' "$p/config/docker-compose.yml" && fail "包内 compose 含 build: 块"
  grep -q './sql:/docker-entrypoint-initdb.d:ro' "$p/config/docker-compose.yml" || fail "包内 compose 未改为 ./sql 挂载"
  grep -q './config/mysql.cnf:' "$p/config/docker-compose.yml" || fail "包内 compose 未改为 ./config/mysql.cnf"
  python3 - "$p/config/docker-compose.yml" >> "${CUR_OUT}" 2>&1 <<'PY' || fail "包内 compose yaml 解析 / 结构错误"
import sys,yaml
d=yaml.safe_load(open(sys.argv[1]))
svcs=d["services"]; assert set(svcs)=={"mysql","redis","backend","algo-service","frontend"}, set(svcs)
for n,s in svcs.items():
    assert "build" not in s, n; assert "image" in s, n
PY
  if docker compose version >/dev/null 2>&1; then
    (cd "$p/config" && docker compose --env-file .env.example -f docker-compose.yml config -q) >> "${CUR_OUT}" 2>&1 || fail "包内 compose docker compose config 失败"
  fi
  # 镜像 tar.gz 真实可解
  for f in "$p"/images/*.tar.gz; do gzip -t "$f" 2>>"${CUR_OUT}" || fail "$(basename "$f") 不是有效 gzip"; done
  # docker 静态包内容
  tar -tzf "$p/docker/docker-$2.tgz" | grep -q '^docker/docker$' || fail "docker-$2.tgz 内无 docker/docker"
}

SRC1="${WORK}/src-full"; make_src_tree "${SRC1}" with-backend
begin "B1 build.sh --arch all 完整十步（含 backend）"
sandbox_env build1
run_build "${SRC1}" --arch all; rc=$?; a_rc $rc 0
for i in 1 2 3 4 5 6 7 8 9 10; do a_grep "第 $i 步 / 10"; done
a_grep '跳过训练'                                  # dqn.npz 已存在
a_grep '未设置 DEEPSEEK_API_KEY'
# buildx --output type=docker,dest=
for svc in backend algo-service frontend; do for arch in amd64 arm64; do
  a_log "docker buildx build --platform linux/${arch} --output type=docker,dest=.*/${svc}-${arch}.tar -t energy-tds/${svc}:1.0"
done; done
a_log 'buildx build .* --build-arg VITE_USE_MOCK=false .*frontend'
# 坑 B：pull → save → rmi 串行，且每轮 rmi 在下一轮 pull 之前
seqf="${WORK}/out/b1-pullsave.txt"; grep -E '^docker (pull|save|rmi) ' "${FAKE_LOG}" > "${seqf}"
expected=$'docker pull --platform linux/amd64 mysql:8.0\ndocker save mysql:8.0\ndocker rmi -f mysql:8.0\ndocker pull --platform linux/amd64 redis:7-alpine\ndocker save redis:7-alpine\ndocker rmi -f redis:7-alpine\ndocker pull --platform linux/arm64 mysql:8.0\ndocker save mysql:8.0\ndocker rmi -f mysql:8.0\ndocker pull --platform linux/arm64 redis:7-alpine\ndocker save redis:7-alpine\ndocker rmi -f redis:7-alpine'
[[ "$(cat "${seqf}")" == "${expected}" ]] || fail "pull/save/rmi 顺序不符（见 ${seqf}）"
# 第 8 步下载：两个架构各自的静态包与 compose
a_log 'curl .*download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz'
a_log 'curl .*download.docker.com/linux/static/stable/aarch64/docker-27.5.1.tgz'
a_log 'curl .*docker-compose-linux-x86_64'; a_log 'curl .*docker-compose-linux-aarch64'
# 产物
for s in x86_64 arm64; do a_file "${SRC1}/dist/energy-tds-v1.0-linux-${s}.tar.gz"; a_file "${SRC1}/dist/energy-tds-v1.0-linux-${s}.tar.gz.sha256"; done
(cd "${SRC1}/dist" && sha256sum -c --quiet ./*.sha256) >> "${CUR_OUT}" 2>&1 || fail "dist/*.sha256 校验失败"
for s in x86_64 arm64; do
  [[ "$(tar -tzf "${SRC1}/dist/energy-tds-v1.0-linux-${s}.tar.gz" | head -1)" == "energy-tds-v1.0-linux-${s}/" ]] || fail "tar 顶层目录应为 energy-tds-v1.0-linux-${s}/"
done
PKG_X86="${SRC1}/build/energy-tds-v1.0-linux-x86_64"; PKG_ARM="${SRC1}/build/energy-tds-v1.0-linux-arm64"
check_pkg_layout "${PKG_X86}" amd64 with-backend
check_pkg_layout "${PKG_ARM}"  arm64 with-backend
grep -q 'performance_schema *= *OFF' "${PKG_ARM}/config/mysql.cnf" && grep -q 'innodb_buffer_pool_size *= *128M' "${PKG_ARM}/config/mysql.cnf" || fail "ARM 包 mysql.cnf 不是低配版"
grep -q 'innodb_buffer_pool_size *= *256M' "${PKG_X86}/config/mysql.cnf" || fail "x86 包 mysql.cnf 不是标准版"
grep -q '^DB_IMAGE=mysql:8.0$' "${PKG_X86}/VERSION" || fail "VERSION DB_IMAGE 应为 mysql:8.0"
grep -q 'image: mysql:8.0' "${PKG_X86}/config/docker-compose.yml" || fail "包内 compose 应保持 mysql:8.0"
# 第二次构建复用下载缓存
run_build "${SRC1}" --arch amd64; rc=$?; a_rc $rc 0; a_grep '已缓存 docker-27.5.1-x86_64.tgz'
finish

begin "B2 build.sh --skip-backend --arch amd64（无 backend/）"
SRC2="${WORK}/src-nobackend"; make_src_tree "${SRC2}" no-backend
sandbox_env build2
run_build "${SRC2}" --arch amd64 --skip-backend; rc=$?; a_rc $rc 0
a_grep 'backend/ 不存在，已指定 --skip-backend'; a_grep '仅构建 amd64，跳过 arm64 验证'
a_nolog 'buildx build .* -t energy-tds/backend'
check_pkg_layout "${SRC2}/build/energy-tds-v1.0-linux-x86_64" amd64 no-backend
grep -q '^SKIP_BACKEND=1$' "${SRC2}/build/energy-tds-v1.0-linux-x86_64/VERSION" || fail "VERSION 应记录 SKIP_BACKEND=1"
PKG_SKIP="${SRC2}/build/energy-tds-v1.0-linux-x86_64"
finish

begin "B3 build.sh 缺 backend/ 且无 --skip-backend → 第 1 步明确报错"
sandbox_env build3
run_build "${SRC2}" --arch amd64; rc=$?; a_rc $rc 1; a_grep '缺少 backend/ 目录'; a_grep '\-\-skip-backend'
a_nolog 'buildx build'
finish

begin "B4 mysql:8.0 无 arm64 → 两架构整体切 mariadb:11（含 healthcheck / cnf 适配）"
SRC4="${WORK}/src-mariadb"; make_src_tree "${SRC4}" with-backend
sandbox_env build4; export FAKE_NO_ARM64_IMAGES="mysql:8.0"
run_build "${SRC4}" --arch all; rc=$?; a_rc $rc 0
a_grep 'mysql:8.0 : 无 linux/arm64'; a_grep '整体切换为 mariadb:11'
a_log 'docker pull --platform linux/amd64 mariadb:11'; a_log 'docker pull --platform linux/arm64 mariadb:11'; a_nolog 'docker pull .*mysql:8.0'
for s in x86_64 arm64; do
  p="${SRC4}/build/energy-tds-v1.0-linux-${s}"
  grep -q 'image: mariadb:11' "$p/config/docker-compose.yml" || fail "[$s] 包内 compose 未切 mariadb"
  grep -q 'image: mysql:8.0' "$p/config/docker-compose.yml" && fail "[$s] 包内 compose 仍有 mysql:8.0"
  grep -q 'healthcheck.sh --connect --innodb_initialized' "$p/config/docker-compose.yml" || fail "[$s] mariadb 包 healthcheck 未换成 healthcheck.sh"
  grep -q '^DB_IMAGE=mariadb:11$' "$p/VERSION" || fail "[$s] VERSION DB_IMAGE 应为 mariadb:11"
  # mysql.cnf 对 mariadb 的适配：MySQL 专有变量必须带 loose- 前缀，否则 mariadb 启动失败
  grep -qE '^log_error_verbosity' "$p/config/mysql.cnf" && fail "[$s] mysql.cnf 含 MySQL 专有 log_error_verbosity 且无 loose- 前缀（mariadb 会拒绝启动）"
  grep -qE 'default.authentication.plugin|mysqlx|caching_sha2' "$p/config/mysql.cnf" && fail "[$s] mysql.cnf 含 mariadb 不识别的选项"
  (cd "$p/config" && docker compose --env-file .env.example -f docker-compose.yml config -q) >> "${CUR_OUT}" 2>&1 || fail "[$s] mariadb 包 compose config 失败"
done
# --db-image 强制时不自动切换
sandbox_env build4b; export FAKE_NO_ARM64_IMAGES="mysql:8.0"
run_build "${SRC4}" --arch arm64 --db-image mysql:8.0 --skip-dqn-train; rc=$?; a_rc $rc 0
grep -q '^DB_IMAGE=mysql:8.0$' "${SRC4}/build/energy-tds-v1.0-linux-arm64/VERSION" || fail "--db-image 强制时不应自动切换"
grep -q 'mysqladmin ping' "${SRC4}/build/energy-tds-v1.0-linux-arm64/config/docker-compose.yml" || fail "--db-image mysql 时 healthcheck 应保持 mysqladmin"

finish

begin "B5 imagetools 不可用 → 退回 Docker Hub registry API 判定 arm64"
sandbox_env build5; export FAKE_IMAGETOOLS_UNAVAILABLE=1 FAKE_NO_ARM64_IMAGES="mysql:8.0"
run_build "${SRC4}" --arch arm64; rc=$?; a_rc $rc 0
a_log 'curl .*auth.docker.io/token'; a_log 'curl .*registry-1.docker.io/v2/library/mysql/manifests/8.0'
a_grep 'redis:7-alpine : 有 linux/arm64'; a_grep '整体切换为 mariadb:11'
finish

begin "B6 build.sh 失败分支：builder 无 arm64 / 断网 / buildx build 失败 / 磁盘不足"
sandbox_env build6a; export FAKE_NO_ARM64_BUILDER=1
run_build "${SRC1}" --arch arm64; rc=$?; a_rc $rc 1; a_grep '缺少 arm64 构建能力'; a_grep 'tonistiigi/binfmt'
sandbox_env build6b; export FAKE_OFFLINE=1
run_build "${SRC1}" --arch amd64; rc=$?; a_rc $rc 1; a_grep '需要联网'
sandbox_env build6c; export FAKE_BUILD_FAIL=1
run_build "${SRC1}" --arch amd64; rc=$?; a_rc $rc 1; a_nolog 'docker pull'
sandbox_env build6d; export FAKE_DISK_GB=10
run_build "${SRC1}" --arch amd64; rc=$?; a_rc $rc 1; a_grep '不足 25GB'
sandbox_env build6e
run_build "${SRC1}" --arch mips; rc=$?; a_rc $rc 1; a_grep '\-\-arch 只能是'
finish

begin "B7 build.sh --dry-run --arch all --skip-backend 不产生任何 docker 调用与产物"
SRC7="${WORK}/src-dry"; make_src_tree "${SRC7}" no-backend
sandbox_env build7
run_build "${SRC7}" --arch all --skip-backend --dry-run; rc=$?; a_rc $rc 0
for i in 1 2 3 4 5 6 7 8 9 10; do a_grep "第 $i 步 / 10"; done
a_nolog 'docker (buildx build|pull|save)'
a_nofile "${SRC7}/dist/energy-tds-v1.0-linux-x86_64.tar.gz"
finish

# ============================================================
# C. install.sh（使用 B1 产出的真实包目录）
# ============================================================
INST="${PKG_X86}/install.sh"; INST_ARM="${PKG_ARM}/install.sh"
unset ENERGY_TDS_BUILD_DIR ENERGY_TDS_DIST_DIR

begin "C1 非 root → 提示 sudo"
sandbox_env c1; export FAKE_UID=1000
run_in normal bash "${INST}"; rc=$?; a_rc $rc 1; a_grep 'sudo ./install.sh'; a_nogrep '第 2 步'
finish

begin "C2 armv7l → 立即退出并提示重刷 64 位系统"
sandbox_env c2; export FAKE_ARCH=armv7l
run_in normal bash "${INST_ARM}"; rc=$?; a_rc $rc 1
a_grep '32 位 ARM'; a_grep 'Raspberry Pi OS \(64-bit\)'; a_nogrep '第 3 步'; a_nolog '^docker'; a_nolog '^free'
finish

begin "C3 架构不匹配（aarch64 机器装 x86_64 包）"
sandbox_env c3; export FAKE_ARCH=aarch64
run_in normal bash "${INST}"; rc=$?; a_rc $rc 1; a_grep '本包为 amd64，当前机器为 arm64'; a_grep 'energy-tds-v1.0-linux-arm64.tar.gz'
finish

begin "C4 内存不足 → 打印实际值"
sandbox_env c4; export FAKE_MEM_MB=2048
run_in normal bash "${INST}"; rc=$?; a_rc $rc 1; a_grep '内存不足：当前 2048MB，要求 ≥ 3500MB'; a_nogrep '第 4 步'
finish

begin "C5 磁盘不足 → 打印实际值"
sandbox_env c5; export FAKE_DISK_GB=5
run_in normal bash "${INST}"; rc=$?; a_rc $rc 1; a_grep '磁盘不足：/opt 剩余 5GB'; a_grep '要求均 ≥ 8GB'
finish

begin "C6 目标机无 Docker → 离线安装静态二进制 + systemctl enable --now docker"
sandbox_env c6; export FAKE_DOCKER_DAEMON=0
run_in nodocker bash "${INST}"; rc=$?; a_rc $rc 0
a_grep '未检测到可用 Docker，开始离线安装'; a_grep 'Docker 离线安装完成：27.5.1'
a_file "${SBX}/bin/docker"; a_file "${SBX}/bin/dockerd"; a_file "${SBX}/bin/containerd"; a_file "${SBX}/systemd/docker.service"
a_log '^systemctl daemon-reload'; a_log '^systemctl enable --now docker'; a_log '^groupadd docker'
# 安装后 docker 由 BIN_DIR 的（假）二进制提供且继续完成了后续步骤
a_grep '安装完成'
finish

begin "C7 有 docker 命令但守护进程未运行 → systemctl 启动"
sandbox_env c7; export FAKE_DOCKER_DAEMON=0
run_in normal bash "${INST}"; rc=$?; a_rc $rc 0; a_grep '守护进程未运行，尝试启动'; a_log '^systemctl enable --now docker'; a_nogrep '开始离线安装'
finish

begin "C8 缺 compose 插件 → 从包内安装到 cli-plugins"
sandbox_env c8; export FAKE_COMPOSE=0
run_in normal bash "${INST}"; rc=$?; a_rc $rc 0; a_file "${SBX}/plugins/docker-compose"; a_grep '已安装 docker compose 插件'
finish

begin "C9 端口 80 被占用 → 列出占用进程并退出；8000 被占用仅警告"
sandbox_env c9; export FAKE_BUSY_PORTS="80:nginx:1234"
run_in normal bash "${INST}"; rc=$?; a_rc $rc 1; a_grep '端口 80 已被占用'; a_grep '"nginx",pid=1234'; a_grep '\-\-port <其它端口>'; a_nolog 'docker load'
sandbox_env c9b; export FAKE_BUSY_PORTS="8000:python3:999 3306:mysqld:555"
run_in normal bash "${INST}"; rc=$?; a_rc $rc 0; a_grep '端口 8000 被占用'; a_grep '"mysqld",pid=555'; a_grep '不影响安装'
finish

begin "C10 --port 8080 绕过 80 占用，.env 与健康 URL 使用新端口"
sandbox_env c10; export FAKE_BUSY_PORTS="80:nginx:1234"
run_in normal bash "${INST}" --port 8080; rc=$?; a_rc $rc 0
grep -q '^FRONTEND_PORT=8080$' "${SBX}/opt/energy-tds/.env" || fail ".env FRONTEND_PORT 未改为 8080"
a_log 'curl .*http://localhost:8080/health'; a_grep 'http://192.168.1.100:8080'
finish

begin "C11 镜像 SHA256 不符 → 提示包损坏，不 docker load"
sandbox_env c11; PKG_BAD="${WORK}/pkg-bad"; rm -rf "${PKG_BAD}"; cp -r "${PKG_X86}" "${PKG_BAD}"; echo corrupt >> "${PKG_BAD}/images/redis.tar.gz"
run_in normal bash "${PKG_BAD}/install.sh"; rc=$?; a_rc $rc 1; a_grep '镜像文件校验失败'; a_nolog 'docker load'
finish

begin "C12 docker load 失败 → 提示磁盘空间"
sandbox_env c12; export FAKE_LOAD_FAIL=1
run_in normal bash "${INST}"; rc=$?; a_rc $rc 1; a_grep '镜像导入失败'; a_grep 'df -h /var/lib/docker'
finish

begin "C13 x86_64 成功安装：随机密钥 / 文件布局 / 健康轮询 / systemd / 横幅六账号"
sandbox_env c13
run_in normal bash "${INST}"; rc=$?; a_rc $rc 0
for i in 1 2 3 4 5 6 7 8 9; do a_grep "第 $i 步 / 9"; done
E="${SBX}/opt/energy-tds/.env"; a_file "$E"
[[ "$(stat -c %a "$E")" == 600 ]] || fail ".env 权限应为 600"
for k in MYSQL_ROOT_PASSWORD MYSQL_PASSWORD; do grep -qE "^${k}=[0-9a-f]{32}$" "$E" || fail "$k 不是 32 位随机 hex"; done
grep -qE '^JWT_SECRET=[0-9a-f]{64}$' "$E" || fail "JWT_SECRET 不是 64 位随机 hex"
grep -q 'energy-tds-demo-secret-2026' "$E" && fail "JWT_SECRET 仍是模板默认值"
# KEY_CUSTODY_SECRET（甲 backend/core/config.py 读取）：随机 64 位 hex，且与 JWT_SECRET 不同
grep -qE '^KEY_CUSTODY_SECRET=[0-9a-f]{64}$' "$E" || fail "KEY_CUSTODY_SECRET 不是 64 位随机 hex"
grep -q 'energy-tds-key-custody-2026' "$E" && fail "KEY_CUSTODY_SECRET 仍是模板默认值"
[[ "$(sed -n 's/^JWT_SECRET=//p' "$E")" != "$(sed -n 's/^KEY_CUSTODY_SECRET=//p' "$E")" ]] || fail "KEY_CUSTODY_SECRET 与 JWT_SECRET 相同"
for v in REDIS_DB JWT_ALGORITHM ALGO_TIMEOUT DEBUG DB_WAIT_TIMEOUT; do grep -q "^${v}=" "$E" || fail ".env 缺 backend 非契约变量 $v"; done
grep -qE '^[A-Z_]+=.*#' "$E" && fail ".env 残留行尾注释"
# 契约全部变量都在 .env 中
for v in $(sed -n '/^## 第四部分/,/^## 第五部分/p' "${ROOT_DIR}/contract/API-CONTRACT.md" | grep -oE '^[A-Z_]+=' | tr -d =); do grep -q "^${v}=" "$E" || fail ".env 缺契约变量 $v"; done
for f in docker-compose.yml config/mysql.cnf sql/01_schema.sql sql/02_seed.sql uninstall.sh VERSION 部署说明.md; do a_file "${SBX}/opt/energy-tds/$f"; done
# docker load 五个镜像，SHA 校验在 load 之前
[[ "$(grep -c '^docker load -q -i' "${FAKE_LOG}")" -eq 5 ]] || fail "应 docker load 5 个镜像"
a_log '^docker compose up -d'; a_log 'curl .*http://localhost:80/health'
a_grep '平台健康检查通过'
# systemd
a_file "${SBX}/systemd/energy-tds.service"; a_log '^systemctl enable energy-tds.service'
grep -q "WorkingDirectory=${SBX}/opt/energy-tds" "${SBX}/systemd/energy-tds.service" || fail "energy-tds.service WorkingDirectory 未指向安装目录"
a_nofile "${SBX}/systemd/energy-tds-kiosk.service"; a_nolog 'dphys-swapfile'
# 横幅
a_grep '安装完成'; a_grep '访问地址   http://192.168.1.100$'
for acct in 'admin / admin123' 'grid / grid123' 'vpp / vpp123' 'subject / subject123' 'regulator / reg123' 'edge / edge123'; do a_grep "$acct"; done
a_grep 'systemctl start\|stop\|status energy-tds'; a_grep 'uninstall.sh'
SECRETS_1="$(grep -E '^(MYSQL_ROOT_PASSWORD|MYSQL_PASSWORD|JWT_SECRET|KEY_CUSTODY_SECRET)=' "$E")"
finish

begin "C14 第二台机器安装 → 四个密钥与 C13 全部不同"
sandbox_env c14
run_in normal bash "${INST}"; rc=$?; a_rc $rc 0
SECRETS_2="$(grep -E '^(MYSQL_ROOT_PASSWORD|MYSQL_PASSWORD|JWT_SECRET|KEY_CUSTODY_SECRET)=' "${SBX}/opt/energy-tds/.env")"
for k in MYSQL_ROOT_PASSWORD MYSQL_PASSWORD JWT_SECRET KEY_CUSTODY_SECRET; do
  [[ "$(grep "^$k=" <<<"${SECRETS_1}")" != "$(grep "^$k=" <<<"${SECRETS_2}")" ]] || fail "$k 两次安装相同"
done
finish

begin "C15 重装（旧实例运行中）→ 先 compose down，保留旧 .env"
sandbox_env c15
run_in normal bash "${INST}"; rc=$?; a_rc $rc 0
OLD="$(cat "${SBX}/opt/energy-tds/.env")"
: > "${FAKE_LOG}"; export FAKE_OLD_RUNNING=1
run_in normal bash "${INST}"; rc=$?; a_rc $rc 0
a_grep '检测到已安装的旧实例正在运行，先停止'; a_log "^docker compose -f ${SBX}/opt/energy-tds/docker-compose.yml down"
a_grep '已存在 .env（上次安装生成），保留原密钥'
[[ "$(cat "${SBX}/opt/energy-tds/.env")" == "${OLD}" ]] || fail "重装后 .env 被改写"
finish

begin "C16 健康检查 180s 超时 → 打印 compose ps + logs --tail=50 并退出"
sandbox_env c16; export FAKE_HEALTH_OK_AT=0
run_in normal bash "${INST}"; rc=$?; a_rc $rc 1
a_grep 'docker compose logs --tail=50'; a_log '^docker compose logs --tail=50'; a_log '^docker compose ps'
[[ "$(grep -c '^curl .*[0-9a-z]/health$' "${FAKE_LOG}")" -eq 60 ]] || fail "180s/3s 应轮询 60 次（实际 $(grep -c '^curl .*[0-9a-z]/health$' "${FAKE_LOG}")）"
a_grep '等待 180s 仍未通过健康检查'; a_nogrep '安装完成'; a_nofile "${SBX}/systemd/energy-tds.service"
# 第 5 次才健康
sandbox_env c16b; export FAKE_HEALTH_OK_AT=5
run_in normal bash "${INST}"; rc=$?; a_rc $rc 0; [[ "$(grep -c '^curl .*[0-9a-z]/health$' "${FAKE_LOG}")" -eq 5 ]] || fail "应在第 5 次轮询通过"
finish

begin "C17 docker compose up 失败 → 退出并给出 logs 命令"
sandbox_env c17; export FAKE_COMPOSE_UP_FAIL=1
run_in normal bash "${INST}"; rc=$?; a_rc $rc 1; a_grep '容器启动失败'; a_grep 'logs --tail=50'
finish

begin "C18 arm64 成功安装：swap 调 2048 + kiosk 单元（用户 pi）"
sandbox_env c18; export FAKE_ARCH=aarch64; printf 'CONF_SWAPSIZE=100\n' > "${SBX}/dphys-swapfile"
run_in arm bash "${INST_ARM}"; rc=$?; a_rc $rc 0
grep -q '^CONF_SWAPSIZE=2048$' "${SBX}/dphys-swapfile" || fail "dphys-swapfile 未改为 2048"
a_log '^systemctl restart dphys-swapfile'; a_grep 'swap 调整为 2048MB（原 100MB）'
a_file "${SBX}/systemd/energy-tds-kiosk.service"; a_file "${SBX}/opt/energy-tds/kiosk.sh"
grep -q '^User=pi$' "${SBX}/systemd/energy-tds-kiosk.service" || fail "kiosk User 应为 pi"
grep -q "ExecStart=/bin/bash ${SBX}/opt/energy-tds/kiosk.sh" "${SBX}/systemd/energy-tds-kiosk.service" || fail "kiosk ExecStart 未指向安装目录 kiosk.sh"
grep -q '__KIOSK_USER__' "${SBX}/systemd/energy-tds-kiosk.service" && fail "kiosk 占位符未替换"
a_log '^systemctl enable energy-tds-kiosk.service'; a_grep 'energy-tds-kiosk.service 已启用'
# swap 已够：不改
sandbox_env c18b; export FAKE_ARCH=aarch64; printf 'CONF_SWAPSIZE=2048\n' > "${SBX}/dphys-swapfile"
run_in arm bash "${INST_ARM}"; rc=$?; a_rc $rc 0; a_grep 'swap 已是 2048MB'; a_nolog 'restart dphys-swapfile'
# 无 dphys-swapfile（非 RPi OS）：警告不失败
sandbox_env c18c; export FAKE_ARCH=aarch64
run_in arm bash "${INST_ARM}"; rc=$?; a_rc $rc 0; a_grep '跳过 swap 调整'
finish

begin "C19 arm64 --no-kiosk / 无 chromium / 无桌面用户 → 跳过 kiosk"
sandbox_env c19; export FAKE_ARCH=aarch64
run_in arm bash "${INST_ARM}" --no-kiosk; rc=$?; a_rc $rc 0; a_nofile "${SBX}/systemd/energy-tds-kiosk.service"
sandbox_env c19b; export FAKE_ARCH=aarch64
run_in normal bash "${INST_ARM}"; rc=$?; a_rc $rc 0; a_grep '未安装 chromium，跳过'; a_nofile "${SBX}/systemd/energy-tds-kiosk.service"
sandbox_env c19c; export FAKE_ARCH=aarch64 FAKE_USERS="nobody" SUDO_USER=root
run_in arm bash "${INST_ARM}"; rc=$?; a_rc $rc 0; a_grep '找不到桌面用户 pi'
finish

begin "C20 --no-start 不启动容器，仍装 systemd"
sandbox_env c20
run_in normal bash "${INST}" --no-start; rc=$?; a_rc $rc 0; a_nolog '^docker compose up'; a_file "${SBX}/systemd/energy-tds.service"
finish

begin "C21 演练包（--skip-backend）健康 URL 退为 /healthz 并警告 sql/ 为空"
sandbox_env c21
run_in normal bash "${PKG_SKIP}/install.sh"; rc=$?; a_rc $rc 0; a_grep '演练包'; a_log 'curl .*http://localhost:80/healthz'; a_grep 'sql/ 为空'
finish

begin "C22 install.sh --help / 未知参数 / 非包目录"
sandbox_env c22
run_in normal bash "${INST}" --help; rc=$?; a_rc $rc 0; a_grep '用法'
run_in normal bash "${INST}" --bogus; rc=$?; a_rc $rc 1; a_grep '未知参数'
cp "${INST}" "${SBX}/install.sh"; run_in normal bash "${SBX}/install.sh"; rc=$?; a_rc $rc 1; a_grep '找不到 .*VERSION'
finish

# ============================================================
# D. uninstall.sh
# ============================================================
begin "D1 uninstall 默认保留数据卷与 .env"
sandbox_env d1
run_in normal bash "${INST}"; rc=$?; a_rc $rc 0
: > "${FAKE_LOG}"
run_in normal bash "${SBX}/opt/energy-tds/uninstall.sh"; rc=$?; a_rc $rc 0
a_log '^docker compose down --remove-orphans$'; a_nolog 'down -v'
a_log '^systemctl disable --now energy-tds.service'; a_nofile "${SBX}/systemd/energy-tds.service"
a_file "${SBX}/opt/energy-tds/.env"; a_nofile "${SBX}/opt/energy-tds/docker-compose.yml"; a_nofile "${SBX}/opt/energy-tds/sql"
a_log '^docker rmi -f energy-tds/frontend:1.0'; a_log '^docker rmi -f mysql:8.0'; a_log '^docker network rm energy-tds-net'
a_grep '数据卷 energy-tds-mysql-data / energy-tds-redis-data 已保留'; a_grep '\-\-purge'
finish

begin "D2 uninstall --purge -y 删数据卷与整个目录"
sandbox_env d2; export FAKE_ARCH=aarch64
run_in arm bash "${INST_ARM}"; rc=$?; a_rc $rc 0
: > "${FAKE_LOG}"
run_in normal bash "${SBX}/opt/energy-tds/uninstall.sh" --purge -y; rc=$?; a_rc $rc 0
a_log '^docker compose down -v --remove-orphans'; a_nofile "${SBX}/opt/energy-tds"
a_nofile "${SBX}/systemd/energy-tds-kiosk.service"; a_log '^systemctl disable --now energy-tds-kiosk.service'
a_grep '数据已彻底清除'
finish

begin "D3 uninstall --purge 无 -y 输入非 yes → 取消；非 root 拒绝"
sandbox_env d3
run_in normal bash "${INST}"; rc=$?; a_rc $rc 0
echo no | PATH="${FAKEBIN}:${SBX}/bin:${REALBIN}" bash "${SBX}/opt/energy-tds/uninstall.sh" --purge >> "${CUR_OUT}" 2>&1; rc=$?; a_rc $rc 0; a_grep '已取消'; a_file "${SBX}/opt/energy-tds/.env"
export FAKE_UID=1000; run_in normal bash "${SBX}/opt/energy-tds/uninstall.sh"; rc=$?; a_rc $rc 1; a_grep '需要 root'
finish

# ============================================================
# 汇总
# ============================================================
echo
echo "══════════════════════ 结果 ══════════════════════"
for r in "${RESULTS[@]}"; do echo "$r"; done
echo "──────────────────────────────────────────────────"
echo "场景 $((PASS_N+FAIL_N))：PASS ${PASS_N}，FAIL ${FAIL_N}   （输出目录 ${WORK}/out）"
[[ ${FAIL_N} -eq 0 ]]
