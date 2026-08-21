#!/usr/bin/env bash
# ============================================================
# 能源可信数据空间平台 · 离线一键安装脚本
#   用法：sudo ./install.sh [--no-kiosk] [--no-start] [--port 80]
#   九步流程严格对应《安装包规范》§四；任何一步失败立即退出并打印可操作的提示。
#   安装目录：/opt/energy-tds
# ============================================================
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/energy-tds"
HEALTH_TIMEOUT=180
NO_KIOSK=0
NO_START=0
FRONTEND_PORT_OVERRIDE=""

C_RED='\033[31m'; C_GRN='\033[32m'; C_YLW='\033[33m'; C_CYN='\033[36m'; C_RST='\033[0m'
info()  { echo -e "${C_CYN}[INFO]${C_RST} $*"; }
ok()    { echo -e "${C_GRN}[ OK ]${C_RST} $*"; }
warn()  { echo -e "${C_YLW}[WARN]${C_RST} $*"; }
die()   { echo -e "\n${C_RED}[失败]${C_RST} $*\n" >&2; exit 1; }
step()  { echo; echo -e "${C_CYN}━━━━━━━━━━ 第 $1 步 / 9：$2 ━━━━━━━━━━${C_RST}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-kiosk) NO_KIOSK=1; shift ;;
    --no-start) NO_START=1; shift ;;
    --port)     FRONTEND_PORT_OVERRIDE="${2:-}"; shift 2 ;;
    -h|--help)  sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done

# 读取包内 VERSION
[[ -f "${PKG_DIR}/VERSION" ]] || die "找不到 ${PKG_DIR}/VERSION，请在解压后的安装包目录内执行本脚本"
# shellcheck disable=SC1090
PKG_ARCH="$(grep -E '^ARCH=' "${PKG_DIR}/VERSION" | cut -d= -f2)"
PKG_VERSION="$(grep -E '^VERSION=' "${PKG_DIR}/VERSION" | cut -d= -f2)"
PKG_SKIP_BACKEND="$(grep -E '^SKIP_BACKEND=' "${PKG_DIR}/VERSION" | cut -d= -f2 || echo 0)"

echo "============================================================"
echo "  能源可信数据空间平台  离线安装程序  v${PKG_VERSION} (${PKG_ARCH})"
echo "============================================================"
[[ "${PKG_SKIP_BACKEND}" == "1" ]] && warn "本包为 --skip-backend 演练包，不含后端镜像与 SQL，仅用于流程验证"

# ============================================================
# 第 1 步：root 权限
# ============================================================
step 1 "检查 root 权限"
[[ "$(id -u)" -eq 0 ]] || die "需要 root 权限，请执行：sudo ./install.sh"
ok "当前为 root"

# ============================================================
# 第 2 步：架构一致性（armv7l 直接拒绝）
# ============================================================
step 2 "检查 CPU 架构"
MACHINE="$(uname -m)"
case "${MACHINE}" in
  x86_64|amd64)  HOST_ARCH=amd64 ;;
  aarch64|arm64) HOST_ARCH=arm64 ;;
  armv7l|armv6l)
    die "检测到 32 位 ARM 系统（uname -m = ${MACHINE}）。
       mysql:8.0 等镜像没有 armv7 变体，本平台无法在 32 位系统上运行。
       请用 Raspberry Pi Imager 重新刷写【Raspberry Pi OS (64-bit)】后再安装。" ;;
  *) die "不支持的架构：${MACHINE}（仅支持 x86_64 / aarch64）" ;;
esac
if [[ "${HOST_ARCH}" != "${PKG_ARCH}" ]]; then
  die "本包为 ${PKG_ARCH}，当前机器为 ${HOST_ARCH}（uname -m = ${MACHINE}），请使用对应架构的安装包：
       energy-tds-v${PKG_VERSION}-linux-$([[ ${HOST_ARCH} == amd64 ]] && echo x86_64 || echo arm64).tar.gz"
fi
ok "架构匹配：${MACHINE} ↔ 包 ${PKG_ARCH}"

# ============================================================
# 第 3 步：内存 / 磁盘
# ============================================================
step 3 "检查内存 ≥ 3.5GB、磁盘 ≥ 8GB"
MEM_MB=$(( $(grep MemTotal /proc/meminfo | awk '{print $2}') / 1024 ))
if [[ ${MEM_MB} -lt 3500 ]]; then
  die "内存不足：当前 ${MEM_MB}MB，要求 ≥ 3584MB（3.5GB）。树莓派请使用 4GB 及以上机型。"
fi
ok "内存 ${MEM_MB}MB"
mkdir -p "$(dirname "${INSTALL_DIR}")"
DISK_GB=$(df -BG --output=avail "$(dirname "${INSTALL_DIR}")" | tail -1 | tr -dc '0-9')
DOCKER_DISK_GB=$(df -BG --output=avail /var/lib 2>/dev/null | tail -1 | tr -dc '0-9' || echo "${DISK_GB}")
if [[ ${DISK_GB} -lt 8 || ${DOCKER_DISK_GB} -lt 8 ]]; then
  die "磁盘不足：/opt 剩余 ${DISK_GB}GB，/var/lib 剩余 ${DOCKER_DISK_GB}GB，要求均 ≥ 8GB。"
fi
ok "磁盘 /opt ${DISK_GB}GB，/var/lib ${DOCKER_DISK_GB}GB"

# ============================================================
# 第 4 步：Docker（无则离线安装）
# ============================================================
step 4 "检测 / 离线安装 Docker"

install_compose_plugin() {
  local src="${PKG_DIR}/docker/docker-compose-${PKG_ARCH}"
  [[ -f "${src}" ]] || die "安装包缺少 docker/docker-compose-${PKG_ARCH}，无法安装 compose 插件"
  for dir in /usr/libexec/docker/cli-plugins /usr/local/lib/docker/cli-plugins; do
    mkdir -p "${dir}"
    install -m 0755 "${src}" "${dir}/docker-compose"
  done
  ok "已安装 docker compose 插件：$(docker compose version 2>/dev/null || echo '?')"
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "已有 Docker：$(docker version --format '{{.Server.Version}}')"
else
  if command -v docker >/dev/null 2>&1; then
    warn "检测到 docker 命令但守护进程未运行，尝试启动…"
    systemctl enable --now docker 2>/dev/null || true
  fi
  if ! docker info >/dev/null 2>&1; then
    info "未检测到可用 Docker，开始离线安装（静态二进制）"
    tgz="${PKG_DIR}/docker/docker-${PKG_ARCH}.tgz"
    [[ -f "${tgz}" ]] || die "安装包缺少 ${tgz}。
       手动安装指引：在联网机器下载 download.docker.com/linux/static/stable/<arch>/docker-<ver>.tgz，
       解压后把所有二进制复制到 /usr/bin/，再重新运行本脚本。"
    command -v systemctl >/dev/null 2>&1 || die "目标机没有 systemd，无法自动管理 Docker 服务，请手动安装 Docker 后重试"
    tmp="$(mktemp -d)"
    tar -xzf "${tgz}" -C "${tmp}"
    install -m 0755 "${tmp}"/docker/* /usr/bin/
    rm -rf "${tmp}"
    install -m 0644 "${PKG_DIR}/docker/docker.service" /etc/systemd/system/docker.service
    getent group docker >/dev/null || groupadd docker
    systemctl daemon-reload
    systemctl enable --now docker
    for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
    docker info >/dev/null 2>&1 || die "Docker 启动失败，请查看：journalctl -u docker -n 50"
    ok "Docker 离线安装完成：$(docker version --format '{{.Server.Version}}')"
  fi
fi
if docker compose version >/dev/null 2>&1; then
  ok "docker compose 可用：$(docker compose version --short 2>/dev/null || true)"
else
  install_compose_plugin
fi

# ============================================================
# 第 5 步：端口占用
# ============================================================
step 5 "检测端口占用（80 / 8000 / 8100 / 3306 / 6379）"

# 读取 .env.example 默认端口（若已有旧 .env 则以旧 .env 为准）
ENV_SRC="${PKG_DIR}/config/.env.example"
[[ -f "${INSTALL_DIR}/.env" ]] && ENV_SRC="${INSTALL_DIR}/.env"
get_env() { grep -E "^$1=" "${ENV_SRC}" | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//' | tr -d '[:space:]'; }
FRONTEND_PORT="${FRONTEND_PORT_OVERRIDE:-$(get_env FRONTEND_PORT)}"; FRONTEND_PORT="${FRONTEND_PORT:-80}"

port_users() {   # 打印占用该端口的进程
  if command -v ss >/dev/null 2>&1; then ss -ltnp 2>/dev/null | awk -v p=":$1" '$4 ~ p"$" {print "    " $0}'
  elif command -v netstat >/dev/null 2>&1; then netstat -ltnp 2>/dev/null | awk -v p=":$1" '$4 ~ p"$" {print "    " $0}'
  else grep -i ":$(printf '%04X' "$1") " /proc/net/tcp /proc/net/tcp6 2>/dev/null | awk '{print "    " $0}'; fi
}
port_in_use() { [[ -n "$(port_users "$1")" ]]; }

# 若本机已有旧版本在跑，先停掉，避免把自己当成冲突
if [[ -f "${INSTALL_DIR}/docker-compose.yml" ]] && docker compose -f "${INSTALL_DIR}/docker-compose.yml" ps -q 2>/dev/null | grep -q .; then
  warn "检测到已安装的旧实例正在运行，先停止（数据卷保留）"
  docker compose -f "${INSTALL_DIR}/docker-compose.yml" down >/dev/null 2>&1 || true
fi

if port_in_use "${FRONTEND_PORT}"; then
  echo "  端口 ${FRONTEND_PORT} 已被占用，占用进程："; port_users "${FRONTEND_PORT}"
  die "请停止该进程，或用 --port <其它端口> 重新安装（对应 .env 的 FRONTEND_PORT）"
fi
ok "端口 ${FRONTEND_PORT}（前端）空闲"
for p in 8000 8100 3306 6379; do
  if port_in_use "${p}"; then
    echo "  端口 ${p} 被占用，占用进程："; port_users "${p}"
    warn "该端口仅在容器网络内部使用、未映射到宿主机，不影响安装；如需对外暴露请调整 .env 中的对应端口变量"
  else
    ok "端口 ${p} 空闲"
  fi
done

# ============================================================
# 第 6 步：docker load + SHA256 校验
# ============================================================
step 6 "导入镜像并校验 SHA256"
[[ -f "${PKG_DIR}/images/SHA256SUMS" ]] || die "缺少 images/SHA256SUMS，安装包可能不完整，请重新拷贝"
(cd "${PKG_DIR}/images" && sha256sum -c --quiet SHA256SUMS) \
  || die "镜像文件校验失败，安装包可能已损坏或拷贝不完整。请重新下载 / 拷贝 tar.gz 并核对 .sha256 文件"
ok "SHA256 校验通过"
for f in "${PKG_DIR}"/images/*.tar.gz; do
  info "docker load $(basename "${f}") …"
  docker load -q -i "${f}" || die "镜像导入失败：${f}。请检查磁盘空间（df -h /var/lib/docker）"
done
ok "全部镜像导入完成"
docker images --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E 'energy-tds|mysql|mariadb|redis' || true

# ============================================================
# 第 7 步：生成 .env（随机密钥）
# ============================================================
step 7 "生成 ${INSTALL_DIR}/.env"
mkdir -p "${INSTALL_DIR}"
rand_hex() { if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"; else head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; fi; }

if [[ -f "${INSTALL_DIR}/.env" ]]; then
  warn "已存在 .env（上次安装生成），保留原密钥以兼容已有数据卷；如需重置请先 uninstall.sh --purge"
else
  # 去掉模板里的行尾注释，保证值干净
  sed -E 's/[[:space:]]+#.*$//' "${PKG_DIR}/config/.env.example" > "${INSTALL_DIR}/.env"
  sed -i -e "s|^MYSQL_ROOT_PASSWORD=.*|MYSQL_ROOT_PASSWORD=$(rand_hex 16)|" \
         -e "s|^MYSQL_PASSWORD=.*|MYSQL_PASSWORD=$(rand_hex 16)|" \
         -e "s|^JWT_SECRET=.*|JWT_SECRET=$(rand_hex 32)|" \
         "${INSTALL_DIR}/.env"
  ok "已随机生成 MYSQL_ROOT_PASSWORD / MYSQL_PASSWORD / JWT_SECRET"
fi
if [[ -n "${FRONTEND_PORT_OVERRIDE}" ]]; then
  sed -i "s|^FRONTEND_PORT=.*|FRONTEND_PORT=${FRONTEND_PORT_OVERRIDE}|" "${INSTALL_DIR}/.env"
fi
chmod 600 "${INSTALL_DIR}/.env"

# 复制运行所需文件
install -m 0644 "${PKG_DIR}/config/docker-compose.yml" "${INSTALL_DIR}/docker-compose.yml"
mkdir -p "${INSTALL_DIR}/config" "${INSTALL_DIR}/sql"
install -m 0644 "${PKG_DIR}/config/mysql.cnf" "${INSTALL_DIR}/config/mysql.cnf"
if compgen -G "${PKG_DIR}/sql/*.sql" >/dev/null; then
  cp "${PKG_DIR}"/sql/*.sql "${INSTALL_DIR}/sql/"
else
  warn "安装包 sql/ 为空，数据库不会自动建表（演练包）"
fi
install -m 0755 "${PKG_DIR}/uninstall.sh" "${INSTALL_DIR}/uninstall.sh"
install -m 0644 "${PKG_DIR}/VERSION" "${INSTALL_DIR}/VERSION"
cp "${PKG_DIR}/部署说明.md" "${INSTALL_DIR}/部署说明.md" 2>/dev/null || true
ok "运行文件已复制到 ${INSTALL_DIR}"

# ARM：swap 调到 2GB（树莓派 dphys-swapfile）
if [[ "${HOST_ARCH}" == "arm64" && -f /etc/dphys-swapfile ]]; then
  cur=$(grep -E '^CONF_SWAPSIZE=' /etc/dphys-swapfile | cut -d= -f2 || echo 0)
  if [[ "${cur:-0}" -lt 2048 ]]; then
    info "ARM 设备：把 swap 调整为 2048MB（原 ${cur:-默认}MB）"
    sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
    grep -q '^CONF_SWAPSIZE=' /etc/dphys-swapfile || echo 'CONF_SWAPSIZE=2048' >> /etc/dphys-swapfile
    systemctl restart dphys-swapfile 2>/dev/null || warn "dphys-swapfile 重启失败，重启系统后生效"
    ok "swap: $(free -m | awk '/Swap/ {print $2}')MB"
  else
    ok "swap 已是 ${cur}MB，无需调整"
  fi
fi

# ============================================================
# 第 8 步：启动并等待健康检查
# ============================================================
step 8 "docker compose up -d 并等待 /api/v1/health（最长 ${HEALTH_TIMEOUT}s）"
cd "${INSTALL_DIR}"
if [[ ${NO_START} -eq 1 ]]; then
  warn "--no-start：跳过启动"
else
  docker compose up -d || die "容器启动失败，请查看：docker compose -f ${INSTALL_DIR}/docker-compose.yml logs --tail=50"
  HEALTH_URL="http://localhost:${FRONTEND_PORT}/api/v1/health"
  [[ "${PKG_SKIP_BACKEND}" == "1" ]] && HEALTH_URL="http://localhost:${FRONTEND_PORT}/healthz"
  info "等待 ${HEALTH_URL} …"
  healthy=0
  for i in $(seq 1 $((HEALTH_TIMEOUT / 3))); do
    if curl -fs --max-time 3 "${HEALTH_URL}" >/dev/null 2>&1 || wget -q -T 3 -O /dev/null "${HEALTH_URL}" 2>/dev/null; then
      healthy=1; break
    fi
    printf '.'; sleep 3
  done
  echo
  if [[ ${healthy} -eq 1 ]]; then
    ok "平台健康检查通过（耗时约 $((i * 3))s）"
  else
    echo "---------------- docker compose ps ----------------"
    docker compose ps || true
    echo "---------------- docker compose logs --tail=50 ----------------"
    docker compose logs --tail=50 || true
    die "等待 ${HEALTH_TIMEOUT}s 仍未通过健康检查。常见原因：
       1) MySQL 首次初始化（导入种子数据）在树莓派 SD 卡上可能超过 3 分钟，可再等几分钟后执行：
          curl ${HEALTH_URL}
       2) 内存不足被 OOM：free -m，查看 docker compose ps 是否有 Exited 容器
       3) 查看完整日志：docker compose -f ${INSTALL_DIR}/docker-compose.yml logs -f"
  fi
fi

# ============================================================
# 第 9 步：systemd 开机自启 + 打印结果
# ============================================================
step 9 "安装 systemd 单元并启用开机自启"
install -m 0644 "${PKG_DIR}/systemd/energy-tds.service" /etc/systemd/system/energy-tds.service
# 静态安装的 docker 在 /usr/bin/docker；若为发行版安装可能在别处，做一次替换
DOCKER_BIN="$(command -v docker)"
sed -i "s|/usr/bin/docker|${DOCKER_BIN}|g" /etc/systemd/system/energy-tds.service
systemctl daemon-reload
systemctl enable energy-tds.service >/dev/null 2>&1
ok "energy-tds.service 已启用（开机自启）"

KIOSK_MSG=""
if [[ "${HOST_ARCH}" == "arm64" && ${NO_KIOSK} -eq 0 && -f "${PKG_DIR}/systemd/energy-tds-kiosk.service" ]]; then
  if command -v chromium-browser >/dev/null 2>&1 || command -v chromium >/dev/null 2>&1; then
    KIOSK_USER="${SUDO_USER:-pi}"
    [[ "${KIOSK_USER}" == "root" ]] && KIOSK_USER="pi"
    if id "${KIOSK_USER}" >/dev/null 2>&1; then
      sed "s|__KIOSK_USER__|${KIOSK_USER}|g" "${PKG_DIR}/systemd/energy-tds-kiosk.service" \
        > /etc/systemd/system/energy-tds-kiosk.service
      systemctl daemon-reload
      systemctl enable energy-tds-kiosk.service >/dev/null 2>&1
      KIOSK_MSG="  全屏展示   energy-tds-kiosk.service 已启用（用户 ${KIOSK_USER}，重启后 Chromium 自动全屏）"
      ok "Kiosk 单元已启用（用户 ${KIOSK_USER}）；请确保已设置桌面自动登录：sudo raspi-config → Boot / Auto Login"
    else
      warn "找不到桌面用户 ${KIOSK_USER}，跳过 kiosk 单元"
    fi
  else
    warn "未安装 chromium，跳过 kiosk 全屏单元（sudo apt install -y chromium 后可手动启用）"
  fi
fi

# 访问地址
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"; HOST_IP="${HOST_IP:-127.0.0.1}"
URL="http://${HOST_IP}"; [[ "${FRONTEND_PORT}" != "80" ]] && URL="${URL}:${FRONTEND_PORT}"

echo
echo "════════════════════════════════════════════"
echo "  能源可信数据空间平台  安装完成"
echo "════════════════════════════════════════════"
echo "  访问地址   ${URL}"
echo "  演示账号   admin / admin123        系统管理员"
echo "             grid / grid123          电网调度方"
echo "             vpp / vpp123            虚拟电厂运营商"
echo "             subject / subject123    能源主体"
echo "             regulator / reg123      监管方"
echo "             edge / edge123          边缘节点"
echo
echo "  服务管理   systemctl start|stop|status energy-tds"
echo "  查看日志   docker compose -f ${INSTALL_DIR}/docker-compose.yml logs -f"
echo "  卸载       sudo ${INSTALL_DIR}/uninstall.sh"
[[ -n "${KIOSK_MSG}" ]] && echo "${KIOSK_MSG}"
echo "════════════════════════════════════════════"
