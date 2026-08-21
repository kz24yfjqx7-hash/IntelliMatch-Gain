#!/usr/bin/env bash
# ============================================================
# 能源可信数据空间平台 · 卸载脚本
#   sudo ./uninstall.sh            停服务、删容器与镜像、删 systemd 单元；保留数据卷与 .env
#   sudo ./uninstall.sh --purge    额外删除数据卷、.env 与 /opt/energy-tds 整个目录（数据不可恢复）
#   注意：不会卸载 Docker 本身。
# ============================================================
set -euo pipefail

INSTALL_DIR="/opt/energy-tds"
PURGE=0
ASSUME_YES=0
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$a" >&2; exit 1 ;;
  esac
done
[[ "$(id -u)" -eq 0 ]] || { echo "需要 root 权限：sudo ./uninstall.sh" >&2; exit 1; }

C_GRN='\033[32m'; C_YLW='\033[33m'; C_RST='\033[0m'
ok()   { echo -e "${C_GRN}[ OK ]${C_RST} $*"; }
warn() { echo -e "${C_YLW}[WARN]${C_RST} $*"; }

if [[ ${PURGE} -eq 1 ]]; then
  warn "--purge 模式：将删除数据库数据卷与 .env，数据不可恢复！"
  ans=yes; [[ ${ASSUME_YES} -eq 1 ]] || read -r -p "确认继续？输入 yes 继续：" ans
  [[ "${ans}" == "yes" ]] || { echo "已取消"; exit 0; }
fi

# 1. 停并禁用 systemd 单元
for unit in energy-tds-kiosk.service energy-tds.service; do
  if [[ -f "/etc/systemd/system/${unit}" ]]; then
    systemctl disable --now "${unit}" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/${unit}"
    ok "已移除 ${unit}"
  fi
done
systemctl daemon-reload 2>/dev/null || true

# 2. 停容器（--purge 时连同数据卷）
if [[ -f "${INSTALL_DIR}/docker-compose.yml" ]] && command -v docker >/dev/null 2>&1; then
  cd "${INSTALL_DIR}"
  if [[ ${PURGE} -eq 1 ]]; then
    docker compose down -v --remove-orphans >/dev/null 2>&1 || true
    ok "容器与数据卷已删除"
  else
    docker compose down --remove-orphans >/dev/null 2>&1 || true
    ok "容器已删除（数据卷 energy-tds-mysql-data / energy-tds-redis-data 已保留）"
  fi
fi
# 兜底：按容器名清理残留
for c in energy-tds-frontend energy-tds-backend energy-tds-algo energy-tds-redis energy-tds-mysql; do
  docker rm -f "${c}" >/dev/null 2>&1 || true
done

# 3. 删镜像
if command -v docker >/dev/null 2>&1; then
  DB_IMAGE="$(grep -E '^DB_IMAGE=' "${INSTALL_DIR}/VERSION" 2>/dev/null | cut -d= -f2 || echo mysql:8.0)"
  REDIS_IMAGE="$(grep -E '^REDIS_IMAGE=' "${INSTALL_DIR}/VERSION" 2>/dev/null | cut -d= -f2 || echo redis:7-alpine)"
  for img in $(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^energy-tds/' || true) "${DB_IMAGE:-mysql:8.0}" "${REDIS_IMAGE:-redis:7-alpine}"; do
    docker rmi -f "${img}" >/dev/null 2>&1 && ok "已删除镜像 ${img}" || true
  done
  docker network rm energy-tds-net >/dev/null 2>&1 || true
fi

# 4. 删安装目录
if [[ ${PURGE} -eq 1 ]]; then
  rm -rf "${INSTALL_DIR}"
  ok "已删除 ${INSTALL_DIR}"
else
  if [[ -d "${INSTALL_DIR}" ]]; then
    find "${INSTALL_DIR}" -mindepth 1 ! -name '.env' -exec rm -rf {} + 2>/dev/null || true
    ok "已清理 ${INSTALL_DIR}（保留 .env，以便重装后继续使用原数据卷）"
  fi
fi

echo
ok "卸载完成。$( [[ ${PURGE} -eq 1 ]] && echo '数据已彻底清除。' || echo '如需彻底清除数据：sudo ./uninstall.sh --purge' )"
