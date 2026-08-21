#!/usr/bin/env bash
# ============================================================
# 目标机加载 build-offline.sh 产出的离线镜像包并启动
#   在解压目录执行：sudo ./load-offline.sh [--no-up]
# ============================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NO_UP=0
[[ "${1:-}" == "--no-up" ]] && NO_UP=1
die() { echo "[失败] $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "目标机没有 Docker；无 Docker 的机器请使用 packaging/ 正式安装包（内含 Docker 离线二进制）"
docker info >/dev/null 2>&1 || die "无法连接 docker 守护进程（需要 sudo）"
docker compose version >/dev/null 2>&1 || die "缺少 docker compose 插件"
[[ -f "${HERE}/images.tar" ]] || die "找不到 images.tar，请在解压目录内执行"

echo "[1/3] docker load（约 1~2 分钟）"
docker load -i "${HERE}/images.tar"

echo "[2/3] 准备 .env"
cd "${HERE}"
[[ -f .env ]] || { cp .env.example .env; echo "  已从 .env.example 生成 .env（开发默认密码，仅限联调）"; }

if [[ ${NO_UP} -eq 1 ]]; then echo "跳过启动（--no-up）"; exit 0; fi
echo "[3/3] docker compose up -d"
docker compose up -d
PORT="$(grep -E '^FRONTEND_PORT=' .env | cut -d= -f2 | tr -d '[:space:]')"; PORT="${PORT:-80}"
echo "启动完成，访问 http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PORT}  （admin / admin123）"
echo "查看日志：docker compose logs -f"
