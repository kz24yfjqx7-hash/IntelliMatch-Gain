#!/usr/bin/env bash
# qa：npm run build + 源码外网 URL 扫描（白名单过滤）+ dist 内 CDN 扫描
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend" || exit 1
FAIL=0

echo "== 1. npm run build =="
if npm run build >/tmp/qa-frontend-build.log 2>&1; then
  echo "[ OK ] build 成功（$(grep -c "dist/" /tmp/qa-frontend-build.log) 个产物）"
else
  echo "[FAIL] build 失败："; tail -30 /tmp/qa-frontend-build.log; FAIL=1
fi
if grep -qi "error" /tmp/qa-frontend-build.log; then echo "[WARN] build 日志含 error 字样："; grep -i "error" /tmp/qa-frontend-build.log | head -5; fi

echo "== 2. src/ 外网 URL 扫描（白名单：w3id.org/did/v1）=="
HITS=$(grep -rn --include='*.js' --include='*.vue' --include='*.css' --include='*.html' --include='*.json' -E "https?://" src index.html 2>/dev/null | grep -v "w3id.org/did/v1" || true)
if [ -z "$HITS" ]; then echo "[ OK ] src/ 无外网 URL"; else echo "[FAIL] src/ 发现外网 URL："; echo "$HITS"; FAIL=1; fi

echo "== 3. dist/ CDN 扫描 =="
if [ -d dist ]; then
  DH=$(grep -rlE "cdn\.|googleapis|jsdelivr|unpkg\.com|cdnjs" dist/ 2>/dev/null || true)
  if [ -z "$DH" ]; then echo "[ OK ] dist/ 无 CDN 引用"; else echo "[FAIL] dist/ 含 CDN 引用："; echo "$DH"; FAIL=1; fi
  [ -f dist/mockServiceWorker.js ] && echo "[ OK ] dist/mockServiceWorker.js 存在" || echo "[WARN] dist/mockServiceWorker.js 缺失（MSW 兜底镜像需要）"
  [ -f dist/index.html ] && echo "[ OK ] dist/index.html 存在" || { echo "[FAIL] dist/index.html 缺失"; FAIL=1; }
else
  echo "[FAIL] dist/ 不存在"; FAIL=1
fi

echo "== 4. public/mockServiceWorker.js =="
[ -f public/mockServiceWorker.js ] && echo "[ OK ] 存在" || { echo "[FAIL] public/mockServiceWorker.js 缺失（npx msw init public/）"; FAIL=1; }

[ "$FAIL" = 0 ] && echo "== frontend build check: ALL PASS ==" || echo "== frontend build check: FAILED =="
exit $FAIL
