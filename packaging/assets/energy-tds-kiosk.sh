#!/usr/bin/env bash
# 能源可信数据空间平台 · 树莓派 Kiosk 启动脚本（由 energy-tds-kiosk.service 调用）
#   1) 等平台真正就绪：优先等 /health（nginx 反代到 backend 根路径 GET /health，后端 + 数据库都起来），最长 5 分钟；
#      演练包（无 backend）或超时则退回只等 nginx 的 /healthz，再不行也照常拉起浏览器（页面会自行重试）。
#   2) 以 kiosk 参数全屏拉起 chromium；--disable-gpu-compositing 缓解树莓派渲染卡顿。
set -u
URL="${KIOSK_URL:-http://localhost}"
WAIT_SECONDS="${KIOSK_WAIT_SECONDS:-300}"

probe() { curl -fs --max-time 3 "$1" >/dev/null 2>&1 || wget -q -T 3 -O /dev/null "$1" 2>/dev/null; }

waited=0
while [ "${waited}" -lt "${WAIT_SECONDS}" ]; do
  if probe "${URL}/health"; then echo "[kiosk] 平台健康检查通过（${waited}s）"; break; fi
  sleep 5; waited=$((waited + 5))
done
if [ "${waited}" -ge "${WAIT_SECONDS}" ]; then
  echo "[kiosk] 等待 ${WAIT_SECONDS}s 后端仍未就绪，改为检查前端 nginx"
  probe "${URL}/healthz" && echo "[kiosk] 前端 nginx 就绪" || echo "[kiosk] 前端也未就绪，仍尝试打开浏览器"
fi

BROWSER="$(command -v chromium-browser || command -v chromium || command -v chromium-browser-stable || true)"
[ -n "${BROWSER}" ] || { echo "[kiosk] 未找到 chromium，请 sudo apt install -y chromium" >&2; exit 1; }

exec "${BROWSER}" \
  --kiosk --noerrdialogs --disable-infobars --no-first-run --disable-restore-session-state \
  --disable-session-crashed-bubble --disable-gpu-compositing --disable-features=TranslateUI \
  --check-for-update-interval=31536000 --password-store=basic "${URL}"
