#!/usr/bin/env bash
# ============================================================
# 单架构快速离线镜像包（开发联调用，非正式安装包）
#   在开发机执行：./deploy/build-offline.sh [--skip-backend] [--mock] [-o dist]
#   产物：dist/energy-tds-images-<arch>-<日期>.tar.gz
#         内含 images.tar（docker save 全部镜像）+ docker-compose.yml + .env.example + deploy/mysql + backend/sql
#   目标机：tar -xzf ... && ./load-offline.sh
#   正式双架构安装包请用 packaging/build.sh。
# ============================================================
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/dist"
SKIP_BACKEND=0
MOCK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-backend) SKIP_BACKEND=1; shift ;;
    --mock) MOCK=1; shift ;;            # 前端打成 MSW 模式（答辩兜底）
    -o) OUT_DIR="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done
die() { echo "[失败] $*" >&2; exit 1; }

docker info >/dev/null 2>&1 || die "无法连接 docker 守护进程（需要 sudo 或加入 docker 组）"
cd "${ROOT_DIR}"
[[ -f .env ]] || cp .env.example .env

ARCH="$(docker version --format '{{.Server.Arch}}')"
STAMP="$(date +%Y%m%d-%H%M)"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

SERVICES=(algo-service frontend)
if [[ -d backend && ${SKIP_BACKEND} -eq 0 ]]; then
  SERVICES=(backend algo-service frontend)
elif [[ ${SKIP_BACKEND} -eq 0 ]]; then
  die "缺少 backend/ 目录；乙方独立联调请加 --skip-backend"
fi

echo "[1/4] 构建镜像：${SERVICES[*]}（${ARCH}）"
if [[ ${MOCK} -eq 1 ]]; then
  VITE_USE_MOCK=true docker compose build "${SERVICES[@]}"
else
  docker compose build "${SERVICES[@]}"
fi

echo "[2/4] 拉取基础镜像"
docker compose pull mysql redis

echo "[3/4] docker save"
IMAGES=(mysql:8.0 redis:7-alpine)
for s in "${SERVICES[@]}"; do IMAGES+=("energy-tds/${s}:1.0"); done
docker save "${IMAGES[@]}" -o "${WORK}/images.tar"

echo "[4/4] 打包"
mkdir -p "${OUT_DIR}" "${WORK}/deploy/mysql" "${WORK}/backend/sql"
cp docker-compose.yml .env.example deploy/load-offline.sh "${WORK}/"
cp deploy/mysql/mysql.cnf "${WORK}/deploy/mysql/"
[[ -d backend/sql ]] && cp backend/sql/*.sql "${WORK}/backend/sql/" 2>/dev/null || true
PKG="${OUT_DIR}/energy-tds-images-${ARCH}-${STAMP}.tar.gz"
tar -C "${WORK}" -czf "${PKG}" .
sha256sum "${PKG}" > "${PKG}.sha256"
echo "完成：${PKG} ($(du -h "${PKG}" | cut -f1))"
echo "目标机：mkdir energy-tds && tar -xzf $(basename "${PKG}") -C energy-tds && cd energy-tds && sudo ./load-offline.sh"
