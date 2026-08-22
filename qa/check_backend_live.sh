#!/usr/bin/env bash
# qa：对真后端（甲方 FastAPI）按契约 API-CONTRACT.md 第一、二部分逐接口打一遍。
# 用法：bash qa/check_backend_live.sh        （BASE 可用 BACKEND_BASE 覆盖）
# 约束：不重置数据库；新建数据均带时间戳；篡改存证只篡改本次新写的那条（链会永久断裂！）。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
BASE="${BACKEND_BASE:-http://127.0.0.1:8000/api/v1}"
WS_BASE="${BACKEND_WS:-$(echo "$BASE" | sed -E 's#^http#ws#; s#/api/v1$#/ws#')}"
BACKEND_DIR="$ROOT/backend"
TS="$(date +%Y%m%d%H%M%S)"
DATE8="$(date +%Y%m%d)"
PASS_N=0; FAIL_N=0
TOKEN=""                      # 当前请求用的 token（setuser 切换）
STATUS=""; HDR=""; BODY=""    # req 的输出
TRACE_PFX="tr-$DATE8-a$(printf "%02x" $((RANDOM%256)))"   # 自定义 X-Trace-Id 前缀（3hex + 5hex 序号 = 8hex）
SEQ=0
LAST_CURL=""

# ---------------------------------------------------------------- 基础工具
req() { # method path [json] [extra curl args...]  -> STATUS / HDR / BODY / LAST_CURL / TRACE
  local m="$1" p="$2" d="${3:-}"; shift 3 2>/dev/null || shift $#
  SEQ=$((SEQ+1))
  TRACE="$(printf '%s%05x' "$TRACE_PFX" "$SEQ")"      # tr-YYYYMMDD-8hex
  local auth=(); [ -n "$TOKEN" ] && auth=(-H "Authorization: Bearer $TOKEN")
  local data=(); [ -n "$d" ] && data=(-d "$d")
  local out
  out=$(curl -s --max-time 60 -D - -X "$m" -H 'Content-Type: application/json' -H "X-Trace-Id: $TRACE" \
        "${auth[@]}" "${data[@]}" "$@" "$BASE$p")
  HDR="${out%%$'\r\n\r\n'*}"
  BODY="${out##*$'\r\n\r\n'}"
  [ "$HDR" = "$out" ] && BODY=""
  STATUS=$(printf '%s' "$HDR" | head -1 | awk '{print $2}')
  LAST_CURL="curl -s -X $m -H 'Content-Type: application/json' -H 'X-Trace-Id: $TRACE'${TOKEN:+ -H 'Authorization: Bearer <token:'$CUR_USER'>'}${d:+ -d '$d'} '$BASE$p'"
}

# check 名称 期望code [python 表达式(可用 b=body json, d=b['data'], h=headers dict, status, trace)]
# 自动校验：HTTP 状态与 code 语义一致、包裹四键、traceId 格式、X-Trace-Id 沿用。
check() {
  local name="$1" code="$2" expr="${3:-True}"
  local res
  res=$("$PY" - "$STATUS" "$BODY" "$HDR" "$code" "$expr" "$TRACE" <<'PYEOF'
import json, re, sys
status, body, hdr, code, expr, trace = sys.argv[1:7]
CODE_HTTP = {0:200,1001:400,1002:401,1003:403,1004:403,1005:404,1006:409,1007:429,2001:502,2002:502,5000:500}
h = {}
for line in hdr.splitlines():
    if ":" in line:
        k, v = line.split(":", 1); h[k.strip().lower()] = v.strip()
errs = []
try:
    b = json.loads(body)
except Exception as e:
    print("FAIL: 响应不是 JSON (HTTP %s): %s" % (status, body[:200].replace("\n", " "))); sys.exit(1)
if not isinstance(b, dict):
    print("FAIL: 响应不是对象: %s" % body[:200]); sys.exit(1)
missing = [k for k in ("code", "message", "data", "traceId") if k not in b]
if missing: errs.append("包裹缺键 %s" % missing)
want_code = int(code)
if b.get("code") != want_code: errs.append("code 期望 %s 实际 %s" % (want_code, b.get("code")))
want_http = CODE_HTTP.get(b.get("code"), None)
try: st = int(status)
except Exception: st = -1
if want_http is not None and st != want_http:
    errs.append("HTTP 期望 %s(code %s) 实际 %s" % (want_http, b.get("code"), st))
elif want_http is None:
    errs.append("code %s 不在契约表中 (HTTP %s)" % (b.get("code"), st))
tid = b.get("traceId")
if not (isinstance(tid, str) and re.fullmatch(r"tr-\d{8}-[0-9a-f]{8}", tid)):
    errs.append("traceId 格式不符 %r" % (tid,))
if tid != trace:
    errs.append("X-Trace-Id 未沿用: 发 %s 回 %s" % (trace, tid))
d = b.get("data")
if not errs or expr != "True":
    try:
        if not eval(expr):
            errs.append("断言不成立: %s" % expr)
    except Exception as e:
        errs.append("断言异常 %s: %s" % (type(e).__name__, e))
if errs:
    print("FAIL: " + "; ".join(errs) + " | 实际: " + body[:200].replace("\n", " "))
    sys.exit(1)
print("OK")
PYEOF
  )
  if [ "$res" = "OK" ]; then
    PASS_N=$((PASS_N+1)); echo "PASS $name"
  else
    FAIL_N=$((FAIL_N+1)); echo "FAIL $name: ${res#FAIL: }"; echo "     复现: $LAST_CURL"
  fi
}

# 非 JSON 响应的裸断言：check_raw 名称 python表达式(可用 status, h, body)
check_raw() {
  local name="$1" expr="$2" res bodyfile
  # 响应体可能是几百 KB 的 CSV，用临时文件传给 python，避免 "Argument list too long"
  bodyfile=$(mktemp)
  printf '%s' "$BODY" > "$bodyfile"
  res=$("$PY" - "$STATUS" "$bodyfile" "$HDR" "$expr" <<'PYEOF'
import sys
status, bodyfile, hdr, expr = sys.argv[1:5]
body = open(bodyfile, encoding="utf-8", errors="replace").read()
h = {}
for line in hdr.splitlines():
    if ":" in line:
        k, v = line.split(":", 1); h[k.strip().lower()] = v.strip()
try:
    ok = eval(expr)
except Exception as e:
    ok = False; expr += " (%s)" % e
print("OK" if ok else "FAIL: 断言不成立: %s | 实际 HTTP %s CT=%s body=%s" % (expr, status, h.get("content-type"), body[:200].replace("\n"," ")))
PYEOF
  )
  rm -f "$bodyfile"
  if [ "$res" = "OK" ]; then PASS_N=$((PASS_N+1)); echo "PASS $name"
  else FAIL_N=$((FAIL_N+1)); echo "FAIL $name: ${res#FAIL: }"; echo "     复现: $LAST_CURL"; fi
}

# 直接记一条失败/通过（非 HTTP 场景）
record() { if [ "$1" = OK ]; then PASS_N=$((PASS_N+1)); echo "PASS $2"; else FAIL_N=$((FAIL_N+1)); echo "FAIL $2: $3"; fi; }

jget() { # 从 $BODY 取值：jget 'd["token"]'
  "$PY" -c 'import json,sys
try:
    b=json.loads(sys.argv[1]); d=b.get("data")
    v=eval(sys.argv[2]); print(v if not isinstance(v,(dict,list)) else json.dumps(v,ensure_ascii=False))
except Exception: print("")' "$BODY" "$1" 2>/dev/null
}
urlenc() { "$PY" -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }

declare -A TOKENS
CUR_USER=""
setuser() { CUR_USER="$1"; TOKEN="${TOKENS[$1]:-}"; }
nouser() { CUR_USER=""; TOKEN=""; }

echo "== backend live check  BASE=$BASE  TS=$TS =="
if ! curl -s --max-time 5 "$BASE/auth/me" >/dev/null; then
  echo "FAIL 后端不可达: $BASE"; echo "通过 0 / 失败 1"; exit 1
fi

# ================================================================ 2.1 认证与用户
nouser
for pair in admin:admin123 grid:grid123 vpp:vpp123 subject:subject123 regulator:reg123 edge:edge123; do
  u="${pair%%:*}"; pw="${pair##*:}"
  req POST /auth/login "{\"username\":\"$u\",\"password\":\"$pw\"}"
  check "login $u" 0 "d['token'] and d['expiresIn']==28800 and d['user']['username']=='$u' and isinstance(d['user']['roles'],list) and d['user']['did']"
  TOKENS[$u]="$(jget 'd["token"]')"
done
req POST /auth/login '{"username":"admin","password":"wrong-pass"}'
check "login 错密码 -> 1002/401" 1002
req GET /auth/me
check "无 token /auth/me -> 1002" 1002

setuser admin
req GET /auth/me
check "auth/me(admin) 含 permissions" 0 "d['username']=='admin' and 'sys_admin' in d['roles'] and isinstance(d['permissions'],list) and all(':' in p for p in d['permissions']) and 'dispatch:issue' in d['permissions']"
ADMIN_DID="$(jget 'd["did"]')"
setuser subject
req GET /auth/me
check "auth/me(subject)" 0 "d['username']=='subject' and isinstance(d['permissions'],list)"
SUBJECT_DID="$(jget 'd["did"]')"
setuser vpp
req GET /auth/me
check "auth/me(vpp) 无 dispatch:issue" 0 "'dispatch:issue' not in d['permissions']"

setuser admin
req GET "/users?page=1&size=5"
check "users 列表(admin) 分页" 0 "set(['items','total','page','size'])<=set(d) and d['page']==1 and d['size']==5 and d['total']>=6 and len(d['items'])==5"
setuser vpp
req GET "/users"
check "users 列表(vpp) -> 1003" 1003

# ================================================================ 2.2 DID
setuser admin
DID_NAME="qa-device-$TS"
req POST /did/register "{\"subjectType\":\"device\",\"subjectName\":\"$DID_NAME\",\"orgName\":\"QA园区\",\"metadata\":{\"model\":\"VPP-2000\",\"location\":\"QA区\"}}"
check "did/register(device)" 0 "d['did'].startswith('did:vpp:device:0x') and len(d['did'].split(':')[-1])==34 and d['didDocument']['id']==d['did'] and d['didDocument']['verificationMethod'][0]['publicKeyHex']==d['publicKey'] and d['privateKey'] and d['chainTxId'] and d['evidenceId']"
NEW_DID="$(jget 'd["did"]')"; NEW_PUB="$(jget 'd["publicKey"]')"; NEW_PRIV="$(jget 'd["privateKey"]')"
NEW_DID_ENC="$(urlenc "$NEW_DID")"
echo "     新 DID: $NEW_DID"

req GET "/did?subjectType=device&keyword=$DID_NAME&page=1&size=20"
check "did 列表(筛选 keyword)" 0 "set(['items','total','page','size'])<=set(d) and any(i.get('did')=='$NEW_DID' for i in d['items'])"

# DID 文档：冒号原样 vs URL 编码 都试一下
req GET "/did/$NEW_DID"
check "did 文档 GET /did/{did} (冒号原样)" 0 "(d.get('id')=='$NEW_DID' or d.get('did')=='$NEW_DID' or (d.get('didDocument') or {}).get('id')=='$NEW_DID')"
RAW_OK=$?
req GET "/did/$NEW_DID_ENC"
check "did 文档 GET /did/{did} (冒号 URL 编码 %3A)" 0 "(d.get('id')=='$NEW_DID' or d.get('did')=='$NEW_DID' or (d.get('didDocument') or {}).get('id')=='$NEW_DID')"
req GET "/did/$NEW_DID/document"
check "did 文档 GET /did/{did}/document (后端额外路由)" 0 "(d.get('id')=='$NEW_DID' or (d.get('didDocument') or {}).get('id')=='$NEW_DID')"
req GET "/did/did:vpp:device:0x00000000000000000000000000000000"
check "did 文档 不存在 -> 1005" 1005

# 验签：优先用 backend/core/gm_crypto.py（纯 python SM2）本地签名；失败则传假签名期望 valid=false
MSG="qa-verify-$TS"
SIG="$(cd "$BACKEND_DIR" 2>/dev/null && "$PY" -c 'import sys; sys.path.insert(0,"."); from core.gm_crypto import sign; print(sign(sys.argv[1], sys.argv[2], sys.argv[3]))' "$MSG" "$NEW_PRIV" "$NEW_PUB" 2>/dev/null)"
if [ ${#SIG} -eq 128 ]; then
  req POST /did/verify "{\"did\":\"$NEW_DID\",\"message\":\"$MSG\",\"signature\":\"$SIG\"}"
  check "did/verify 真 SM2 签名 valid=true" 0 "d['valid'] is True and d['subjectType']=='device' and d['status']=='active'"
else
  echo "     (本地 SM2 签名不可用，改传假签名)"
fi
req POST /did/verify "{\"did\":\"$NEW_DID\",\"message\":\"$MSG\",\"signature\":\"$(printf 'ab%.0s' $(seq 1 64))\"}"
check "did/verify 假签名 valid=false 且 code 0" 0 "d['valid'] is False and d.get('reason')"

req POST "/did/$NEW_DID/status" '{"action":"freeze","reason":"qa 冻结演示"}'
check "did/status freeze" 0 "d['did']=='$NEW_DID' and d['status']=='frozen' and d.get('evidenceId')"
req GET "/did/$NEW_DID_ENC"
check "did 文档 冻结后 status=frozen" 0 "'frozen' in json.dumps(d)"
req POST "/did/$NEW_DID/status" '{"action":"unfreeze","reason":"qa 解冻"}'
check "did/status unfreeze" 0 "d['status']=='active'"
req POST "/did/$NEW_DID/rotate-key" '{"reason":"qa 轮换"}'
check "did/rotate-key" 0 "json.dumps(d) and (d.get('did','$NEW_DID')=='$NEW_DID') and (d.get('publicKey') or d.get('version') or d.get('keyId') or d.get('didDocument'))"
NEW_PUB2="$(jget 'd.get("publicKey","")')"
req POST /did/resolve "{\"dids\":[\"$NEW_DID\",\"$ADMIN_DID\",\"did:vpp:device:0xdeadbeef\"]}"
check "did/resolve 批量" 0 "isinstance(d.get('items'),list) and len(d['items'])>=2 and any((i.get('did') or i.get('id'))=='$NEW_DID' for i in d['items'])"
setuser vpp
req POST "/did/$NEW_DID/status" '{"action":"freeze","reason":"越权"}'
check "did/status 非 admin -> 1003" 1003

# ================================================================ 2.3 密钥
setuser admin
req GET "/keys?did=$NEW_DID_ENC&page=1&size=20"
check "keys 列表(did 筛选, 轮换后 >=2 条)" 0 "set(['items','total','page','size'])<=set(d) and d['total']>=2 and all(i['did']=='$NEW_DID' and i['algorithm'] in ('SM2','ECC','RSA') and i['status'] in ('active','frozen','revoked') for i in d['items']) and sum(1 for i in d['items'] if i['status']=='active')==1"
KEY_ID="$(jget '[i for i in d["items"] if i["status"]=="active"][0]["id"]')"
req GET "/keys?page=1&size=3"
check "keys 列表(全量分页)" 0 "d['size']==3 and len(d['items'])<=3"
if [ -n "$KEY_ID" ]; then
  req GET "/keys/$KEY_ID/history"
  check "keys/{id}/history" 0 "isinstance(d,(list,dict))"
fi

# ================================================================ 2.4 资产
ASSET_NAME="qa-pv-$TS"
req POST /assets "{\"name\":\"$ASSET_NAME\",\"dataType\":\"pv\",\"sourceDid\":\"$NEW_DID\",\"level\":\"L2\",\"payload\":{\"pvOutput\":45.3,\"ts\":\"2026-08-22T14:00:00+08:00\"},\"description\":\"qa 分钟级采集\"}"
check "assets 登记(pv)" 0 "d['id'] and str(d['hash']).startswith('sm3:') and d['level']=='L2' and d['chainTxId'] and d['evidenceId'] and d['authStatus'] in ('unauthorized','authorized') and d['createdAt']"
ASSET_ID="$(jget 'd["id"]')"
ASSET_EV="$(jget 'd["evidenceId"]')"
req POST /assets "{\"name\":\"$ASSET_NAME-auto\",\"dataType\":\"pv\",\"sourceDid\":\"$NEW_DID\",\"payload\":{\"pvOutput\":1.0,\"gps\":\"x\"},\"description\":\"qa 自动分级\"}"
check "assets 登记(level 留空自动分级)" 0 "d['level'] in ('L1','L2','L3','L4')"
req GET "/assets?dataType=pv&sourceDid=$NEW_DID_ENC&page=1&size=20"
check "assets 列表(筛选)" 0 "set(['items','total','page','size'])<=set(d) and d['total']>=2 and any(str(i['id'])==str('$ASSET_ID') for i in d['items'])"
req GET "/assets?keyword=$ASSET_NAME&page=1&size=20"
check "assets 列表(keyword)" 0 "d['total']>=1"
req GET "/assets/$ASSET_ID"
check "assets 详情" 0 "str(d['id'])==str('$ASSET_ID') and d['name']=='$ASSET_NAME' and d['dataType']=='pv' and d['sourceDid']=='$NEW_DID'"
req GET "/assets/$ASSET_ID/lineage"
check "assets lineage 含 register" 0 "str(d['assetId'])==str('$ASSET_ID') and d.get('traceId') and any(c['stage']=='register' for c in d['chain']) and all('evidenceId' in c and 'at' in c for c in d['chain'])"
req GET "/assets/99999999"
check "assets 详情 不存在 -> 1005" 1005
req POST /assets/classify '{"records":[{"dataType":"pv","fields":["power","voltage","gps"],"freq":"minute","volume":1440},{"dataType":"load","fields":["power"],"freq":"hour","volume":24}]}'
check "assets/classify 代理算法服务" 0 "len(d['results'])==2 and all(r['level'] in ('L1','L2','L3','L4') for r in d['results']) and 'clusterCenters' in d"
req GET /assets/stats
check "assets/stats" 0 "isinstance(d['byLevel'],list) and isinstance(d['byType'],list) and d['total']>=80 and 'authorized' in d and 'onChain' in d"

# ================================================================ 2.5 权限
req GET /roles
check "roles 列表" 0 "(isinstance(d,list) and any(r['code']=='sys_admin' for r in d)) or (isinstance(d,dict) and any(r['code']=='sys_admin' for r in d.get('items',[])))"
req GET /permissions/matrix
check "permissions/matrix" 0 "set(['asset','model','dispatch','evidence','algo'])<=set(d['resources']) and set(['read','write','execute','issue','export'])<=set(d['actions']) and any(r['code']=='sys_admin' and 'grants' in r for r in d['roles'])"

setuser subject
req POST /permissions/apply "{\"resourceType\":\"asset\",\"resourceId\":\"$ASSET_ID\",\"action\":\"read\",\"reason\":\"qa 联合建模需读取 $TS\",\"expireAt\":\"2026-12-31T00:00:00+08:00\"}"
check "permissions/apply(subject)" 0 "d['id'] and d['status']=='pending' and d['applicantDid']=='$SUBJECT_DID' and d.get('evidenceId')"
APP_ID="$(jget 'd["id"]')"
req POST /permissions/apply "{\"resourceType\":\"asset\",\"resourceId\":\"$ASSET_ID\",\"action\":\"write\",\"reason\":\"qa 待驳回 $TS\"}"
check "permissions/apply 第二条(待驳回)" 0 "d['status']=='pending'"
APP_ID2="$(jget 'd["id"]')"
req POST /permissions/check "{\"did\":\"$SUBJECT_DID\",\"resourceType\":\"asset\",\"resourceId\":\"$ASSET_ID\",\"action\":\"read\"}"
check "permissions/check 审批前 返回 allowed 字段" 0 "'allowed' in d and 'reason' in d"
PRE_ALLOWED="$(jget 'd["allowed"]')"
req POST "/permissions/applications/$APP_ID/approve" '{"reason":"subject 自批"}'
check "applications/approve 非 admin -> 1003" 1003

setuser admin
req GET "/permissions/applications?status=pending&page=1&size=50"
check "permissions/applications 列表(pending)" 0 "set(['items','total','page','size'])<=set(d) and any(str(i['id'])==str('$APP_ID') for i in d['items'])"
req POST "/permissions/applications/$APP_ID/approve" '{"reason":"qa 审批通过"}'
check "applications/approve(admin)" 0 "d.get('status')=='approved' or d.get('grantId') or d.get('approved') is True"
GRANT_ID_FROM_APPROVE="$(jget 'd.get("grantId","")')"
req POST "/permissions/applications/$APP_ID2/reject" '{"reason":"qa 驳回"}'
check "applications/reject(admin)" 0 "d.get('status')=='rejected' or d.get('rejected') is True"
req GET "/permissions/applications?applicantDid=$(urlenc "$SUBJECT_DID")&page=1&size=100"
check "applications 列表(applicantDid 筛选) 状态已更新" 0 "any(str(i['id'])==str('$APP_ID') and i['status']=='approved' for i in d['items']) and any(str(i['id'])==str('$APP_ID2') and i['status']=='rejected' for i in d['items'])"
req GET "/permissions/grants?did=$(urlenc "$SUBJECT_DID")&page=1&size=100"
check "permissions/grants 列表(did 筛选) 含新授权" 0 "set(['items','total'])<=set(d) and any(str(i.get('resourceId'))==str('$ASSET_ID') and i.get('action')=='read' and i.get('status','active') in ('active','approved') for i in d['items'])"
GRANT_ID="$(jget '[i["id"] for i in d["items"] if str(i.get("resourceId"))==str("'"$ASSET_ID"'") and i.get("action")=="read" and i.get("status","active") in ("active","approved")][0]')"
[ -z "$GRANT_ID" ] && GRANT_ID="$GRANT_ID_FROM_APPROVE"
req POST /permissions/check "{\"did\":\"$SUBJECT_DID\",\"resourceType\":\"asset\",\"resourceId\":\"$ASSET_ID\",\"action\":\"read\"}"
check "permissions/check 审批后 allowed=true" 0 "d['allowed'] is True"
setuser subject
req POST /permissions/check "{\"resourceType\":\"asset\",\"resourceId\":\"$ASSET_ID\",\"action\":\"read\"}"
check "permissions/check(subject 自查, did 留空) allowed=true" 0 "d['allowed'] is True"
req GET "/assets/$ASSET_ID"
check "subject 授权后读资产详情" 0 "str(d['id'])==str('$ASSET_ID')"
req POST /permissions/check "{\"resourceType\":\"dispatch\",\"resourceId\":\"task-9\",\"action\":\"issue\"}"
check "permissions/check subject dispatch:issue allowed=false" 0 "d['allowed'] is False and d.get('reason')"
setuser admin
if [ -n "$GRANT_ID" ]; then
  req POST "/permissions/grants/$GRANT_ID/revoke" '{"reason":"qa 回收"}'
  check "permissions/grants/{id}/revoke" 0 "d.get('status') in ('revoked', None) or d.get('revoked') is True"
  req POST /permissions/check "{\"did\":\"$SUBJECT_DID\",\"resourceType\":\"asset\",\"resourceId\":\"$ASSET_ID\",\"action\":\"read\"}"
  check "permissions/check 回收后 allowed=false" 0 "d['allowed'] is False"
else
  record FAIL "permissions/grants revoke" "grants 列表里找不到 asset $ASSET_ID read 的授权 id，无法回收"
fi
req GET "/assets/$ASSET_ID/lineage"
check "assets lineage 授权后含 authorize 阶段" 0 "any(c['stage']=='authorize' for c in d['chain'])"

# ================================================================ 2.6 存证
req GET /evidence/chain/status
check "evidence/chain/status(篡改前)" 0 "d['height']>0 and d['lastHash'] and 'intact' in d and 'brokenAt' in d and d['totalRecords']>0 and isinstance(d['byCategory'],dict)"
INTACT_BEFORE="$(jget 'd["intact"]')"
BROKEN_BEFORE="$(jget 'd["brokenAt"]')"
[ "$INTACT_BEFORE" != "True" ] && echo "     !! 注意：脚本开始前链已断裂 brokenAt=$BROKEN_BEFORE（可能是之前的篡改演示），后面 intact 判断按此基线"

req POST /evidence "{\"category\":\"data\",\"refId\":\"$ASSET_ID\",\"payload\":{\"pvOutput\":45.3,\"qa\":\"$TS\"},\"actorDid\":\"$ADMIN_DID\"}"
check "evidence 写入" 0 "d['evidenceId'].startswith('ev-') and str(d['hash']).startswith('sm3:') and isinstance(d['blockHeight'],int) and d['txId'] and 'prevHash' in d and d['timestamp']"
EV_ID="$(jget 'd["evidenceId"]')"
EV_HASH="$(jget 'd["hash"]')"
EV_TRACE="$TRACE"
echo "     本次新写存证: $EV_ID（稍后只篡改这一条）"
req GET "/evidence?category=data&page=1&size=20"
check "evidence 列表(category)" 0 "set(['items','total','page','size'])<=set(d) and all(i['category']=='data' for i in d['items']) and d['total']>0"
req GET "/evidence?did=$(urlenc "$ADMIN_DID")&from=2026-08-01T00:00:00%2B08:00&to=2027-01-01T00:00:00%2B08:00&page=1&size=5"
check "evidence 列表(did/from/to)" 0 "d['size']==5"
req GET "/evidence/$EV_ID"
check "evidence 详情" 0 "d['evidenceId']=='$EV_ID' and d['hash']=='$EV_HASH' and d['category']=='data'"
req POST /evidence/verify "{\"evidenceId\":\"$EV_ID\"}"
check "evidence/verify(库内重算) intact=true" 0 "d['intact'] is True and d['localHash']==d['chainHash']=='$EV_HASH'"
req POST /evidence/verify "{\"evidenceId\":\"$EV_ID\",\"payload\":{\"pvOutput\":45.3,\"qa\":\"$TS\"}}"
check "evidence/verify(带原 payload) intact=true" 0 "d['intact'] is True"
req POST /evidence/verify "{\"evidenceId\":\"$EV_ID\",\"payload\":{\"pvOutput\":999.9}}"
check "evidence/verify(带错 payload) intact=false" 0 "d['intact'] is False and d['localHash']!=d['chainHash'] and d.get('message')"
req POST /evidence/verify '{"evidenceId":"ev-nonexist-0"}'
check "evidence/verify 不存在 -> 1005" 1005
req GET "/evidence/trace/$EV_TRACE"
check "evidence/trace/{traceId} 含本次存证" 0 "'$EV_ID' in json.dumps(d)"
req GET "/evidence/$EV_ID/certificate"
check "evidence/{id}/certificate(篡改前)" 0 "'$EV_ID' in json.dumps(d) and ('hash' in d or 'evidence' in d or 'certificate' in d)"

setuser vpp
req POST /evidence/demo/tamper "{\"evidenceId\":\"$EV_ID\",\"newValue\":{\"pvOutput\":999.9}}"
check "evidence/demo/tamper 非 admin -> 1003" 1003
setuser admin
echo "     !!!! 即将篡改本次新写的存证 $EV_ID —— 这会让链从该高度起永久断裂（演示效果），其他用例/Agent 看到 intact=false 属预期 !!!!"
req POST /evidence/demo/tamper "{\"evidenceId\":\"$EV_ID\",\"newValue\":{\"pvOutput\":999.9}}"
check "evidence/demo/tamper(admin)" 0 "d['evidenceId']=='$EV_ID' and d['tampered'] is True and d.get('hint')"
req POST /evidence/verify "{\"evidenceId\":\"$EV_ID\"}"
check "evidence/verify 篡改后 intact=false" 0 "d['intact'] is False and d['localHash']!=d['chainHash'] and d['chainHash']=='$EV_HASH' and d.get('tamperedAt') and d.get('message')"
req GET /evidence/chain/status
check "evidence/chain/status 篡改后 intact=false 且 brokenAt 非空" 0 "d['intact'] is False and d['brokenAt']"
req GET "/evidence/$EV_ID/certificate"
check "evidence/{id}/certificate(篡改后仍可导出)" 0 "'$EV_ID' in json.dumps(d)"

# ================================================================ 2.7 审计
req GET "/audit/logs?page=1&size=10"
check "audit/logs 分页" 0 "set(['items','total','page','size'])<=set(d) and d['total']>0 and all(set(['id','traceId','action','result','riskLevel','module','at']) <= set(i) for i in d['items'])"
req GET "/audit/logs?traceId=$EV_TRACE&page=1&size=10"
check "audit/logs 按 traceId 筛选" 0 "d['total']>=1 and all(i['traceId']=='$EV_TRACE' for i in d['items'])"
req GET "/audit/logs?riskLevel=high&page=1&size=10"
check "audit/logs riskLevel=high" 0 "all(i['riskLevel']=='high' for i in d['items'])"
req GET "/audit/logs?keyword=qa&actorDid=$(urlenc "$ADMIN_DID")&from=2026-08-01T00:00:00%2B08:00&to=2027-01-01T00:00:00%2B08:00&page=1&size=5"
check "audit/logs 多条件筛选" 0 "d['size']==5"
req GET "/audit/trace/$EV_TRACE"
check "audit/trace/{traceId}" 0 "d['traceId']=='$EV_TRACE' and 'summary' in d and isinstance(d['steps'],list) and len(d['steps'])>=1 and all(set(['seq','module','action','at','result'])<=set(s) for s in d['steps'])"
req GET "/audit/trace/tr-20200101-00000000"
check "audit/trace 不存在 -> 1005 (或 0 且 steps 为空)" 1005
req GET "/audit/alerts?page=1&size=10"
check "audit/alerts" 0 "set(['items','total'])<=set(d) and all('ruleCode' in i for i in d['items'])"
req GET "/audit/alerts?status=open&page=1&size=10"
check "audit/alerts?status=open" 0 "all(i.get('status')=='open' for i in d['items'])"
ALERT_ID="$(jget 'd["items"][0]["id"] if d["items"] else ""')"
if [ -n "$ALERT_ID" ]; then
  req POST "/audit/alerts/$ALERT_ID/ack" '{}'
  check "audit/alerts/{id}/ack" 0 "d.get('status')=='acked' or d.get('acked') is True"
fi
req GET "/audit/report?period=day&date=$(date +%F)"
check "audit/report?period=day" 0 "d['period']=='day' and d['date'] and 'identityOps' in d and 'permissionOps' in d and 'evidence' in d and isinstance(d['riskEvents'],list) and d['narrative'] and d['narrativeSource'] in ('live','cache','rule')"
req GET "/audit/report?period=week"
check "audit/report?period=week" 0 "d['period']=='week'"
req GET /audit/stats
check "audit/stats" 0 "all(k in d for k in ('todayLogs','highRiskLogs','openAlerts','onChainLogs','byModule','byRisk','trend'))"
req GET "/audit/logs/export?from=2026-08-01T00:00:00%2B08:00&size=100"
check_raw "audit/logs/export 返回 CSV 流而非 JSON" "status=='200' and ('csv' in h.get('content-type','').lower() or 'octet-stream' in h.get('content-type','').lower()) and 'application/json' not in h.get('content-type','') and not body.lstrip().startswith('{')"

# ================================================================ 2.8 节点
req GET /nodes
check "nodes 列表" 0 "set(['items','total'])<=set(d) and d['total']>=4 and all(i['status'] in ('online','warning','offline') and 'metrics' in i and 'did' in i for i in d['items']) and any(i['id']=='Node-A' for i in d['items'])"
req GET /nodes/Node-A
check "nodes/Node-A 详情" 0 "d['id']=='Node-A' and d['status'] in ('online','warning','offline') and all(k in d['metrics'] for k in ('pvOutput','storageOutput','load','soc'))"
req GET "/nodes/Node-A/metrics?from=2026-08-01T00:00:00%2B08:00&to=2027-01-01T00:00:00%2B08:00&interval=hour"
check "nodes/Node-A/metrics" 0 "(isinstance(d,list) and len(d)>0) or (isinstance(d,dict) and (len(d.get('items',[]))>0 or len(d.get('points',[]))>0 or len(d.get('metrics',[]))>0))"
req GET /nodes/Node-Z
check "nodes 不存在 -> 1005" 1005
req POST /nodes/Node-A/online '{"did":"did:vpp:edge:0x00000000000000000000000000000000","nonce":"qa-nonce","signature":"deadbeef"}'
check "nodes/online 假 DID/签名 -> 1004" 1004

# ================================================================ 2.9 联邦学习
req POST /fl/tasks "{\"name\":\"qa-fl-$TS\",\"nodeIds\":[\"Node-A\",\"Node-B\",\"Node-C\"],\"rounds\":3,\"dp\":{\"enabled\":true,\"epsilon\":1.0,\"delta\":1e-5},\"topk\":{\"enabled\":true,\"ratio\":0.1}}"
check "fl/tasks 创建" 0 "d['id'].startswith('fl-') and d['status']=='created' and d['createdAt'] and d.get('traceId')"
FL_ID="$(jget 'd["id"]')"
req GET "/fl/tasks?page=1&size=5"
check "fl/tasks 列表" 0 "set(['items','total','page','size'])<=set(d)"
req POST "/fl/tasks/$FL_ID/start" '{}'
check "fl/tasks/{id}/start" 0 "d.get('status') in ('running','created','success') or d.get('started') is True"
FL_FINAL=""
for _ in $(seq 1 60); do
  req GET "/fl/tasks/$FL_ID"
  FL_FINAL="$(jget 'd["status"]')"
  case "$FL_FINAL" in success|failed|cancelled) break;; esac
  sleep 1
done
check "fl/tasks/{id} 轮询终态 success(60s 内)" 0 "d['status']=='success' and d['totalRounds']==3 and d['currentRound']==3 and len(d['rounds'])==3 and all(set(['round','loss','acc','compressionRatio','epsilonSpent','gradientHash'])<=set(r) for r in d['rounds']) and all(r.get('evidenceId') for r in d['rounds']) and isinstance(d['nodes'],list) and 'epsilonSpent' in d['dp'] and 'compressionRatio' in d['topk']"
req GET "/fl/tasks/$FL_ID/rounds"
check "fl/tasks/{id}/rounds" 0 "(isinstance(d,list) and len(d)==3) or (isinstance(d,dict) and len(d.get('items') or d.get('rounds') or [])==3)"
req GET "/fl/models?page=1&size=10"
check "fl/models 列表" 0 "(isinstance(d,dict) and set(['items','total'])<=set(d) and d['total']>=1) or (isinstance(d,list) and len(d)>=1)"
req POST "/fl/tasks/$FL_ID/start" '{}'
check "fl/tasks 已完成任务再 start -> 1006" 1006
req GET /fl/tasks/fl-999999
check "fl/tasks 不存在 -> 1005" 1005

# ================================================================ 2.10 调度
req POST /dispatch/tasks "{\"name\":\"qa-dispatch-$TS\",\"nodeIds\":[\"Node-A\",\"Node-B\",\"Node-C\",\"Node-D\"],\"timeWindow\":\"2026-08-22T15:00~16:00+08:00\"}"
check "dispatch/tasks 创建" 0 "d['id'].startswith('dp-') and d['status']=='created'"
DP_ID="$(jget 'd["id"]')"
req GET "/dispatch/tasks?page=1&size=5"
check "dispatch/tasks 列表" 0 "set(['items','total'])<=set(d)"
req POST "/dispatch/tasks/$DP_ID/run" '{}'
check "dispatch/tasks/{id}/run 策略+解释" 0 "d['id']=='$DP_ID' and d['status']=='success' and isinstance(d['strategy']['actions'],list) and len(d['strategy']['actions'])>=1 and all(a['action'] in ('charge','idle','discharge') and 'powerKw' in a and 'nodeId' in a for a in d['strategy']['actions']) and 'totalReward' in d['strategy'] and d['explanation'] and d['explanationSource'] in ('live','cache','rule') and d.get('evidenceId') and d.get('traceId')"
DP_TRACE="$TRACE"
req GET "/dispatch/tasks/$DP_ID"
check "dispatch/tasks/{id} 详情含 strategy" 0 "d['id']=='$DP_ID' and d.get('strategy') and d['strategy'].get('actions')"
TARGETS="$(jget 'json.dumps(sorted(set(a["nodeId"] for a in d["strategy"]["actions"])))')"
setuser vpp
req POST "/dispatch/tasks/$DP_ID/issue" '{}'
check "dispatch issue(vpp) -> 1003" 1003
VPP_DENY_TRACE="$TRACE"
setuser admin
req GET "/audit/logs?traceId=$VPP_DENY_TRACE"
check "vpp 越权 issue 产生 high 风险 denied 审计日志" 0 "any(i['riskLevel']=='high' and i['result']=='denied' and i['action']=='dispatch:issue' for i in d['items'])"
# 签名：后端 require_signature 的待签原文由服务端计算（dispatch:issue:{taskId}:{strategyHash}），
# signature 留空时使用平台托管私钥代签（DidKey.custody=1）；传错签名应 1004。
req POST "/dispatch/tasks/$DP_ID/issue" "{\"signature\":\"$(printf 'cd%.0s' $(seq 1 64))\"}"
check "dispatch issue(admin) 假签名 -> 1004" 1004
req POST "/dispatch/tasks/$DP_ID/issue" '{}'
check "dispatch issue(admin) 托管密钥代签 issued=true" 0 "d['issued'] is True and d['commandId'].startswith('cmd-') and d['signerDid']=='$ADMIN_DID' and d.get('evidenceId') and isinstance(d['targets'],list) and len(d['targets'])>=1"
TARGET0="$(jget 'd["targets"][0]')"
req POST "/dispatch/tasks/$DP_ID/ack" "{\"nodeId\":\"${TARGET0:-Node-A}\",\"accepted\":true,\"detail\":\"qa ack\"}"
check "dispatch/tasks/{id}/ack" 0 "(d.get('taskId')=='$DP_ID' or d.get('id')=='$DP_ID') and (d.get('acked') in (True,1) or d.get('accepted') is True or d.get('ackStatus') in ('partial','acked','full') or d.get('status') in ('acked','success'))"
req GET "/dispatch/tasks/$DP_ID"
check "dispatch 详情 ack 后状态" 0 "d['status'] in ('acked','issued','success')"
req POST /dispatch/tasks/dp-999999/run '{}'
check "dispatch/tasks 不存在 -> 1005" 1005

# ================================================================ 2.11 AI
req POST /ai/analyze "{\"scene\":\"dispatch\",\"context\":{\"taskId\":\"$DP_ID\"},\"question\":\"为什么选择这些节点执行该动作？\"}"
check "ai/analyze scene=dispatch" 0 "d['answer'] and d['source'] in ('live','cache','rule') and isinstance(d['latencyMs'],int) and isinstance(d.get('reasoning',[]),list) and d.get('traceId')"
req POST /ai/analyze '{"scene":"qa","context":{},"question":"什么是差分隐私？"}'
check "ai/analyze scene=qa" 0 "d['answer'] and d['source'] in ('live','cache','rule')"
req POST /ai/analyze '{"scene":"bad","context":{},"question":"x"}'
check "ai/analyze 非法 scene -> 1001" 1001
req GET "/ai/history?page=1&size=5"
check "ai/history 分页" 0 "set(['items','total','page','size'])<=set(d) and d['total']>=2"

# ================================================================ 2.12 风险
req POST /risk/assess '{"nodeId":"Node-A","features":{"queryFreq":12,"dataGranularity":"minute","exposedFields":6,"epsilonRemaining":0.58}}'
check "risk/assess" 0 "d['nodeId']=='Node-A' and isinstance(d['riskScore'],(int,float)) and d['level'] in ('low','medium','high','critical') and isinstance(d['factors'],list) and len(d['factors'])>=1 and all(set(['name','weight','score'])<=set(f) for f in d['factors']) and d['suggestion']"
req GET "/risk/history?nodeId=Node-A&page=1&size=5"
check "risk/history 分页" 0 "set(['items','total','page','size'])<=set(d) and d['total']>=1"

# ================================================================ 2.13 WebSocket
WS_OUT="$("$PY" - "$WS_BASE" "${TOKENS[admin]:-}" <<'PYEOF'
import asyncio, json, sys
base, token = sys.argv[1], sys.argv[2]
try:
    import websockets
except ImportError:
    print("FAIL ws: .venv 缺少 websockets 库"); sys.exit(0)
async def main():
    # 1) 无 token -> 期望 4001
    try:
        async with websockets.connect(base, open_timeout=5) as ws:
            try:
                await asyncio.wait_for(ws.recv(), 5)
                print("FAIL ws 无 token 应关闭 4001: 连接未被关闭")
            except websockets.ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
                print(("PASS" if code == 4001 else "FAIL") + " ws 无 token 关闭 4001" + ("" if code == 4001 else ": 实际 close code=%s" % code))
    except websockets.InvalidStatus as e:
        print("FAIL ws 无 token 应关闭 4001: 握手被拒 HTTP %s" % e.response.status_code)
    except Exception as e:
        print("FAIL ws 无 token 应关闭 4001: %s %s" % (type(e).__name__, e))
    # 1b) 错 token
    try:
        async with websockets.connect(base + "?token=bad.token.x", open_timeout=5) as ws:
            try:
                await asyncio.wait_for(ws.recv(), 5); print("FAIL ws 错 token 应关闭 4001: 未关闭")
            except websockets.ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
                print(("PASS" if code == 4001 else "FAIL") + " ws 错 token 关闭 4001" + ("" if code == 4001 else ": 实际 close code=%s" % code))
    except Exception as e:
        print("FAIL ws 错 token 应关闭 4001: %s %s" % (type(e).__name__, e))
    # 2) 带 token：ping->pong，8 秒内收 node_status
    try:
        async with websockets.connect(base + "?token=" + token, open_timeout=5) as ws:
            await ws.send(json.dumps({"type": "ping"}))
            got_pong = False; got_node = None; bad_fmt = []
            loop = asyncio.get_event_loop(); end = loop.time() + 8
            while loop.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), max(0.1, end - loop.time()))
                except asyncio.TimeoutError:
                    break
                try: m = json.loads(raw)
                except Exception: bad_fmt.append(raw[:100]); continue
                if m.get("type") == "pong": got_pong = True
                elif m.get("type") == "node_status":
                    got_node = m
                    p = m.get("payload") or {}
                    if not (set(["nodeId","status","metrics"]) <= set(p) and "ts" in m and "traceId" in m):
                        bad_fmt.append(raw[:150])
                elif m.get("type") not in ("fl_progress","dispatch_progress","audit_alert","log","evidence_written"):
                    bad_fmt.append(raw[:100])
                if got_pong and got_node and loop.time() > end - 6:
                    pass
            print(("PASS" if got_pong else "FAIL") + " ws ping->pong" + ("" if got_pong else ": 8s 内未收到 pong"))
            print(("PASS" if got_node else "FAIL") + " ws 8s 内收到 node_status" + ("" if got_node else ": 未收到 node_status 推送"))
            print(("PASS" if not bad_fmt else "FAIL") + " ws 推送格式 {type,ts,traceId,payload}" + ("" if not bad_fmt else ": " + "; ".join(bad_fmt)[:200]))
    except Exception as e:
        print("FAIL ws 带 token 连接: %s %s" % (type(e).__name__, e))
asyncio.run(main())
PYEOF
)"
while IFS= read -r line; do
  [ -z "$line" ] && continue
  case "$line" in PASS*) PASS_N=$((PASS_N+1));; *) FAIL_N=$((FAIL_N+1));; esac
  echo "$line"
done <<< "$WS_OUT"

# ================================================================ 登出
setuser edge
req POST /auth/logout '{}'
check "auth/logout(edge)" 0
req GET /auth/me
check "logout 后 token 失效 -> 1002" 1002

echo
echo "!!!! 提醒：本次已篡改存证 $EV_ID（链从该高度起 intact=false，brokenAt 非空），这是演示防篡改的预期结果；如需恢复请用 POST /evidence/demo/restore（后端额外提供）。"
echo "通过 $PASS_N / 失败 $FAIL_N"
[ "$FAIL_N" -eq 0 ] && exit 0 || exit 1
