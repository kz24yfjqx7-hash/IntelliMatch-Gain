#!/usr/bin/env bash
# ============================================================
# 能源可信数据空间平台 · 离线安装包构建脚本（开发机上运行，需联网）
#
# 用法：
#   sudo ./packaging/build.sh --arch all            # 两个架构都出
#   sudo ./packaging/build.sh --arch amd64          # 只出 x86_64
#   sudo ./packaging/build.sh --arch arm64          # 只出树莓派
# 可选：
#   --skip-backend     乙方独立试打包：缺 backend/ 时不报错（产物不含 backend 镜像与 sql，仅供流程演练）
#   --skip-dqn-train   跳过第 3 步 DQN 训练（models/dqn.npz 已存在时自动跳过）
#   --dry-run          只打印将执行的步骤 / 命令，不真正执行
#   --version X        版本号，默认 1.0
#   --db-image IMG     强制数据库镜像（默认 mysql:8.0；arm64 验证失败自动切 mariadb:11）
#
# 十步流程严格对应《安装包规范》§2.3。产物落在 dist/，中间产物在 build/。
# 一次性环境准备（只做一次）：
#   docker run --privileged --rm tonistiigi/binfmt --install all
#   docker buildx create --name energy-builder --use --bootstrap
# ============================================================
set -euo pipefail

# ---------- 常量 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${ENERGY_TDS_BUILD_DIR:-${ROOT_DIR}/build}"     # 可覆盖，仅供 qa/deploy-sandbox
DIST_DIR="${ENERGY_TDS_DIST_DIR:-${ROOT_DIR}/dist}"
DL_DIR="${BUILD_DIR}/downloads"

VERSION="1.0"
ARCH_ARG="all"
SKIP_BACKEND=0
SKIP_DQN=0
DRY_RUN=0
DB_IMAGE="mysql:8.0"
DB_IMAGE_FORCED=0
REDIS_IMAGE="redis:7-alpine"
BASE_IMAGES=("mysql:8.0" "redis:7-alpine" "python:3.11-slim" "nginx:alpine" "node:20-alpine")
DOCKER_STATIC_VERSION="${DOCKER_STATIC_VERSION:-27.5.1}"     # download.docker.com 静态二进制版本
COMPOSE_VERSION="${COMPOSE_VERSION:-2.32.4}"                 # compose 插件版本
MIN_DISK_GB=25
PYTHON_BIN=""

# ---------- 输出工具 ----------
C_RED='\033[31m'; C_GRN='\033[32m'; C_YLW='\033[33m'; C_CYN='\033[36m'; C_RST='\033[0m'
info()  { echo -e "${C_CYN}[INFO]${C_RST} $*"; }
ok()    { echo -e "${C_GRN}[ OK ]${C_RST} $*"; }
warn()  { echo -e "${C_YLW}[WARN]${C_RST} $*"; }
die()   { echo -e "${C_RED}[FAIL]${C_RST} $*" >&2; exit 1; }
step()  { echo; echo -e "${C_CYN}━━━━━━━━━━ 第 $1 步 / 10：$2 ━━━━━━━━━━${C_RST}"; }

# 执行命令；dry-run 时只打印
run() {
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "  [dry-run] $*"
  else
    "$@"
  fi
}
# 执行含管道/重定向的 shell 片段
run_sh() {
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "  [dry-run] bash -c '$*'"
  else
    bash -o pipefail -c "$*"
  fi
}

usage() { sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

# ---------- 参数 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch)           ARCH_ARG="${2:-}"; shift 2 ;;
    --arch=*)         ARCH_ARG="${1#*=}"; shift ;;
    --skip-backend)   SKIP_BACKEND=1; shift ;;
    --skip-dqn-train) SKIP_DQN=1; shift ;;
    --dry-run)        DRY_RUN=1; shift ;;
    --version)        VERSION="${2:-}"; shift 2 ;;
    --db-image)       DB_IMAGE="${2:-}"; DB_IMAGE_FORCED=1; shift 2 ;;
    -h|--help)        usage ;;
    *) die "未知参数：$1（用 --help 查看用法）" ;;
  esac
done

case "${ARCH_ARG}" in
  all)   ARCHES=(amd64 arm64) ;;
  amd64) ARCHES=(amd64) ;;
  arm64) ARCHES=(arm64) ;;
  *) die "--arch 只能是 amd64 | arm64 | all，收到：${ARCH_ARG}" ;;
esac

# 架构名映射：docker 平台名 → 包名后缀 / docker 静态二进制目录
pkg_suffix()   { case "$1" in amd64) echo x86_64 ;; arm64) echo arm64 ;; esac; }
docker_arch()  { case "$1" in amd64) echo x86_64 ;; arm64) echo aarch64 ;; esac; }

SERVICES=(backend algo-service frontend)
[[ ${SKIP_BACKEND} -eq 1 ]] && SERVICES=(algo-service frontend)

[[ ${DRY_RUN} -eq 1 ]] && warn "dry-run 模式：只打印步骤，不执行任何构建 / 下载"
info "版本 ${VERSION}，架构：${ARCHES[*]}，自建镜像：${SERVICES[*]}"

# ============================================================
# 第 1 步：前置检查
# ============================================================
step 1 "前置检查（docker / buildx / 网络 / 磁盘 / 三个源码目录）"

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "docker 可用：$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"
    if docker buildx version >/dev/null 2>&1; then
      ok "buildx 可用：$(docker buildx version | head -1)"
      if [[ " ${ARCHES[*]} " == *" arm64 "* ]]; then
        if docker buildx inspect --bootstrap 2>/dev/null | grep -q 'linux/arm64'; then
          ok "当前 builder 支持 linux/arm64"
        else
          warn "当前 builder 不支持 linux/arm64，请先执行："
          warn "  docker run --privileged --rm tonistiigi/binfmt --install all"
          warn "  docker buildx create --name energy-builder --use --bootstrap"
          [[ ${DRY_RUN} -eq 1 ]] || die "缺少 arm64 构建能力"
        fi
      fi
    else
      [[ ${DRY_RUN} -eq 1 ]] && warn "buildx 不可用" || die "需要 docker buildx（Docker 20.10+ 自带）"
    fi
  else
    [[ ${DRY_RUN} -eq 1 ]] && warn "无法连接 docker 守护进程（当前用户可能不在 docker 组，或需 sudo）" \
                            || die "无法连接 docker 守护进程：请用 sudo 运行，或把当前用户加入 docker 组"
  fi
else
  [[ ${DRY_RUN} -eq 1 ]] && warn "未找到 docker 命令" || die "未找到 docker，请先安装 Docker 20.10+"
fi

if curl -fsI --max-time 8 https://download.docker.com/ >/dev/null 2>&1; then
  ok "网络可达 download.docker.com"
else
  [[ ${DRY_RUN} -eq 1 ]] && warn "网络不可达 download.docker.com" || die "需要联网下载 Docker 静态二进制与基础镜像"
fi

avail_gb=$(df -BG --output=avail "${ROOT_DIR}" | tail -1 | tr -dc '0-9')
if [[ ${avail_gb} -ge ${MIN_DISK_GB} ]]; then
  ok "磁盘剩余 ${avail_gb}GB（要求 ≥ ${MIN_DISK_GB}GB）"
else
  [[ ${DRY_RUN} -eq 1 ]] && warn "磁盘剩余 ${avail_gb}GB < ${MIN_DISK_GB}GB" || die "磁盘剩余 ${avail_gb}GB，不足 ${MIN_DISK_GB}GB"
fi

for d in algo-service frontend; do
  [[ -d "${ROOT_DIR}/${d}" ]] && ok "目录存在：${d}/" || die "缺少目录 ${ROOT_DIR}/${d}"
done
if [[ -d "${ROOT_DIR}/backend" ]]; then
  ok "目录存在：backend/"
  [[ -f "${ROOT_DIR}/backend/Dockerfile" ]] || die "backend/Dockerfile 不存在，请确认甲方代码已合并"
  [[ -d "${ROOT_DIR}/backend/sql" ]] || die "backend/sql/ 不存在（需要 01_schema.sql / 02_seed.sql）"
else
  if [[ ${SKIP_BACKEND} -eq 1 ]]; then
    warn "backend/ 不存在，已指定 --skip-backend：产物不含 backend 镜像与 sql/，仅供流程演练，不可交付"
  else
    die "缺少 backend/ 目录（甲方代码）。规范要求 backend/ algo-service/ frontend/ 三个目录齐全后再打包；
       乙方独立演练流程请加 --skip-backend"
  fi
fi
for f in algo-service/Dockerfile frontend/Dockerfile frontend/nginx.conf docker-compose.yml .env.example \
         packaging/install.sh packaging/uninstall.sh packaging/部署说明.md \
         packaging/assets/mysql-amd64.cnf packaging/assets/mysql-arm64.cnf \
         packaging/assets/energy-tds.service packaging/assets/energy-tds-kiosk.service packaging/assets/energy-tds-kiosk.sh \
         packaging/assets/docker.service; do
  if [[ ! -f "${ROOT_DIR}/${f}" ]]; then
    [[ ${DRY_RUN} -eq 1 ]] && warn "缺少文件 ${f}（dry-run 继续）" || die "缺少文件 ${f}"
  fi
done
ok "打包所需文件检查完毕"

# ============================================================
# 第 2 步：验证基础镜像 arm64 可用性
# ============================================================
step 2 "验证基础镜像 arm64 可用性"

# 返回 0 = 有 arm64 变体。优先 docker buildx imagetools，不可用时退回 Docker Hub registry API
has_arm64() {
  local img="$1" repo tag token
  if docker buildx imagetools inspect "${img}" >/dev/null 2>&1; then
    docker buildx imagetools inspect "${img}" 2>/dev/null | grep -q 'linux/arm64'
    return $?
  fi
  repo="${img%%:*}"; tag="${img##*:}"
  [[ "${repo}" == */* ]] || repo="library/${repo}"
  token=$(curl -fs --max-time 15 "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${repo}:pull" \
          | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
  [[ -n "${token}" ]] || return 2
  curl -fs --max-time 15 -H "Authorization: Bearer ${token}" \
       -H 'Accept: application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json' \
       "https://registry-1.docker.io/v2/${repo}/manifests/${tag}" | grep -q '"architecture":"arm64"'
}

if [[ " ${ARCHES[*]} " == *" arm64 "* ]]; then
  mysql_arm64_ok=1
  for img in "${BASE_IMAGES[@]}"; do
    if has_arm64 "${img}"; then
      ok "${img} : 有 linux/arm64"
    else
      warn "${img} : 无 linux/arm64"
      [[ "${img}" == "mysql:8.0" ]] && mysql_arm64_ok=0
    fi
  done
  if [[ ${mysql_arm64_ok} -eq 0 && ${DB_IMAGE_FORCED} -eq 0 ]]; then
    DB_IMAGE="mariadb:11"
    warn "mysql:8.0 无 arm64 变体 → 两个架构整体切换为 ${DB_IMAGE}（DDL 兼容，SQL 无需改动）"
    has_arm64 "${DB_IMAGE}" || die "${DB_IMAGE} 也没有 arm64 变体，无法继续"
  fi
else
  info "仅构建 amd64，跳过 arm64 验证"
fi
ok "数据库镜像：${DB_IMAGE}"

# ============================================================
# 第 3 步：训练 DQN（只在 x86 原生跑一次，两个架构共用）
# ============================================================
step 3 "训练 DQN（models/dqn.npz 不存在时才跑）"

pick_python() {
  if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then echo "${ROOT_DIR}/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then command -v python3
  else echo ""; fi
}
PYTHON_BIN="$(pick_python)"

DQN_FILE="${ROOT_DIR}/algo-service/models/dqn.npz"
if [[ -f "${DQN_FILE}" ]]; then
  ok "已存在 ${DQN_FILE#"${ROOT_DIR}"/}，跳过训练"
elif [[ ${SKIP_DQN} -eq 1 ]]; then
  warn "--skip-dqn-train：跳过训练；Dockerfile 内的兜底逻辑会在镜像构建时生成 checkpoint"
else
  [[ -f "${ROOT_DIR}/algo-service/scripts/train_dqn.py" ]] || die "缺少 algo-service/scripts/train_dqn.py"
  if [[ -n "${PYTHON_BIN}" ]] && "${PYTHON_BIN}" -c 'import numpy' 2>/dev/null; then
    info "使用 ${PYTHON_BIN} 训练"
    run_sh "cd '${ROOT_DIR}/algo-service' && '${PYTHON_BIN}' scripts/train_dqn.py"
  else
    info "本机无带 numpy 的 Python，用 python:3.11-slim 容器训练"
    run_sh "docker run --rm -v '${ROOT_DIR}/algo-service:/app' -w /app python:3.11-slim \
            sh -c 'pip install -q -r requirements.txt && python scripts/train_dqn.py'"
  fi
  [[ ${DRY_RUN} -eq 1 || -f "${DQN_FILE}" ]] || die "训练结束但未生成 ${DQN_FILE}"
fi
# 节点数据集同理（FedAvg 用）
DS_FILE="${ROOT_DIR}/algo-service/data/node_datasets.npz"
if [[ ! -f "${DS_FILE}" && -f "${ROOT_DIR}/algo-service/scripts/gen_datasets.py" && -n "${PYTHON_BIN}" ]]; then
  info "生成 FedAvg 节点数据集 data/node_datasets.npz"
  run_sh "cd '${ROOT_DIR}/algo-service' && '${PYTHON_BIN}' scripts/gen_datasets.py" || warn "数据集生成失败，交由 Dockerfile 兜底"
fi

# ============================================================
# 第 4 步：预热 DeepSeek 缓存
# ============================================================
step 4 "预热 DeepSeek 缓存（有 DEEPSEEK_API_KEY 时）"

# 从环境或根目录 .env 读取 key
if [[ -z "${DEEPSEEK_API_KEY:-}" && -f "${ROOT_DIR}/.env" ]]; then
  DEEPSEEK_API_KEY="$(grep -E '^DEEPSEEK_API_KEY=' "${ROOT_DIR}/.env" | cut -d= -f2- | tr -d '[:space:]' || true)"
fi
if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
  if [[ -f "${ROOT_DIR}/algo-service/scripts/warm_cache.py" && -n "${PYTHON_BIN}" ]]; then
    info "检测到 DEEPSEEK_API_KEY，预热常见场景的真实回答到 cache/"
    run_sh "cd '${ROOT_DIR}/algo-service' && DEEPSEEK_API_KEY='${DEEPSEEK_API_KEY}' '${PYTHON_BIN}' scripts/warm_cache.py" \
      || warn "预热失败（网络 / 配额问题），镜像将使用已有缓存 + 规则模板"
  else
    warn "缺少 scripts/warm_cache.py 或 Python，跳过预热"
  fi
else
  info "未设置 DEEPSEEK_API_KEY，跳过预热；运行时走 cache → rule 降级"
fi

# ============================================================
# 第 5 步：构建三个自建镜像（每个架构一遍，直接输出 tar）
# ============================================================
step 5 "构建自建镜像（buildx --output type=docker,dest=...）"

mkdir -p "${BUILD_DIR}/images" "${DL_DIR}" "${DIST_DIR}"
for arch in "${ARCHES[@]}"; do
  for svc in "${SERVICES[@]}"; do
    out="${BUILD_DIR}/images/${svc}-${arch}.tar"
    info "构建 energy-tds/${svc}:${VERSION} @ linux/${arch}"
    extra_args=()
    if [[ "${svc}" == "frontend" ]]; then
      extra_args+=(--build-arg VITE_USE_MOCK=false --build-arg VITE_API_BASE=/api/v1 --build-arg VITE_WS_BASE=/ws)
    fi
    run docker buildx build \
      --platform "linux/${arch}" \
      --output "type=docker,dest=${out}" \
      -t "energy-tds/${svc}:${VERSION}" \
      ${extra_args[@]+"${extra_args[@]}"} \
      "${ROOT_DIR}/${svc}"
    run_sh "gzip -f '${out}'"
    ok "→ images/${svc}-${arch}.tar.gz"
  done
done

# ============================================================
# 第 6 / 7 步：拉取基础镜像（串行 pull → save → rmi，避免架构互相覆盖）
# ============================================================
step 6 "拉取基础镜像（${DB_IMAGE} / ${REDIS_IMAGE}，按架构串行）"
step 7 "导出基础镜像 tar 并 gzip"

for arch in "${ARCHES[@]}"; do
  for pair in "mysql=${DB_IMAGE}" "redis=${REDIS_IMAGE}"; do
    name="${pair%%=*}"; img="${pair#*=}"
    out="${BUILD_DIR}/images/${name}-${arch}.tar.gz"
    info "[${arch}] pull ${img}"
    run docker pull --platform "linux/${arch}" "${img}"
    run_sh "docker save '${img}' | gzip > '${out}'"
    run docker rmi -f "${img}"
    ok "→ images/${name}-${arch}.tar.gz"
  done
done

# ============================================================
# 第 8 步：下载 Docker 静态二进制 + compose 插件
# ============================================================
step 8 "下载 Docker 静态二进制（download.docker.com）与 docker-compose"

for arch in "${ARCHES[@]}"; do
  darch="$(docker_arch "${arch}")"
  tgz="${DL_DIR}/docker-${DOCKER_STATIC_VERSION}-${darch}.tgz"
  comp="${DL_DIR}/docker-compose-${COMPOSE_VERSION}-linux-${darch}"
  if [[ -f "${tgz}" ]]; then ok "已缓存 $(basename "${tgz}")"
  else
    run curl -fL --retry 3 -o "${tgz}" \
      "https://download.docker.com/linux/static/stable/${darch}/docker-${DOCKER_STATIC_VERSION}.tgz"
  fi
  if [[ -f "${comp}" ]]; then ok "已缓存 $(basename "${comp}")"
  else
    run curl -fL --retry 3 -o "${comp}" \
      "https://github.com/docker/compose/releases/download/v${COMPOSE_VERSION}/docker-compose-linux-${darch}"
    run chmod +x "${comp}"
  fi
done

# ============================================================
# 第 9 步：组装目录
# ============================================================
step 9 "组装安装包目录"

# 生成安装包用的 compose：镜像已 docker load，不再需要 build；路径改为包内相对路径
gen_pkg_compose() {   # $1 = 输出文件
  local src="${ROOT_DIR}/docker-compose.yml" dst="$1"
  # 去掉每个服务的 build: 块（4 空格缩进的 build: 直到下一个 4 空格缩进的键）
  awk '
    /^    build:/ { skip=1; next }
    skip && /^    [^ ]/ { skip=0 }
    skip && /^[^ ]/     { skip=0 }
    !skip { print }
  ' "${src}" \
  | sed -e "s#\./backend/sql:#./sql:#" \
        -e "s#\./deploy/mysql/mysql.cnf:#./config/mysql.cnf:#" \
        -e "s#image: mysql:8.0#image: ${DB_IMAGE}#" \
        -e "s#energy-tds/\([a-z-]*\):1.0#energy-tds/\1:${VERSION}#g" \
  > "${dst}"
  # mariadb:11 镜像自带 healthcheck.sh（mysqladmin 别名在 11.x 已弃用），整体切换时连同健康检查一起换
  if [[ "${DB_IMAGE}" == mariadb:* ]]; then
    sed -i 's#"mysqladmin ping -h 127.0.0.1 -u root -p$${MYSQL_ROOT_PASSWORD} --silent"#"healthcheck.sh --connect --innodb_initialized"#' "${dst}"
  fi
  grep -q '^    build:' "${dst}" && die "包内 compose 仍含 build: 块，gen_pkg_compose 失败"
  return 0
}

for arch in "${ARCHES[@]}"; do
  suffix="$(pkg_suffix "${arch}")"; darch="$(docker_arch "${arch}")"
  pkg_name="energy-tds-v${VERSION}-linux-${suffix}"
  pkg="${BUILD_DIR}/${pkg_name}"
  info "[${arch}] 组装 ${pkg_name}/"
  run rm -rf "${pkg}"
  run mkdir -p "${pkg}/images" "${pkg}/docker" "${pkg}/config" "${pkg}/sql" "${pkg}/systemd"

  # 脚本与说明
  run install -m 0755 "${SCRIPT_DIR}/install.sh"   "${pkg}/install.sh"
  run install -m 0755 "${SCRIPT_DIR}/uninstall.sh" "${pkg}/uninstall.sh"
  run cp "${SCRIPT_DIR}/部署说明.md" "${pkg}/部署说明.md"

  # 镜像
  for svc in "${SERVICES[@]}"; do
    run cp "${BUILD_DIR}/images/${svc}-${arch}.tar.gz" "${pkg}/images/${svc}.tar.gz"
  done
  run cp "${BUILD_DIR}/images/mysql-${arch}.tar.gz" "${pkg}/images/mysql.tar.gz"
  run cp "${BUILD_DIR}/images/redis-${arch}.tar.gz" "${pkg}/images/redis.tar.gz"

  # Docker 离线二进制
  run cp "${DL_DIR}/docker-${DOCKER_STATIC_VERSION}-${darch}.tgz" "${pkg}/docker/docker-${arch}.tgz"
  run cp "${DL_DIR}/docker-compose-${COMPOSE_VERSION}-linux-${darch}" "${pkg}/docker/docker-compose-${arch}"
  run cp "${SCRIPT_DIR}/assets/docker.service" "${pkg}/docker/docker.service"

  # 配置
  if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] gen_pkg_compose ${pkg}/config/docker-compose.yml"
  else gen_pkg_compose "${pkg}/config/docker-compose.yml"; fi
  run cp "${ROOT_DIR}/.env.example" "${pkg}/config/.env.example"
  run cp "${SCRIPT_DIR}/assets/mysql-${arch}.cnf" "${pkg}/config/mysql.cnf"

  # SQL（甲方建表脚本）
  if [[ -d "${ROOT_DIR}/backend/sql" ]]; then
    run_sh "cp '${ROOT_DIR}'/backend/sql/*.sql '${pkg}/sql/'"
  else
    warn "无 backend/sql，sql/ 目录为空（--skip-backend 演练包）"
  fi

  # systemd
  run cp "${SCRIPT_DIR}/assets/energy-tds.service" "${pkg}/systemd/"
  if [[ "${arch}" == "arm64" ]]; then
    run cp "${SCRIPT_DIR}/assets/energy-tds-kiosk.service" "${pkg}/systemd/"
    run install -m 0755 "${SCRIPT_DIR}/assets/energy-tds-kiosk.sh" "${pkg}/systemd/energy-tds-kiosk.sh"
  fi

  # VERSION + 校验和
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "  [dry-run] 写入 ${pkg}/VERSION 与 images/SHA256SUMS"
  else
    (cd "${pkg}/images" && sha256sum ./*.tar.gz > SHA256SUMS)
    {
      echo "NAME=energy-tds"
      echo "VERSION=${VERSION}"
      echo "ARCH=${arch}"
      echo "PKG_SUFFIX=${suffix}"
      echo "BUILD_TIME=$(date '+%Y-%m-%d %H:%M:%S %z')"
      echo "BUILD_HOST=$(hostname)"
      echo "DB_IMAGE=${DB_IMAGE}"
      echo "REDIS_IMAGE=${REDIS_IMAGE}"
      echo "DOCKER_STATIC_VERSION=${DOCKER_STATIC_VERSION}"
      echo "COMPOSE_VERSION=${COMPOSE_VERSION}"
      echo "SKIP_BACKEND=${SKIP_BACKEND}"
      echo "# 镜像文件 SHA256"
      sed 's#^\([0-9a-f]*\)  \./\(.*\)$#IMAGE_SHA256 \2 \1#' "${pkg}/images/SHA256SUMS"
    } > "${pkg}/VERSION"
  fi
  ok "[${arch}] 组装完成"
done

# ============================================================
# 第 10 步：打 tar.gz + SHA256
# ============================================================
step 10 "打包 tar.gz 并生成 SHA256"

for arch in "${ARCHES[@]}"; do
  suffix="$(pkg_suffix "${arch}")"
  pkg_name="energy-tds-v${VERSION}-linux-${suffix}"
  run_sh "tar -C '${BUILD_DIR}' -czf '${DIST_DIR}/${pkg_name}.tar.gz' '${pkg_name}'"
  run_sh "cd '${DIST_DIR}' && sha256sum '${pkg_name}.tar.gz' > '${pkg_name}.tar.gz.sha256'"
  if [[ ${DRY_RUN} -eq 0 ]]; then
    ok "dist/${pkg_name}.tar.gz  ($(du -h "${DIST_DIR}/${pkg_name}.tar.gz" | cut -f1))"
  else
    ok "dist/${pkg_name}.tar.gz"
  fi
done

echo
ok "全部完成。产物目录：${DIST_DIR}"
[[ ${SKIP_BACKEND} -eq 1 ]] && warn "注意：本次为 --skip-backend 演练包，不含 backend 镜像与 sql/，不可用于交付"
info "可执行 docker buildx prune 回收中间层磁盘空间"
