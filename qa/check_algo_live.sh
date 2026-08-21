#!/usr/bin/env bash
# qa：起 uvicorn（后台）→ curl 打全部 6 个接口并用 python 校验 JSON 字段 → 杀进程
# 用法：bash qa/check_algo_live.sh [port]
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
PORT="${1:-8199}"
BASE="http://127.0.0.1:$PORT/algo/v1"
LOG="$(mktemp)"
FAIL=0

cd "$ROOT/algo-service" || exit 1
FL_ROUND_DELAY=0 DEEPSEEK_API_KEY= ALGO_PORT="$PORT" "$PY" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; wait $PID 2>/dev/null; rm -f "$LOG"' EXIT

# 等待就绪（最多 20s）
for _ in $(seq 1 40); do
  curl -fs "$BASE/health" >/dev/null 2>&1 && break
  sleep 0.5
done
if ! curl -fs "$BASE/health" >/dev/null 2>&1; then
  echo "[FAIL] uvicorn 未在 20s 内就绪"; cat "$LOG"; exit 1
fi

check() { # name, python-expr on `b`(json) and `h`(headers dict)
  local name="$1" body="$2" hdr="$3" expr="$4"
  if "$PY" - "$body" "$hdr" "$expr" <<'PYEOF'
import json, sys
body, hdr, expr = sys.argv[1], sys.argv[2], sys.argv[3]
b = json.loads(body)
h = {}
hdr_raw = hdr
for line in hdr.splitlines():
    if ":" in line:
        k, v = line.split(":", 1); h[k.strip().lower()] = v.strip()
assert eval(expr), expr
PYEOF
  then echo "[ OK ] $name"; else echo "[FAIL] $name"; echo "       body=$body"; FAIL=1; fi
}

req() { # method path json -> sets BODY, HDR
  local m="$1" p="$2" d="${3:-}"
  local out
  out=$(curl -s -D - -X "$m" -H 'Content-Type: application/json' -H 'X-Trace-Id: tr-20260821-qa00live' ${d:+-d "$d"} "$BASE$p")
  HDR="${out%%$'\r\n\r\n'*}"
  BODY="${out##*$'\r\n\r\n'}"
}

req GET /health
check "health" "$BODY" "$HDR" "b['status']=='ok' and b['models']['dqn']=='loaded' and b['models']['deepseek'] in ('live','cache') and h.get('x-trace-id')=='tr-20260821-qa00live'"

JOB="fl-live-$$"
req POST /fl/train "{\"jobId\":\"$JOB\",\"rounds\":5,\"nodes\":[{\"id\":\"Node-A\",\"samples\":480},{\"id\":\"Node-B\",\"samples\":320},{\"id\":\"Node-C\",\"samples\":400},{\"id\":\"Node-D\",\"samples\":360}],\"dp\":{\"enabled\":true,\"epsilon\":1.0,\"delta\":1e-5},\"topk\":{\"enabled\":true,\"ratio\":0.1}}"
check "fl/train" "$BODY" "$HDR" "b['jobId']=='$JOB' and b['status']=='running'"
for _ in $(seq 1 100); do
  req GET "/fl/jobs/$JOB"
  echo "$BODY" | grep -q '"status": *"success"' && break
  sleep 0.2
done
check "fl/jobs 终态" "$BODY" "$HDR" "b['status']=='success' and len(b['rounds'])==5 and all(len(r['gradientHash'].split(':')[-1])==64 for r in b['rounds']) and abs(b['rounds'][0]['compressionRatio']-90)<3 and 'anomaly' in b"
# 运行中的任务：重复 jobId → 409；cancel → cancelled
JOB2="fl-live-long-$$"
req POST /fl/train "{\"jobId\":\"$JOB2\",\"rounds\":500,\"nodes\":[{\"id\":\"Node-A\",\"samples\":480},{\"id\":\"Node-B\",\"samples\":320},{\"id\":\"Node-C\",\"samples\":400}]}"
check "fl/train(长任务)" "$BODY" "$HDR" "b['status']=='running'"
req POST /fl/train "{\"jobId\":\"$JOB2\",\"rounds\":5,\"nodes\":[{\"id\":\"Node-A\",\"samples\":100}]}"
check "fl/train 运行中重复 jobId → 409" "$BODY" "$HDR" "'error' in b and 'HTTP/1.1 409' in hdr_raw"
req POST "/fl/jobs/$JOB2/cancel"
check "fl/cancel" "$BODY" "$HDR" "b['jobId']=='$JOB2' and b['status']=='cancelled'"

req POST /dqn/dispatch '{"taskId":"dp-live","timeWindow":"2026-08-21T15:00~16:00+08:00","nodes":[{"id":"Node-A","pv":45.3,"load":120,"soc":65,"storage":-12.0,"price":0.62},{"id":"Node-C","pv":28.7,"load":150,"soc":15,"storage":-25.3,"price":0.62}]}'
check "dqn/dispatch" "$BODY" "$HDR" "b['taskId']=='dp-live' and all(a['action'] in ('charge','idle','discharge') and a['powerKw']<=30 for a in b['actions']) and [a for a in b['actions'] if a['nodeId']=='Node-C'][0]['action']!='discharge' and b['constraintsChecked']['socMin']==20 and len(b['qTable'])==2"

req POST /deepseek/analyze '{"scene":"dispatch","context":{"taskId":"dp-live"},"question":"为什么？"}'
check "deepseek/analyze" "$BODY" "$HDR" "b['answer'] and b['source'] in ('live','cache','rule') and isinstance(b['reasoning'],list) and isinstance(b['latencyMs'],int)"

req POST /classify '{"records":[{"dataType":"pv","fields":["power","voltage","gps"],"freq":"minute","volume":1440}]}'
check "classify" "$BODY" "$HDR" "b['results'][0]['level'] in ('L3','L4') and 'clusterCenters' in b and 'factors' in b['results'][0]"

req POST /risk/assess '{"nodeId":"Node-A","features":{"queryFreq":12,"dataGranularity":"minute","exposedFields":6,"epsilonRemaining":0.58}}'
check "risk/assess" "$BODY" "$HDR" "b['nodeId']=='Node-A' and b['level'] in ('low','medium','high','critical') and abs(sum(f['weight'] for f in b['factors'])-1)<1e-3 and b['suggestion']"

if [ "$FAIL" = 0 ]; then echo "== algo live check: ALL PASS =="; else echo "== algo live check: FAILED =="; fi
exit $FAIL
