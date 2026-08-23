"""接口级 + 数据库级 + 安全测试主脚本。运行：backend/.venv/bin/python qa/api-tests/run_all.py
只读业务代码；所有测试实体带 test- 前缀；不重置数据库。结果写到 qa/api-tests/results/run_all.json。"""
import base64
import json
import os
import sys
import time
import datetime as dt

sys.path.insert(0, os.path.dirname(__file__))
from lib import (ACCOUNTS, Recorder, client, db_query, envelope_ok, expect_code, gm, has_keys, hdr,
                 iso_ok, ok, page_ok, req, token, uniq, BASE)

OUT = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(OUT, exist_ok=True)
R = Recorder(os.path.join(OUT, "run_all.json"))
C = R.case
CTX = {}  # 跨节共享的实体 id


# ============================================================ 2.1 认证与用户
def sec_auth():
    r, b = req("POST", "/auth/login", None, {"username": "admin", "password": "admin123"})
    d = (b.get("data") or {}) if isinstance(b, dict) else {}
    C("API-AUTH-01", "契约 2.1 login / 文档(三)3 统一鉴权", "POST /auth/login admin/admin123",
      "200 code0；data 含 token/expiresIn=28800/user{id,username,realName,roles,did,orgName}",
      ok(r, b) + [has_keys(d, ["token", "expiresIn", "user"]), (d.get("expiresIn") == 28800, f"expiresIn={d.get('expiresIn')}"),
                  has_keys(d.get("user"), ["id", "username", "realName", "roles", "did", "orgName"]),
                  (d.get("user", {}).get("roles") == ["sys_admin"], "roles≠[sys_admin]")], b, r.status_code)
    CTX["admin_did"] = d.get("user", {}).get("did")
    # token payload 结构
    try:
        payload = json.loads(base64.urlsafe_b64decode(d["token"].split(".")[1] + "=="))
        header = json.loads(base64.urlsafe_b64decode(d["token"].split(".")[0] + "=="))
    except Exception:
        payload, header = {}, {}
    C("API-AUTH-02", "契约 1.3 JWT HS256 payload sub/username/roles/did/exp", "解析 login 返回的 JWT",
      "alg=HS256；payload 含 sub/username/roles/did/exp；exp-iat≈28800",
      [(header.get("alg") == "HS256", f"alg={header.get('alg')}"), has_keys(payload, ["sub", "username", "roles", "did", "exp"]),
       (abs(payload.get("exp", 0) - payload.get("iat", 0) - 28800) <= 5, "有效期≠8h")], {"header": header, "payload": payload})
    r, b = req("POST", "/auth/login", None, {"username": "admin", "password": "wrong-pass"})
    C("API-AUTH-03", "契约 1.1 1002", "登录密码错误", "401/1002 且统一包裹；message 不区分用户名是否存在",
      expect_code(r, b, 401, 1002), b, r.status_code)
    r2, b2 = req("POST", "/auth/login", None, {"username": "test-no-such-user", "password": "x"})
    C("API-AUTH-04", "安全·账号枚举", "登录不存在的用户名", "与密码错误同样 401/1002，message 相同",
      expect_code(r2, b2, 401, 1002) + [(b2.get("message") == b.get("message"), f"message 不同：{b2.get('message')} vs {b.get('message')}")], b2, r2.status_code)
    r, b = req("POST", "/auth/login", None, {"username": "admin"})
    C("API-AUTH-05", "契约 1.1 1001", "登录缺少 password 字段", "400/1001 统一包裹（非 FastAPI 422）", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("GET", "/auth/me", None)
    C("API-AUTH-06", "契约 1.1 1002", "GET /auth/me 不带 token", "401/1002", expect_code(r, b, 401, 1002), b, r.status_code)
    r, b = req("GET", "/auth/me", "admin")
    d = (b.get("data") or {})
    C("API-AUTH-07", "契约 2.1 /auth/me permissions 扁平串", "GET /auth/me admin",
      "含 id/username/realName/roles/did/permissions；permissions 为 <res>:<act> 串且含 dispatch:issue",
      ok(r, b) + [has_keys(d, ["id", "username", "realName", "roles", "did", "permissions"]),
                  (all(":" in p for p in d.get("permissions", [])), "存在非 res:act 格式"),
                  ("dispatch:issue" in d.get("permissions", []), "admin 无 dispatch:issue")], b, r.status_code)
    r, b = req("GET", "/auth/me", "subject")
    d = (b.get("data") or {})
    C("API-AUTH-08", "DB-SCHEMA 角色矩阵 energy_subject", "GET /auth/me subject",
      "permissions 含 asset:read/asset:write/evidence:read，不含 dispatch:issue、algo:execute",
      ok(r, b) + [({"asset:read", "asset:write", "evidence:read"} <= set(d.get("permissions", [])), f"perms={d.get('permissions')}"),
                  (not {"dispatch:issue", "algo:execute"} & set(d.get("permissions", [])), "subject 拥有越权权限")], b, r.status_code)
    # 登出后 token 失效
    r, b = req("POST", "/auth/login", None, {"username": "grid", "password": "grid123"})
    t = b["data"]["token"]
    r, b = req("POST", "/auth/logout", None, raw_token=t)
    r2, b2 = req("GET", "/auth/me", None, raw_token=t)
    C("API-AUTH-09", "安全·登出后 token 失效（jti 黑名单）", "grid 登录→logout→用旧 token 访问 /auth/me",
      "logout 200；之后 401/1002", ok(r, b) + expect_code(r2, b2, 401, 1002), b2, r2.status_code)
    # JWT 篡改
    at = token("vpp")
    h, p, s = at.split(".")
    pl = json.loads(base64.urlsafe_b64decode(p + "=="))
    pl["roles"] = ["sys_admin"]
    p2 = base64.urlsafe_b64encode(json.dumps(pl).encode()).decode().rstrip("=")
    r, b = req("GET", "/users", None, raw_token=f"{h}.{p2}.{s}")
    C("API-AUTH-10", "安全·JWT 篡改 payload 提权", "vpp token 改 roles=sys_admin 不重签 → GET /users",
      "401/1002", expect_code(r, b, 401, 1002), b, r.status_code)
    h2 = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    r, b = req("GET", "/users", None, raw_token=f"{h2}.{p2}.")
    C("API-AUTH-11", "安全·alg=none", "alg=none 无签名 token", "401/1002", expect_code(r, b, 401, 1002), b, r.status_code)
    import jwt as pyjwt
    forged = pyjwt.encode({**pl, "exp": int(time.time()) + 3600}, "energy-tds-demo-secret-2026", algorithm="HS256")
    r, b = req("GET", "/users", None, raw_token=forged)
    C("API-AUTH-12", "安全·用契约示例密钥伪造 token", "用 .env 示例 JWT_SECRET 重签 sys_admin token",
      "401/1002（运行实例未使用示例密钥）", expect_code(r, b, 401, 1002), b, r.status_code)
    expired = pyjwt.encode({**pl, "exp": int(time.time()) - 10}, "any", algorithm="HS256")
    r, b = req("GET", "/users", None, raw_token=expired)
    C("API-AUTH-13", "安全·过期 token", "exp 已过期的 token", "401/1002", expect_code(r, b, 401, 1002), b, r.status_code)
    r, b = req("GET", "/auth/me", None, raw_token="garbage.token.value")
    C("API-AUTH-14", "安全·畸形 token", "Bearer garbage", "401/1002 不 500", expect_code(r, b, 401, 1002), b, r.status_code)
    # X-Trace-Id 透传
    tid = f"tr-20260822-{uniq('')[-8:]}"
    r, b = req("GET", "/auth/me", "admin", trace=tid)
    C("API-AUTH-15", "契约 1.2 traceId 沿用", f"GET /auth/me 带 X-Trace-Id:{tid}",
      "响应头 X-Trace-Id 与 body.traceId 均回显", ok(r, b) + [(r.headers.get("x-trace-id") == tid, f"头={r.headers.get('x-trace-id')}"),
                                                   (b.get("traceId") == tid, f"body traceId={b.get('traceId')}")], b, r.status_code)
    r, b = req("GET", "/auth/me", "admin")
    C("API-AUTH-16", "契约 1.2 traceId 格式", "不带 X-Trace-Id 请求", "traceId 形如 tr-YYYYMMDD-8hex",
      ok(r, b) + [(__import__("re").match(r"^tr-\d{8}-[0-9a-f]{8}$", b.get("traceId", "")) is not None, b.get("traceId"))], b, r.status_code)


def sec_users():
    r, b = req("GET", "/users", "admin", params={"page": 1, "size": 5})
    d = (b.get("data") or {})
    items = d.get("items", [])
    leak = any(k for it in items for k in it if "password" in k.lower() or "hash" in k.lower())
    C("API-USER-01", "契约 2.1 GET /users 分页", "admin GET /users?page=1&size=5", "分页结构 items/total/page/size；不含 password/hash 字段",
      ok(r, b) + [page_ok(d), (not leak, "响应含口令哈希字段"), (d.get("size") == 5, "size≠5")], b, r.status_code)
    r, b = req("GET", "/users", "vpp")
    C("API-USER-02", "契约 2.1 需 sys_admin / 1003", "vpp GET /users", "403/1003 message 点名 sys_admin",
      expect_code(r, b, 403, 1003) + [("sys_admin" in b.get("message", ""), "未点名角色")], b, r.status_code)
    uname = uniq("test-user")
    r, b = req("POST", "/users", "admin", {"username": uname, "password": "Test-123456", "realName": "测试用户",
                                          "orgName": "测试机构", "roles": ["vpp_operator"], "bindDid": True})
    CTX["user_id"] = (b.get("data") or {}).get("id")
    CTX["user_name"] = uname
    C("API-USER-03", "契约 2.1 POST /users", "admin 新建用户（bindDid）", "200；返回 id/username/did；不返回密码",
      ok(r, b) + [has_keys(b.get("data"), ["id", "username"]), ("password" not in json.dumps(b), "泄露密码")], b, r.status_code)
    rows = db_query("SELECT password_hash FROM sys_user WHERE username=%s", (uname,))
    ph = rows[0]["password_hash"] if rows else ""
    C("API-USER-04", "DB-SCHEMA 密码 bcrypt 存储", f"查 sys_user.password_hash ({uname})", "$2b$ 开头 60 字符，非明文",
      [(ph.startswith("$2") and len(ph) == 60, f"hash={ph[:10]}… len={len(ph)}"), ("Test-123456" not in ph, "明文")], {"hash": ph[:12] + "…"})
    r, b = req("POST", "/users", "admin", {"username": uname, "password": "Test-123456", "realName": "重复", "roles": ["vpp_operator"]})
    C("API-USER-05", "契约 1.1 1006 重复注册", "重复 username 新建", "409/1006", expect_code(r, b, 409, 1006), b, r.status_code)
    r, b = req("POST", "/users", "admin", {"username": uniq("test-bad"), "password": "x", "realName": "r", "roles": ["no_such_role"]})
    C("API-USER-06", "契约 1.1 1001", "roles 含不存在角色", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("PUT", f"/users/{CTX['user_id']}", "admin", {"realName": "测试用户-改", "status": "disabled"})
    C("API-USER-07", "契约 2.1 PUT /users/{id}", "修改 realName 并停用", "200 status=disabled", ok(r, b), b, r.status_code)
    r2, b2 = req("POST", "/auth/login", None, {"username": uname, "password": "Test-123456"})
    C("API-USER-08", "README 加固·停用账号即时生效", "停用后用该账号登录", "401/1002", expect_code(r2, b2, 401, 1002), b2, r2.status_code)
    req("PUT", f"/users/{CTX['user_id']}", "admin", {"status": "active"})
    r, b = req("PUT", "/users/999999", "admin", {"realName": "x"})
    C("API-USER-09", "契约 1.1 1005", "PUT 不存在用户", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("DELETE", f"/users/{CTX['user_id']}", "vpp")
    C("API-USER-10", "1003 越权", "vpp DELETE /users/{id}", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)


# ============================================================ 2.2 DID
def sec_did():
    name = uniq("test-dev")
    r, b = req("POST", "/did/register", "admin", {"subjectType": "device", "subjectName": name, "orgName": "test-园区",
                                                 "metadata": {"model": "VPP-2000"}, "custody": False})
    d = (b.get("data") or {})
    CTX["dev"] = d
    vm = (d.get("didDocument") or {}).get("verificationMethod") or [{}]
    C("API-DID-01", "契约 2.2 register / 文档(一)1 SM2 生成 DID", "注册 device DID（custody=false）",
      "did 形如 did:vpp:device:0x+32hex；didDocument @context/id/controller/verificationMethod[type=SM2VerificationKey2023]；publicKey 04 开头 130；privateKey 64；evidenceId ev-；chainTxId blk-",
      ok(r, b) + [(__import__("re").match(r"^did:vpp:device:0x[0-9a-f]{32}$", d.get("did", "")) is not None, d.get("did")),
                  has_keys(d.get("didDocument"), ["@context", "id", "controller", "verificationMethod", "created"]),
                  (vm[0].get("type") == "SM2VerificationKey2023", f"type={vm[0].get('type')}"),
                  (str(d.get("publicKey", "")).startswith("04") and len(d.get("publicKey", "")) == 130, "publicKey 格式"),
                  (len(d.get("privateKey") or "") == 64, "privateKey 长度"),
                  (str(d.get("evidenceId", "")).startswith("ev-") and str(d.get("chainTxId", "")).startswith("blk-"), "存证字段"),
                  iso_ok(d.get("didDocument", {}).get("created"))], {**b, "data": {**d, "privateKey": "***"}}, r.status_code)
    # DID = SM3(publicKey) 前 32 位
    exp_did = "did:vpp:device:0x" + gm.sm3_hex(bytes.fromhex(d.get("publicKey", "00"))) [:32]
    exp_did2 = "did:vpp:device:0x" + gm.sm3_hex(d.get("publicKey", ""))[:32]
    C("API-DID-02", "契约 2.2 DID 标识 = SM3(publicKey) 前 32 hex", "本地用 SM3 重算 DID", "与返回 did 一致",
      [(d.get("did") in (exp_did, exp_did2), f"重算 {exp_did2} ≠ {d.get('did')}")], {"did": d.get("did"), "recalc": exp_did2})
    rows = db_query("SELECT private_key_enc, custody, public_key FROM did_key WHERE did=%s", (d.get("did"),))
    C("API-DID-03", "README 私钥不明文落库（custody=false 不留存）", "查 did_key.private_key_enc", "为 NULL/空，且不等于明文私钥",
      [(bool(rows), "无密钥行"), (not rows or not rows[0]["private_key_enc"], f"private_key_enc={str(rows[0]['private_key_enc'])[:20] if rows else ''}"),
       (not rows or rows[0]["private_key_enc"] != d.get("privateKey"), "明文落库")], rows and {k: (str(v)[:24]) for k, v in rows[0].items()})
    r, b = req("POST", "/did/register", "admin", {"subjectType": "device", "subjectName": uniq("test-cust"), "custody": True})
    d2 = (b.get("data") or {})
    CTX["dev_custody"] = d2
    rows = db_query("SELECT private_key_enc FROM did_key WHERE did=%s", (d2.get("did"),))
    enc = rows[0]["private_key_enc"] if rows else None
    C("API-DID-04", "README 托管私钥 SM4 密文存储", "注册 custody=true，查 did_key.private_key_enc",
      "非空密文（hex，长度>64）且 ≠ 返回的明文私钥", ok(r, b) + [(bool(enc) and enc != d2.get("privateKey") and len(enc) > 64, f"enc={str(enc)[:20]}")],
      {"enc_prefix": str(enc)[:24], "len": len(enc or "")})
    r, b = req("POST", "/did/register", "admin", {"subjectType": "alien", "subjectName": "x"})
    C("API-DID-05", "1.6 枚举 subjectType / 1001", "subjectType=alien", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("POST", "/did/register", None, {"subjectType": "device", "subjectName": "x"})
    C("API-DID-06", "1002", "未登录注册 DID", "401/1002", expect_code(r, b, 401, 1002), b, r.status_code)
    r, b = req("GET", "/did", "admin", params={"subjectType": "device", "status": "active", "keyword": name, "page": 1, "size": 10})
    d = (b.get("data") or {})
    C("API-DID-07", "契约 2.2 GET /did 筛选分页", f"GET /did?subjectType=device&status=active&keyword={name}", "分页结构；items 均 device/active；命中刚注册的",
      ok(r, b) + [page_ok(d), (all(i.get("subjectType") == "device" and i.get("status") == "active" for i in d.get("items", [])), "筛选失效"),
                  (any(i.get("did") == CTX["dev"]["did"] for i in d.get("items", [])), "keyword 未命中")], b, r.status_code)
    r, b = req("GET", "/did", "admin", params={"page": 1, "size": 500})
    C("API-DID-08", "契约 1.4 size 最大 200", "GET /did?size=500", "400/1001 或被截到 200",
      [(b.get("code") == 1001 or (b.get("code") == 0 and b["data"]["size"] <= 200), f"size={(b.get('data') or {}).get('size')}")], b, r.status_code)
    r, b = req("GET", f"/did/{CTX['dev']['did']}", "admin")
    d = (b.get("data") or {})
    C("API-DID-09", "契约 2.2 GET /did/{did}", "查 DID 文档", "200；含 did/didDocument/status=active；不含 privateKey",
      ok(r, b) + [has_keys(d, ["did", "didDocument", "status"]), ("privateKey" not in json.dumps(d) or not d.get("privateKey"), "泄露私钥")], b, r.status_code)
    r, b = req("GET", "/did/did:vpp:device:0x00000000000000000000000000000000", "admin")
    C("API-DID-10", "1005", "查不存在 DID", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    # 验签
    msg = "test-message-" + uniq()
    sig = gm.sign(msg, CTX["dev"]["privateKey"], CTX["dev"]["publicKey"])
    r, b = req("POST", "/did/verify", "admin", {"did": CTX["dev"]["did"], "message": msg, "signature": sig})
    d = (b.get("data") or {})
    C("API-DID-11", "契约 2.2 verify / 文档(一)3 验签工具", "本地 SM2 私钥签名 → /did/verify",
      "valid=true subjectType=device status=active reason=null", ok(r, b) + [(d.get("valid") is True, f"valid={d.get('valid')}"),
                                                                       (d.get("subjectType") == "device" and d.get("status") == "active", "字段值"),
                                                                       ("reason" in d, "缺 reason")], b, r.status_code)
    r, b = req("POST", "/did/verify", "admin", {"did": CTX["dev"]["did"], "message": msg + "x", "signature": sig})
    C("API-DID-12", "安全·篡改原文验签", "原文改动后验签", "200 valid=false", ok(r, b) + [(b["data"].get("valid") is False, "篡改仍通过")], b, r.status_code)
    wrong_pub, wrong_priv = gm.generate_keypair()
    sig2 = gm.sign(msg, wrong_priv, wrong_pub)
    r, b = req("POST", "/did/verify", "admin", {"did": CTX["dev"]["did"], "message": msg, "signature": sig2})
    C("API-DID-13", "安全·错私钥签名", "用别的私钥签名", "valid=false", ok(r, b) + [(b["data"].get("valid") is False, "错私钥通过")], b, r.status_code)
    r, b = req("POST", "/did/verify", "admin", {"did": CTX["dev"]["did"], "message": msg, "signature": "zz"})
    C("API-DID-14", "健壮性·非法签名 hex", "signature=zz", "400/1001 或 valid=false，不 500",
      [(r.status_code != 500 and (b.get("code") in (0, 1001)), f"HTTP {r.status_code} code {b.get('code')}")], b, r.status_code)
    r, b = req("POST", "/did/resolve", "admin", {"dids": [CTX["dev"]["did"], "did:vpp:device:0xdeadbeef"]})
    C("API-DID-15", "契约 2.2 resolve 批量解析", "resolve 1 个存在 + 1 个不存在", "200；存在的有文档，不存在的标注",
      ok(r, b) + [(CTX["dev"]["did"] in json.dumps(b), "未解析到")], b, r.status_code)
    # 状态流转
    r, b = req("POST", f"/did/{CTX['dev']['did']}/status", "admin", {"action": "freeze", "reason": "test-冻结"})
    C("API-DID-16", "契约 2.2 status freeze / 文档(一)2 生命周期", "冻结", "status=frozen 带 evidenceId",
      ok(r, b) + [(b["data"].get("status") == "frozen", "status"), (str(b["data"].get("evidenceId", "")).startswith("ev-"), "evidenceId")], b, r.status_code)
    r, b = req("POST", "/did/verify", "admin", {"did": CTX["dev"]["did"], "message": msg, "signature": sig})
    C("API-DID-17", "文档(一)3 冻结身份验签拒绝", "冻结后验签", "valid=false status=frozen",
      ok(r, b) + [(b["data"].get("valid") is False and b["data"].get("status") == "frozen", str(b["data"]))], b, r.status_code)
    r, b = req("POST", f"/did/{CTX['dev']['did']}/status", "admin", {"action": "freeze", "reason": "重复"})
    C("API-DID-18", "1006 状态冲突", "再次冻结已冻结 DID", "409/1006", expect_code(r, b, 409, 1006), b, r.status_code)
    r, b = req("POST", f"/did/{CTX['dev']['did']}/status", "admin", {"action": "unfreeze", "reason": "test-解冻"})
    C("API-DID-19", "契约 2.2 unfreeze（挂失恢复）", "解冻", "status=active", ok(r, b) + [(b["data"].get("status") == "active", "status")], b, r.status_code)
    r, b = req("POST", f"/did/{CTX['dev']['did']}/status", "admin", {"action": "destroy"})
    C("API-DID-20", "1001 action 枚举", "action=destroy", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("POST", f"/did/{CTX['dev']['did']}/status", "vpp", {"action": "freeze"})
    C("API-DID-21", "1003 越权", "vpp 冻结 DID", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("POST", f"/did/{CTX['dev']['did']}/rotate-key", "admin", {"reason": "test-轮换", "custody": False})
    d = (b.get("data") or {})
    CTX["dev_v2"] = d
    C("API-DID-22", "契约 2.2 rotate-key / 文档(一)2 密钥轮换", "轮换密钥", "version=2；publicKey 变化；did 不变",
      ok(r, b) + [(d.get("version") == 2, f"version={d.get('version')}"), (d.get("publicKey") != CTX["dev"]["publicKey"], "公钥未变"),
                  (d.get("did") == CTX["dev"]["did"], "did 变了")], {**b, "data": {**d, "privateKey": "***"}}, r.status_code)
    r, b = req("POST", "/did/verify", "admin", {"did": CTX["dev"]["did"], "message": msg, "signature": sig})
    C("API-DID-23", "安全·轮换后旧密钥签名失效", "用 v1 私钥签名验签", "valid=false", ok(r, b) + [(b["data"].get("valid") is False, "旧密钥仍有效")], b, r.status_code)
    rows = db_query("SELECT COUNT(*) n FROM did_key_rotation_log WHERE did=%s", (CTX["dev"]["did"],))
    C("API-DID-24", "文档(一)5 密钥轮换日志表", "查 did_key_rotation_log", "≥1 条", [(rows[0]["n"] >= 1, f"n={rows[0]['n']}")], rows)
    # 注销
    r, b = req("POST", "/did/register", "admin", {"subjectType": "org", "subjectName": uniq("test-org")})
    org = b["data"]["did"]
    r, b = req("POST", f"/did/{org}/status", "admin", {"action": "revoke", "reason": "test-注销"})
    r2, b2 = req("POST", f"/did/{org}/status", "admin", {"action": "unfreeze"})
    C("API-DID-25", "契约 2.2 revoke 注销不可恢复", "注销 org DID 后再 unfreeze", "revoke→revoked；unfreeze 409/1006",
      ok(r, b) + [(b["data"].get("status") == "revoked", "status")] + expect_code(r2, b2, 409, 1006), b2, r2.status_code)
    # 用户 DID 冻结 → 登录/访问被拒 1004 + R02
    r, b = req("POST", "/auth/login", None, {"username": CTX["user_name"], "password": "Test-123456"})
    ut = b["data"]["token"]; udid = b["data"]["user"]["did"]
    CTX["user_did"] = udid
    req("POST", f"/did/{udid}/status", "admin", {"action": "freeze", "reason": "test-R02"})
    r, b = req("GET", "/auth/me", None, raw_token=ut)
    C("API-DID-26", "文档(四)3 R02 异常 DID 登录 / 1004", "冻结测试用户 DID 后用其 token 访问", "403/1004", expect_code(r, b, 403, 1004), b, r.status_code)
    req("POST", f"/did/{udid}/status", "admin", {"action": "unfreeze"})
    time.sleep(0.5)
    r, b = req("GET", "/audit/alerts", "admin", params={"size": 50})
    hit = [a for a in (b.get("data") or {}).get("items", []) if a.get("ruleCode") == "R02_ABNORMAL_DID"]
    C("API-DID-27", "契约 2.7 R02_ABNORMAL_DID 告警", "查 /audit/alerts", "存在 R02_ABNORMAL_DID 告警", [(bool(hit), "无 R02 告警")], hit[:1], r.status_code)


# ============================================================ 2.3 keys
def sec_keys():
    did = CTX["dev"]["did"]
    r, b = req("GET", "/keys", "admin", params={"did": did})
    d = (b.get("data") or {})
    it = d.get("items", [{}])
    C("API-KEY-01", "契约 2.3 GET /keys", f"GET /keys?did={did[:30]}…", "分页；items 字段 id/did/algorithm=SM2/publicKey/status/version/boundAt/expireAt；无私钥",
      ok(r, b) + [page_ok(d), has_keys(it[0] if it else {}, ["id", "did", "algorithm", "publicKey", "status", "version", "boundAt", "expireAt"]),
                  (all(i.get("algorithm") in ("SM2", "ECC", "RSA") for i in it), "algorithm 枚举"), ("privateKey" not in json.dumps(it).replace("privateKeyEnc", ""), "泄露私钥"),
                  iso_ok(it[0].get("boundAt")) if it else (False, "空")], b, r.status_code)
    r, b = req("POST", "/keys", "admin", {"did": did, "algorithm": "SM2", "custody": False})
    kid = (b.get("data") or {}).get("id")
    CTX["key_id"] = kid
    C("API-KEY-02", "契约 2.3 POST /keys", "为 DID 生成新密钥", "200 返回 id/version 递增", ok(r, b) + [(kid is not None, "无 id"), (b["data"].get("version", 0) >= 3, f"version={b['data'].get('version')}")],
      {**b, "data": {k: ("***" if k == "privateKey" else v) for k, v in (b.get("data") or {}).items()}}, r.status_code)
    r, b = req("POST", "/keys", "admin", {"did": did, "algorithm": "DSA"})
    C("API-KEY-03", "2.3 algorithm 枚举 / 1001", "algorithm=DSA", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("POST", "/keys", "admin", {"did": "did:vpp:device:0x" + "0" * 32})
    C("API-KEY-04", "1005", "为不存在 DID 生成密钥", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("POST", f"/keys/{kid}/freeze", "admin", {"reason": "test"})
    C("API-KEY-05", "契约 2.3 freeze", "冻结密钥", "200 status=frozen", ok(r, b) + [(b["data"].get("status") == "frozen", str(b["data"]))], b, r.status_code)
    r, b = req("POST", f"/keys/{kid}/revoke", "admin", {"reason": "test"})
    C("API-KEY-06", "契约 2.3 revoke", "注销密钥", "200 status=revoked", ok(r, b) + [(b["data"].get("status") == "revoked", str(b["data"]))], b, r.status_code)
    r, b = req("POST", f"/keys/{kid}/freeze", "admin", {"reason": "test"})
    C("API-KEY-07", "1006", "冻结已注销密钥", "409/1006", expect_code(r, b, 409, 1006), b, r.status_code)
    r, b = req("GET", f"/keys/{kid}/history", "admin")
    C("API-KEY-08", "契约 2.3 history / 文档(一)5 轮换日志", "密钥历史", "200 列表非空", ok(r, b) + [(len(b["data"].get("keys", []) if isinstance(b["data"], dict) else b["data"]) >= 1, "空"), ("currentVersion" in (b.get("data") or {}), "缺 currentVersion")], {"currentVersion": (b.get("data") or {}).get("currentVersion"), "n": len((b.get("data") or {}).get("keys", []))}, r.status_code)
    r, b = req("GET", "/keys/999999/history", "admin")
    C("API-KEY-09", "1005", "不存在密钥历史", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("POST", f"/keys/{kid}/revoke", "subject", {"reason": "x"})
    C("API-KEY-10", "1003", "subject 注销密钥", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)


# ============================================================ 2.4 assets
def sec_assets():
    did = CTX["dev"]["did"]
    r, b = req("POST", "/assets", "admin", {"name": uniq("test-pv"), "dataType": "pv", "sourceDid": did,
                                           "payload": {"pvOutput": 45.3, "location": {"lat": 39.9, "lng": 116.4}, "interval": "1min"}, "recordCount": 1440})
    d = (b.get("data") or {})
    CTX["asset_id"] = d.get("id")
    CTX["asset_ev"] = d.get("evidenceId")
    C("API-ASSET-01", "契约 2.4 POST /assets 自动分级+SM3+上链 / 文档(二)1", "登记 pv 资产（不填 level）",
      "id/hash=sm3:64hex/level∈L1-L4/chainTxId/evidenceId/authStatus/createdAt",
      ok(r, b) + [has_keys(d, ["id", "hash", "level", "chainTxId", "evidenceId", "authStatus", "createdAt"]),
                  (__import__("re").match(r"^sm3:[0-9a-f]{64}$", d.get("hash", "")) is not None, f"hash={d.get('hash')}"),
                  (d.get("level") in ("L1", "L2", "L3", "L4"), "level"), iso_ok(d.get("createdAt"))], b, r.status_code)
    r, b = req("GET", f"/assets/{CTX['asset_id']}", "admin")
    d = (b.get("data") or {})
    local = gm.payload_hash(d.get("payload", {}))
    C("API-ASSET-02", "契约 2.4 原始 payload 存库、hash=SM3(payload)", "GET /assets/{id} 并本地 SM3 重算",
      "payload 完整；本地 payload_hash == 返回 hash", ok(r, b) + [(d.get("payload", {}).get("pvOutput") == 45.3, "payload 不完整"),
                                                             (local == d.get("hash"), f"本地 {local[:20]} ≠ {d.get('hash', '')[:20]}")], b, r.status_code)
    r, b = req("GET", f"/evidence/{CTX['asset_ev']}", "admin")
    ev = (b.get("data") or {})
    C("API-ASSET-03", "文档(二)1 原始数据不上链仅存哈希", "查资产对应存证 payload", "存证 payload 无 pvOutput/location 原始值",
      ok(r, b) + [("pvOutput" not in json.dumps(ev) and "116.4" not in json.dumps(ev), "原始值上链")], b, r.status_code)
    r, b = req("GET", "/assets", "admin", params={"dataType": "pv", "level": d.get("level"), "keyword": "test-pv", "page": 1, "size": 10})
    dd = (b.get("data") or {})
    C("API-ASSET-04", "契约 2.4 GET /assets 筛选", "dataType/level/keyword 筛选", "分页；items 符合筛选",
      ok(r, b) + [page_ok(dd), (all(i.get("dataType") == "pv" for i in dd.get("items", [])), "dataType 筛选失效"),
                  (any(i.get("id") == CTX["asset_id"] for i in dd.get("items", [])), "keyword 未命中")], b, r.status_code)
    r, b = req("GET", "/assets", "admin", params={"dataType": "oil"})
    C("API-ASSET-05", "1.6 dataType 枚举", "dataType=oil", "400/1001（或空列表）", [(b.get("code") == 1001 or (b.get("code") == 0 and b["data"]["total"] == 0), str(b.get("code")))], b, r.status_code)
    r, b = req("GET", "/assets/99999999", "admin")
    C("API-ASSET-06", "1005", "不存在资产", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("GET", f"/assets/{CTX['asset_id']}/lineage", "admin")
    d = (b.get("data") or {})
    stages = [c.get("stage") for c in d.get("chain", [])]
    C("API-ASSET-07", "契约 2.4 lineage / 文档(二)3 溯源", "GET lineage", "assetId/traceId/chain[stage,at,actorDid,evidenceId]；含 register 与 access",
      ok(r, b) + [has_keys(d, ["assetId", "traceId", "chain"]), ("register" in stages and "access" in stages, f"stages={stages}"),
                  (all(has_keys(c, ["stage", "at", "actorDid", "evidenceId"])[0] for c in d.get("chain", [])), "chain 字段")], b, r.status_code)
    r, b = req("POST", "/assets/classify", "admin", {"records": [{"dataType": "pv", "fields": ["power", "gps"], "freq": "minute", "volume": 1440}]})
    C("API-ASSET-08", "契约 2.4 classify 代理算法", "classify 1 条", "200 results[0].level/score/reason", ok(r, b) + [(bool((b.get("data") or {}).get("results")), "无 results"),
                                                                                                  has_keys(((b.get("data") or {}).get("results") or [{}])[0], ["level", "score", "reason"])], b, r.status_code)
    r, b = req("POST", "/assets/classify", "admin", {"records": []})
    C("API-ASSET-09", "契约 1.1 2001 算法服务错误映射", "classify records=[]（算法服务返回 400）", "400/1001 或 502/2001，不得 500",
      [(r.status_code != 500 and b.get("code") in (1001, 2001), f"HTTP {r.status_code} code {b.get('code')}")], b, r.status_code)
    r, b = req("GET", "/assets/stats", "admin")
    d = (b.get("data") or {})
    C("API-ASSET-10", "契约 2.4 stats", "GET /assets/stats", "byLevel/byType/total/authorized/onChain；total≥81",
      ok(r, b) + [has_keys(d, ["byLevel", "byType", "total", "authorized", "onChain"]), (d.get("total", 0) >= 81, f"total={d.get('total')}")], b, r.status_code)
    # 仅自有
    r, b = req("GET", f"/assets/{CTX['asset_id']}", "subject")
    C("API-ASSET-11", "DB-SCHEMA energy_subject asset:read 仅自有 / 1003", "subject 读 admin 登记的资产", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("GET", "/assets", "subject", params={"size": 200})
    items = (b.get("data") or {}).get("items", [])
    sdid = json.loads(base64.urlsafe_b64decode(token("subject").split(".")[1] + "==")).get("did")
    rows = db_query("SELECT did FROM did_identity WHERE controller_did=%s OR did=%s", (sdid, sdid))
    own = {x["did"] for x in rows} | {sdid}
    foreign = [i for i in items if i.get("sourceDid") not in own and i.get("ownerDid", i.get("sourceDid")) not in own]
    C("API-ASSET-12", "energy_subject 列表仅自有", "subject GET /assets 全量", "items 中无他人资产（sourceDid 不属于 subject 及其控制的 DID）",
      ok(r, b) + [(not foreign, f"含 {len(foreign)} 条他人资产，如 {foreign[0].get('sourceDid') if foreign else ''}")], {"total": (b.get("data") or {}).get("total"), "own": list(own)[:3]}, r.status_code)
    r, b = req("GET", f"/assets/{CTX['asset_id']}/lineage", "subject")
    C("API-ASSET-13", "仅自有·lineage", "subject 查他人资产溯源", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("POST", "/assets", "admin", {"name": "x", "dataType": "pv", "sourceDid": did, "payload": {}, "level": "L9"})
    C("API-ASSET-14", "1001 level 枚举", "level=L9", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("POST", "/assets", "regulator", {"name": "x", "dataType": "pv", "sourceDid": did, "payload": {"a": 1}})
    C("API-ASSET-15", "矩阵 regulator 无 asset:write / 1003", "regulator 登记资产", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)


# ============================================================ 2.5 permissions
def sec_perm():
    r, b = req("GET", "/roles", "admin")
    roles = b.get("data") if isinstance(b.get("data"), list) else (b.get("data") or {}).get("items", [])
    codes = {x.get("code") for x in roles}
    C("API-PERM-01", "契约 2.5 GET /roles / 1.6 六角色", "角色列表", "含 sys_admin/grid_dispatcher/vpp_operator/energy_subject/regulator/edge_node",
      ok(r, b) + [({"sys_admin", "grid_dispatcher", "vpp_operator", "energy_subject", "regulator", "edge_node"} <= codes, f"codes={codes}")], b, r.status_code)
    code = uniq("test_role").replace("-", "_")
    r, b = req("POST", "/roles", "admin", {"code": code, "name": "测试自定义角色", "grants": [{"resourceType": "asset", "action": "read", "scope": "all"}]})
    CTX["role"] = code
    C("API-PERM-02", "契约 2.5 POST /roles / 文档(三)1 自定义角色", "新建自定义角色", "200", ok(r, b), b, r.status_code)
    r, b = req("POST", "/roles", "admin", {"code": code, "name": "重复"})
    C("API-PERM-03", "1006", "重复角色 code", "409/1006", expect_code(r, b, 409, 1006), b, r.status_code)
    r, b = req("PUT", f"/roles/{code}", "admin", {"grants": [{"resourceType": "asset", "action": "read"}, {"resourceType": "evidence", "action": "read"}]})
    C("API-PERM-04", "契约 2.5 PUT /roles/{code}", "修改角色权限", "200", ok(r, b), b, r.status_code)
    r, b = req("PUT", f"/roles/{code}", "admin", {"grants": [{"resourceType": "asset", "action": "fly"}]})
    C("API-PERM-05", "1.6 action 枚举 / 1001", "action=fly", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("PUT", "/roles/no_such_role_x", "admin", {"name": "x"})
    C("API-PERM-06", "1005", "修改不存在角色", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("POST", "/roles", "vpp", {"code": "test_x", "name": "x"})
    C("API-PERM-07", "1003", "vpp 新建角色", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("GET", "/permissions/matrix", "admin")
    d = (b.get("data") or {})
    sa = next((x for x in d.get("roles", []) if x.get("code") == "sys_admin"), {})
    C("API-PERM-08", "契约 2.5 matrix", "权限矩阵", "resources 5 项/actions 5 项/roles≥6；sys_admin.grants.dispatch 含 issue",
      ok(r, b) + [(d.get("resources") == ["asset", "model", "dispatch", "evidence", "algo"], f"resources={d.get('resources')}"),
                  (d.get("actions") == ["read", "write", "execute", "issue", "export"], f"actions={d.get('actions')}"),
                  ("issue" in sa.get("grants", {}).get("dispatch", []), "sys_admin dispatch 无 issue")], {"resources": d.get("resources"), "actions": d.get("actions"), "roles": len(d.get("roles", []))}, r.status_code)
    r, b = req("POST", "/permissions/check", "vpp", {"resourceType": "dispatch", "action": "issue"})
    d = (b.get("data") or {})
    C("API-PERM-09", "契约 2.5 check / 文档(三)3 统一鉴权接口", "vpp check dispatch:issue", "allowed=false reason 含 vpp_operator；含 matchedRule/level",
      ok(r, b) + [(d.get("allowed") is False, "allowed"), ("vpp_operator" in str(d.get("reason")), "reason"), has_keys(d, ["allowed", "reason", "matchedRule", "level"])], b, r.status_code)
    r, b = req("POST", "/permissions/check", "admin", {"resourceType": "dispatch", "action": "issue"})
    C("API-PERM-10", "check 正向", "admin check dispatch:issue", "allowed=true matchedRule 非空", ok(r, b) + [(b["data"].get("allowed") is True and b["data"].get("matchedRule"), str(b["data"]))], b, r.status_code)
    r, b = req("POST", "/permissions/check", "admin", {"resourceType": "planet", "action": "read"})
    C("API-PERM-11", "1001 resourceType 枚举", "resourceType=planet", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    # 申请→审批→生效→回收
    aid = CTX["asset_id"]
    r, b = req("POST", "/permissions/apply", "subject", {"resourceType": "asset", "resourceId": str(aid), "action": "read", "reason": "test-联合建模需要", "expireAt": "2026-12-31T00:00:00+08:00"})
    d = (b.get("data") or {})
    CTX["app_id"] = d.get("id")
    C("API-PERM-12", "契约 2.5 apply / 文档(三)4 授权流转+上链", "subject 申请 asset read", "id/status=pending/applicantDid/createdAt/evidenceId",
      ok(r, b) + [has_keys(d, ["id", "status", "applicantDid", "createdAt", "evidenceId"]), (d.get("status") == "pending", "status"), iso_ok(d.get("createdAt"))], b, r.status_code)
    r, b = req("POST", "/permissions/apply", "subject", {"resourceType": "asset", "resourceId": str(aid), "action": "write", "reason": "x", "expireAt": "2020-01-01"})
    C("API-PERM-13", "1001 expireAt 非法/过去", "expireAt=2020-01-01", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("GET", "/permissions/applications", "admin", params={"status": "pending", "page": 1, "size": 50})
    d = (b.get("data") or {})
    C("API-PERM-14", "契约 2.5 applications 筛选", "status=pending", "分页；全部 pending；含刚提交的",
      ok(r, b) + [page_ok(d), (all(i.get("status") == "pending" for i in d.get("items", [])), "筛选"), (any(i.get("id") == CTX["app_id"] for i in d.get("items", [])), "未命中")], b, r.status_code)
    r, b = req("POST", f"/permissions/applications/{CTX['app_id']}/approve", "vpp", {"reason": "x"})
    C("API-PERM-15", "1003 越权审批", "vpp 审批", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("GET", f"/assets/{aid}", "subject")
    pre = r.status_code
    r, b = req("POST", f"/permissions/applications/{CTX['app_id']}/approve", "admin", {"reason": "test-符合最小必要"})
    d = (b.get("data") or {})
    CTX["grant_id"] = d.get("grantId")
    r2, b2 = req("GET", f"/assets/{aid}", "subject")
    C("API-PERM-16", "契约 2.5 approve → 权限生效", "审批前 subject 读资产→approve→审批后再读", "审批前 403；approve status=approved 带 grantId；审批后 200",
      ok(r, b) + [(pre == 403, f"审批前 HTTP {pre}"), (d.get("status") == "approved" and d.get("grantId"), str(d))] + ok(r2, b2), b2, r2.status_code)
    r, b = req("POST", f"/permissions/applications/{CTX['app_id']}/approve", "admin", {"reason": "again"})
    C("API-PERM-17", "1006 状态不允许", "重复审批", "409/1006", expect_code(r, b, 409, 1006), b, r.status_code)
    r, b = req("POST", "/permissions/applications/99999999/reject", "admin", {"reason": "x"})
    C("API-PERM-18", "1005", "驳回不存在申请", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("GET", "/permissions/grants", "admin", params={"did": json.loads(base64.urlsafe_b64decode(token("subject").split(".")[1] + "==")).get("did")})
    d = (b.get("data") or {})
    C("API-PERM-19", "契约 2.5 grants?did", "按 did 查已授权", "含刚生效的 grantId", ok(r, b) + [(any(i.get("id") == CTX["grant_id"] for i in d.get("items", [])), "未命中")], b, r.status_code)
    r, b = req("POST", f"/permissions/grants/{CTX['grant_id']}/revoke", "admin", {"reason": "test-业务结束"})
    r2, b2 = req("GET", f"/assets/{aid}", "subject")
    C("API-PERM-20", "契约 2.5 revoke 回收即时失效 / 文档(三)5", "回收授权后 subject 再读", "revoke 200；之后 403/1003", ok(r, b) + expect_code(r2, b2, 403, 1003), b2, r2.status_code)
    r, b = req("POST", f"/permissions/grants/{CTX['grant_id']}/revoke", "admin", {"reason": "again"})
    C("API-PERM-21", "1006", "重复回收", "409/1006", expect_code(r, b, 409, 1006), b, r.status_code)
    r, b = req("POST", "/permissions/apply", "subject", {"resourceType": "asset", "resourceId": str(aid), "action": "read", "reason": "test-驳回"})
    app2 = b["data"]["id"]
    r, b = req("POST", f"/permissions/applications/{app2}/reject", "admin", {"reason": "test-不符合"})
    C("API-PERM-22", "契约 2.5 reject", "驳回", "status=rejected", ok(r, b) + [(b["data"].get("status") == "rejected", str(b["data"]))], b, r.status_code)
    rows = db_query("SELECT COUNT(*) n FROM perm_change_log WHERE created_at > NOW() - INTERVAL 10 MINUTE")
    C("API-PERM-23", "文档(三)5 权限变更留痕 perm_change_log", "查最近 10 分钟 perm_change_log", "≥3 条（approve/revoke/reject）", [(rows[0]["n"] >= 3, f"n={rows[0]['n']}")], rows)
    rows = db_query("SELECT evidence_id FROM chain_evidence WHERE category='permission' ORDER BY id DESC LIMIT 3")
    C("API-PERM-24", "文档(三)4 授权记录同步上链", "查 chain_evidence category=permission", "最近存在 permission 类存证", [(len(rows) >= 1, "无")], rows)


# ============================================================ 2.6 evidence
def sec_evidence():
    # 环境整备：其他用例/E2E 的 tamper 演示可能没有还原，先一键还原
    # （B-017 修复后 chain/status.brokenAt 是区块高度 int，不能再拿它当 evidenceId 去 restore）
    fixed = []
    rr, bb = req("GET", "/evidence/chain/status", "admin")
    if not (bb.get("data") or {}).get("intact"):
        rr2, bb2 = req("POST", "/evidence/demo/restore-all", "admin", {})
        fixed = ((bb2.get("data") or {}).get("restored") or [])
        if not fixed:  # 老后端没有 restore-all 时退回逐条还原
            for _ in range(10):
                rr, bb = req("GET", "/evidence/chain/status", "admin")
                if (bb.get("data") or {}).get("intact"):
                    break
                eid = bb["data"].get("brokenAtEvidenceId") or bb["data"].get("brokenAt")
                rr3, bb3 = req("POST", "/evidence/demo/restore", "admin", {"evidenceId": eid})
                fixed.append((eid, bb3.get("code")))
                if bb3.get("code") != 0:
                    break
    if fixed:
        print("  [env] 还原遗留的篡改存证：", fixed)
    CTX["env_restored"] = fixed
    r, b = req("POST", "/evidence", "admin", {"category": "data", "refId": "test-ref-1", "payload": {"k": "v", "n": 1, "中文": "值"}, "actorDid": CTX["admin_did"]})
    d = (b.get("data") or {})
    CTX["ev_id"] = d.get("evidenceId")
    local = gm.payload_hash({"k": "v", "n": 1, "中文": "值"})
    C("API-EV-01", "契约 2.6 POST /evidence / 文档(二)1 SM3 摘要上链", "写入 data 存证", "evidenceId/hash/blockHeight/txId/prevHash/timestamp；hash==本地 SM3(canonical payload)",
      ok(r, b) + [has_keys(d, ["evidenceId", "hash", "blockHeight", "txId", "prevHash", "timestamp"]), (d.get("hash") == local, f"hash {d.get('hash', '')[:20]}≠本地 {local[:20]}"), iso_ok(d.get("timestamp"))], b, r.status_code)
    r, b = req("POST", "/evidence", "admin", {"category": "video", "refId": "x", "payload": {}})
    C("API-EV-02", "1.6 category 枚举 / 1001", "category=video", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("GET", "/evidence", "admin", params={"category": "data", "from": "2026-08-01T00:00:00+08:00", "to": "2027-01-01T00:00:00+08:00", "page": 1, "size": 20})
    d = (b.get("data") or {})
    C("API-EV-03", "契约 2.6 GET /evidence 多维检索", "category+时间范围", "分页；items 全为 data", ok(r, b) + [page_ok(d), (all(i.get("category") == "data" for i in d.get("items", [])), "筛选")], b, r.status_code)
    r, b = req("GET", "/evidence", "admin", params={"from": "not-a-date"})
    C("API-EV-04", "1001", "from 非法时间", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("GET", f"/evidence/{CTX['ev_id']}", "admin")
    d = (b.get("data") or {})
    C("API-EV-05", "契约 2.6 GET /evidence/{id}", "存证详情", "evidenceId/category/refId/hash/blockHeight/prevHash/txId/timestamp 或 createdAt",
      ok(r, b) + [has_keys(d, ["evidenceId", "category", "refId"]), (("hash" in d or "payloadHash" in d), "缺 hash")], b, r.status_code)
    r, b = req("GET", "/evidence/ev-999999", "admin")
    C("API-EV-06", "1005", "不存在存证", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("POST", "/evidence/verify", "admin", {"evidenceId": CTX["ev_id"]})
    d = (b.get("data") or {})
    C("API-EV-07", "契约 2.6 verify（库内重算）/ 文档(二)4", "verify 不带 payload", "intact=true localHash==chainHash", ok(r, b) + [(d.get("intact") is True and d.get("localHash") == d.get("chainHash"), str(d))], b, r.status_code)
    r, b = req("POST", "/evidence/verify", "admin", {"evidenceId": CTX["ev_id"], "payload": {"k": "v", "n": 2, "中文": "值"}})
    d = (b.get("data") or {})
    C("API-EV-08", "文档(二)4 本地数据与链上比对", "verify 带被改 payload", "intact=false message 提示篡改", ok(r, b) + [(d.get("intact") is False and d.get("localHash") != d.get("chainHash"), str(d)), has_keys(d, ["intact", "localHash", "chainHash", "message"])], b, r.status_code)
    r, b = req("GET", "/evidence/chain/status", "admin")
    d = (b.get("data") or {})
    CTX["chain_before"] = d
    C("API-EV-09", "契约 2.6 chain/status", "链状态", "height/lastHash/intact=true/brokenAt=null/totalRecords/byCategory(5 类)",
      ok(r, b) + [has_keys(d, ["height", "lastHash", "intact", "brokenAt", "totalRecords", "byCategory"]), (d.get("intact") is True and d.get("brokenAt") is None, f"intact={d.get('intact')} brokenAt={d.get('brokenAt')}"),
                  (set(d.get("byCategory", {}).keys()) == {"data", "identity", "permission", "audit", "algo"}, f"byCategory={list(d.get('byCategory', {}).keys())}")], b, r.status_code)
    tid = f"tr-20260822-{uniq('')[-8:]}"
    r, b = req("POST", "/evidence", "admin", {"category": "data", "refId": "test-trace", "payload": {"t": 1}}, trace=tid)
    r, b = req("GET", f"/evidence/trace/{tid}", "admin")
    C("API-EV-10", "契约 2.6 trace/{traceId} / 1.2 存证共用 traceId", "带 X-Trace-Id 写存证后按 traceId 查", "200 且含该存证", ok(r, b) + [("test-trace" in json.dumps(b) or tid in json.dumps(b), "未查到")], b, r.status_code)
    # tamper 三连
    ev = CTX["ev_id"]
    r, b = req("POST", "/evidence/demo/tamper", "vpp", {"evidenceId": ev, "newValue": {"x": 1}})
    C("API-EV-11", "契约 2.6 tamper 仅 sys_admin / 1003", "vpp 调 tamper", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("POST", "/evidence/demo/tamper", "admin", {"evidenceId": ev, "newValue": {"k": "hacked"}})
    d = (b.get("data") or {})
    r2, b2 = req("POST", "/evidence/verify", "admin", {"evidenceId": ev})
    r3, b3 = req("GET", "/evidence/chain/status", "admin")
    C("API-EV-12", "契约 2.6 tamper→verify→chain/status 断裂点 / 验收 9", "admin 篡改→校验→链状态", "tampered=true；verify intact=false；chain intact=false 且 brokenAtEvidenceId==evidenceId（B-017 后 brokenAt 是区块高度 int）",
      ok(r, b) + [(d.get("tampered") is True, "tampered"), (b2["data"].get("intact") is False, "verify 仍 intact"),
                  (b3["data"].get("intact") is False, "chain 应为 intact=false"),
                  ((b3["data"].get("brokenAtEvidenceId") or b3["data"].get("brokenAt")) == ev, f"brokenAtEvidenceId={b3['data'].get('brokenAtEvidenceId')} brokenAt={b3['data'].get('brokenAt')}"),
                  (isinstance(b3["data"].get("brokenAt"), int) or b3["data"].get("brokenAt") is None, "brokenAt 应为区块高度 int")], b3, r3.status_code)
    r, b = req("POST", "/evidence/demo/restore", "admin", {"evidenceId": ev})
    r3, b3 = req("GET", "/evidence/chain/status", "admin")
    C("API-EV-13", "演示还原 restore（契约外，验证清单 4.19）", "restore 后链状态", "restored=true；链 intact=true", ok(r, b) + [(b["data"].get("restored") is True, "restored"), (b3["data"].get("intact") is True, "链未恢复")], b3, r3.status_code)
    r, b = req("POST", "/evidence/demo/tamper", "admin", {"evidenceId": "ev-999999", "newValue": {}})
    C("API-EV-14", "1005", "篡改不存在存证", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("GET", f"/evidence/{ev}/certificate", "admin")
    d = (b.get("data") or {})
    C("API-EV-15", "契约 2.6 certificate / 文档(二)3 导出凭证", "导出凭证", "含 chainProof 与 signature（SM2）", ok(r, b) + [has_keys(d, ["chainProof", "signature"])], {k: (str(v)[:80]) for k, v in d.items()} if isinstance(d, dict) else b, r.status_code)
    # 凭证签名离线验证
    sig = d.get("signature") if isinstance(d, dict) else None
    blocked = None
    checks = []
    if isinstance(sig, dict) and sig.get("signerDid"):
        body_wo_sig = {k: v for k, v in d.items() if k != "signature"}
        rr, bb = req("POST", "/did/verify", "admin", {"did": sig.get("signerDid"), "message": gm.canonical_json(body_wo_sig), "signature": sig.get("value")})
        checks = ok(rr, bb) + [((bb.get("data") or {}).get("valid") is True, str(bb.get("data")))]
    else:
        blocked = f"凭证 signature 结构为 {type(sig).__name__}，无 signerDid/message 字段可离线验签"
    C("API-EV-16", "凭证 SM2 签名可核验", "message=canonical_json(凭证去掉 signature) 用 signerDid 调 /did/verify", "valid=true", checks, sig, blocked=blocked)
    r, b = req("GET", "/evidence", "subject", params={"size": 200})
    items = (b.get("data") or {}).get("items", [])
    sdid = json.loads(base64.urlsafe_b64decode(token("subject").split(".")[1] + "==")).get("did")
    foreign = [i for i in items if i.get("actorDid") not in (sdid, None) and sdid not in json.dumps(i)]
    C("API-EV-17", "DB-SCHEMA energy_subject evidence:read 仅自有", "subject GET /evidence", "只返回 actorDid 为自己的存证",
      ok(r, b) + [(not foreign, f"含 {len(foreign)} 条他人存证 actorDid={foreign[0].get('actorDid') if foreign else ''}")], {"total": (b.get("data") or {}).get("total")}, r.status_code)
    r, b = req("GET", f"/evidence/{CTX['ev_id']}", "subject")
    C("API-EV-17b", "energy_subject evidence:read 仅自有（详情）", "subject 读 admin 写的存证详情", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("GET", "/evidence/chain/status", "edge")
    C("API-EV-18", "矩阵 edge_node 无 evidence:read / 1003", "edge 查链状态", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)


# ============================================================ 2.7 audit
def sec_audit():
    tid = f"tr-20260822-{uniq('')[-8:]}"
    req("GET", "/users", "vpp", trace=tid)  # require_roles 路径的拒绝
    tid2 = f"tr-20260822-{uniq('')[-8:]}"
    req("POST", "/evidence", "vpp", {"category": "data", "refId": "test-deny", "payload": {"x": 1}}, trace=tid2)  # @require_permission 路径的拒绝
    time.sleep(0.5)
    r, b = req("GET", "/audit/logs", "admin", params={"traceId": tid})
    d = (b.get("data") or {})
    C("API-AUD-01", "契约 2.7 / 文档(四)1 全链路埋点：所有被拒请求写审计 / 验证清单 4.22", "vpp GET /users（require_roles 拒绝 1003）后按 traceId 查日志",
      "命中 1 条 result=denied riskLevel=high", ok(r, b) + [page_ok(d), (d.get("total", 0) >= 1, "未命中：require_roles 路径的 1003 未写审计日志（仅计入 R01）")], b, r.status_code)
    r, b = req("GET", "/audit/logs", "admin", params={"traceId": tid2})
    d = (b.get("data") or {})
    it = d.get("items") or [{}]
    C("API-AUD-01b", "契约 2.7 logs?traceId 字段逐字对照 / 1.2 审计共用 traceId", "vpp POST /evidence（require_permission 拒绝）后按 traceId 查日志",
      "命中 1 条；字段 id/traceId/actorDid/actorName/action/resourceType/resourceId/result=denied/riskLevel=high/module/detail/ip/at/evidenceId/hash；actorName 为中文姓名；高危已上链",
      ok(r, b) + [page_ok(d), (d.get("total", 0) >= 1, "未命中"), has_keys(it[0], ["id", "traceId", "actorDid", "actorName", "action", "resourceType", "resourceId", "result", "riskLevel", "module", "detail", "ip", "at", "evidenceId", "hash"]),
                  (it[0].get("result") == "denied" and it[0].get("riskLevel") == "high", f"result={it[0].get('result')} risk={it[0].get('riskLevel')}"),
                  (it[0].get("actorName") == "虚拟电厂运营商", f"actorName={it[0].get('actorName')}"), (bool(it[0].get("evidenceId")), "高危未上链"), iso_ok(it[0].get("at"))], b, r.status_code)
    tid = tid2
    r, b = req("GET", "/audit/logs", "admin", params={"riskLevel": "high", "result": "denied", "page": 1, "size": 10})
    d = (b.get("data") or {})
    C("API-AUD-02", "契约 2.7 logs 组合筛选", "riskLevel=high&result=denied", "全部 high/denied", ok(r, b) + [(all(i.get("riskLevel") == "high" and i.get("result") == "denied" for i in d.get("items", [])), "筛选失效"), (d.get("total", 0) >= 1, "空")], b, r.status_code)
    r, b = req("GET", "/audit/logs", "admin", params={"riskLevel": "ultra"})
    C("API-AUD-03", "1001 riskLevel 枚举", "riskLevel=ultra", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("GET", "/audit/logs", "admin", params={"keyword": "' OR 1=1 -- ", "size": 5})
    C("API-AUD-04", "安全·SQL 注入（keyword）", "keyword=' OR 1=1 --", "200 正常结果或 400，不 500", [(r.status_code in (200, 400), f"HTTP {r.status_code}")], b, r.status_code)
    r, b = req("GET", "/audit/logs", "admin", params={"from": "2026-07-01T00:00:00+08:00", "to": "2026-08-31T23:59:59+08:00", "size": 5})
    d = (b.get("data") or {})
    rows = db_query("SELECT (SELECT COUNT(*) FROM audit_log_202607) a, (SELECT COUNT(*) FROM audit_log_202608) b")
    C("API-AUD-05", "DB-SCHEMA 按月分表 + 跨月 UNION ALL 查询", "from=2026-07-01 to=2026-08-31 查询", "total ≥ audit_log_202607 行数，并 ≤ 07+08 两表之和",
      ok(r, b) + [(rows[0]["a"] <= d.get("total", 0) <= rows[0]["a"] + rows[0]["b"], f"total={d.get('total')} 07={rows[0]['a']} 08={rows[0]['b']}")], {"total": d.get("total"), "db": rows[0]}, r.status_code)
    r, b = req("GET", f"/audit/trace/{tid}", "admin")
    d = (b.get("data") or {})
    C("API-AUD-06", "契约 2.7 trace/{traceId} 任务级追踪 / 验收 10", "按 traceId 查链路", "traceId/summary{startAt,endAt,durationMs,actorDid,result,riskLevel}/steps[seq,module,action,at,result]",
      ok(r, b) + [has_keys(d, ["traceId", "summary", "steps"]), has_keys(d.get("summary"), ["startAt", "endAt", "durationMs", "actorDid", "result", "riskLevel"]),
                  (len(d.get("steps", [])) >= 1 and has_keys(d["steps"][0], ["seq", "module", "action", "at", "result"])[0], "steps")], b, r.status_code)
    r, b = req("GET", "/audit/trace/tr-00000000-00000000", "admin")
    C("API-AUD-07", "1005", "不存在 traceId", "404/1005（或空 steps）", [(b.get("code") in (1005, 0), str(b.get("code")))], b, r.status_code)
    # R01：再越权两次凑满 3 次
    for _ in range(3):
        req("GET", "/users", "vpp")
    time.sleep(0.5)
    r, b = req("GET", "/audit/alerts", "admin", params={"status": "open", "size": 50})
    d = (b.get("data") or {})
    a = [x for x in d.get("items", []) if x.get("ruleCode") == "R01_UNAUTHORIZED"]
    CTX["alert_id"] = a[0].get("id") if a else None
    C("API-AUD-08", "契约 2.7 R01_UNAUTHORIZED ≥3 次/5 分钟 / 文档(四)3", "vpp 连续越权 ≥3 次后查 alerts?status=open", "出现 R01_UNAUTHORIZED riskLevel=high 含 evidenceId；字段 id/ruleCode/riskLevel/message/actorDid/status",
      ok(r, b) + [(bool(a), "无 R01"), (bool(a) and a[0].get("riskLevel") == "high" and a[0].get("evidenceId"), str(a[:1])), has_keys(a[0] if a else {}, ["id", "ruleCode", "riskLevel", "message", "actorDid", "status"])], a[:1], r.status_code)
    r, b = req("GET", "/audit/alerts", "admin", params={"status": "weird"})
    C("API-AUD-09", "1001 status 枚举", "status=weird", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    if CTX["alert_id"]:
        r, b = req("POST", f"/audit/alerts/{CTX['alert_id']}/ack", "admin")
        r2, b2 = req("POST", f"/audit/alerts/{CTX['alert_id']}/ack", "admin")
        C("API-AUD-10", "契约 2.7 ack", "确认告警；再确认一次", "首次 status=acked；重复 409/1006", ok(r, b) + [(b["data"].get("status") == "acked", str(b["data"]))] + expect_code(r2, b2, 409, 1006), b2, r2.status_code)
    else:
        C("API-AUD-10", "契约 2.7 ack", "确认告警", "status=acked", [], blocked="无 open 告警可确认")
    r, b = req("POST", "/audit/alerts/99999999/ack", "admin")
    C("API-AUD-11", "1005", "确认不存在告警", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("POST", f"/audit/alerts/{CTX['alert_id'] or 1}/ack", "vpp")
    C("API-AUD-12", "1003", "vpp 确认告警", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("GET", "/audit/report", "admin", params={"period": "day", "date": dt.date.today().isoformat()})
    d = (b.get("data") or {})
    C("API-AUD-13", "契约 2.7 report / 文档(四)4 审计报告+DeepSeek", "日报", "period/date/identityOps{register,freeze,revoke,rotate}/permissionOps{applied,approved,rejected,revoked}/evidence/riskEvents/narrative/narrativeSource∈live|cache|rule",
      ok(r, b) + [has_keys(d, ["period", "date", "identityOps", "permissionOps", "evidence", "riskEvents", "narrative", "narrativeSource"]),
                  has_keys(d.get("identityOps"), ["register", "freeze", "revoke", "rotate"]), has_keys(d.get("permissionOps"), ["applied", "approved", "rejected", "revoked"]),
                  (d.get("narrativeSource") in ("live", "cache", "rule"), f"source={d.get('narrativeSource')}"), (len(d.get("narrative") or "") > 10, "narrative 空")], {**b, "data": {**d, "narrative": str(d.get("narrative"))[:60]}}, r.status_code)
    r, b = req("GET", "/audit/report", "admin", params={"period": "year"})
    C("API-AUD-14", "1001 period 枚举", "period=year", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("GET", "/audit/stats", "admin")
    d = (b.get("data") or {})
    C("API-AUD-15", "契约 2.7 stats", "看板统计", "todayLogs/highRiskLogs/openAlerts/onChainLogs/byModule/byRisk/trend(7 天)",
      ok(r, b) + [has_keys(d, ["todayLogs", "highRiskLogs", "openAlerts", "onChainLogs", "byModule", "byRisk", "trend"]), (len(d.get("trend", [])) == 7, f"trend={len(d.get('trend', []))}")], {**b, "data": {k: (v if not isinstance(v, list) else f"[{len(v)}]") for k, v in d.items()}}, r.status_code)
    r, b = req("GET", "/audit/rules", "admin")
    codes = [x.get("ruleCode") or x.get("code") for x in (b.get("data") if isinstance(b.get("data"), list) else (b.get("data") or {}).get("items", []))]
    C("API-AUD-16", "文档(四)3 五类风险规则", "GET /audit/rules（契约外）", "R01~R05 五条", ok(r, b) + [(len(codes) == 5 and all(c.startswith("R0") for c in codes), str(codes))], codes, r.status_code)
    r = client.get("/audit/logs/export", headers=hdr("admin"), params={"riskLevel": "high"})
    ct = r.headers.get("content-type", "")
    C("API-AUD-17", "契约 2.7 export CSV 文件流非 JSON 包装 / 文档(四)2 导出", "导出 high 日志", "content-type text/csv；X-Total-Rows；UTF-8 BOM 中文表头",
      [("text/csv" in ct, f"ct={ct}"), (r.headers.get("x-total-rows") is not None, "无 X-Total-Rows"), (r.content.startswith(b"\xef\xbb\xbf"), "无 BOM"), ("trace" in r.text[:300].lower() or "追踪" in r.text[:300], "表头")], r.text[:120], r.status_code)
    r, b = req("GET", "/audit/logs/export", "vpp")
    C("API-AUD-18", "1003 导出需审计角色", "vpp 导出", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("GET", "/audit/logs", "subject", params={"size": 5})
    C("API-AUD-19", "1003 审计仅 sys_admin/regulator", "subject 查审计日志", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("GET", "/audit/logs", "regulator", params={"size": 5})
    C("API-AUD-20", "regulator 可查审计", "regulator 查审计日志", "200", ok(r, b), {"total": (b.get("data") or {}).get("total")}, r.status_code)
    # 日志 hash 防篡改 + 高危上链
    rows = db_query("SELECT id, hash, evidence_id, risk_level FROM audit_log_202608 WHERE risk_level IN ('high','critical') ORDER BY id DESC LIMIT 5")
    C("API-AUD-21", "文档(四)5 日志防篡改·高危日志摘要上链", "查 audit_log_202608 高危日志", "hash 非空且 evidence_id 非空", [(bool(rows) and all(x["hash"] and x["evidence_id"] for x in rows), str(rows[:1]))], rows[:2])
    # R04 批量导出：10 分钟内导出 ≥3 次
    for _ in range(3):
        client.get("/audit/logs/export", headers=hdr("admin"), params={"riskLevel": "low"})
    time.sleep(0.5)
    r, b = req("GET", "/audit/alerts", "admin", params={"size": 50})
    a = [x for x in (b.get("data") or {}).get("items", []) if x.get("ruleCode") == "R04_BULK_EXPORT"]
    C("API-AUD-22", "契约 2.7 R04_BULK_EXPORT", "10 分钟内导出 ≥3 次", "出现 R04_BULK_EXPORT 告警", [(bool(a), "无 R04")], a[:1], r.status_code)
    # R03 高频权限变更：同一主体 ≥5 次/10 分钟
    # 用一个本次新建的 energy_subject 账号当申请方：R03 去重窗口 10 分钟，演示账号 subject
    # 很可能刚在上一轮/上一个脚本里告过警，再触发不会有新告警（设计内）；新主体没有历史，
    # 断言也改成「必须出现本次触发后新产生、且归属该主体的告警」——历史行不再能让用例通过
    # （B-028 正是靠历史行被掩盖了两天的缺陷）。
    churn_user = uniq("test-churn")
    req("POST", "/users", "admin", {"username": churn_user, "password": "Churn-123456", "realName": "R03 触发主体",
                                    "orgName": "测试机构", "roles": ["energy_subject"], "bindDid": True})
    rr, bb = req("POST", "/auth/login", None, {"username": churn_user, "password": "Churn-123456"})
    churn_token = (bb.get("data") or {}).get("token")
    churn_did = (bb.get("data") or {}).get("user", {}).get("did") or \
        next((u.get("did") for u in (req("GET", "/users", "admin", params={"keyword": churn_user, "size": 5})[1].get("data") or {}).get("items", []) if u.get("username") == churn_user), None)
    aid = CTX["asset_id"]
    t_before = time.time()
    reject_ms = []
    for i in range(5):
        rr, bb = req("POST", "/permissions/apply", None, {"resourceType": "asset", "resourceId": str(aid), "action": "read", "reason": f"test-churn-{i}"}, raw_token=churn_token)
        app = (bb.get("data") or {}).get("id")
        if app:
            t0 = time.time()
            req("POST", f"/permissions/applications/{app}/reject", "admin", {"reason": "test-churn"})
            reject_ms.append(round((time.time() - t0) * 1000))
    time.sleep(0.5)
    r, b = req("GET", "/audit/alerts", "admin", params={"size": 50, "ruleCode": "R03_PERM_CHURN"})
    a = [x for x in (b.get("data") or {}).get("items", [])
         if x.get("ruleCode") == "R03_PERM_CHURN" and x.get("actorDid") == churn_did
         and _iso_ts(x.get("createdAt")) >= t_before - 2]
    C("API-AUD-23", "契约 2.7 R03_PERM_CHURN / B-028", "新建主体 5 次申请+驳回（5 次权限变更）",
      "出现归属该主体、createdAt 晚于触发时刻的 R03 告警，evidenceId 非空；每次驳回 <2s（B-028 修复前第 5 次约 9s）",
      [(bool(churn_did), "拿不到申请方 DID"), (bool(a), f"无本次新产生的 R03（actorDid={churn_did}）"),
       (bool(a) and bool(a[0].get("evidenceId")), "告警未上链"),
       (bool(reject_ms) and max(reject_ms) < 2000, f"驳回耗时 ms={reject_ms}")],
      {"alert": a[:1], "rejectMs": reject_ms}, r.status_code)



def _iso_ts(v) -> float:
    """ISO8601（含 +08:00）→ 时间戳；解析失败返回 0。"""
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0

# ============================================================ 2.8 nodes
def sec_nodes():
    r, b = req("GET", "/nodes", "admin")
    d = (b.get("data") or {})
    na = next((n for n in d.get("items", []) if n.get("id") == "Node-A"), {})
    nc = next((n for n in d.get("items", []) if n.get("id") == "Node-C"), {})
    C("API-NODE-01", "契约 2.8 GET /nodes / DB-SCHEMA 种子节点", "节点列表", "4 节点；字段 id/name/status/model/did/didStatus/metrics{pvOutput,storageOutput,load,soc}/lastSeenAt；指标为合理数值（B-021 修复后 node_status 每 5 秒真实更新，不再是种子固定值）；Node-C warning",
      ok(r, b) + [(d.get("total") == 4, f"total={d.get('total')}"), has_keys(na, ["id", "name", "status", "model", "did", "didStatus", "metrics", "lastSeenAt"]),
                  (set(na.get("metrics", {}).keys()) == {"pvOutput", "storageOutput", "load", "soc"}, f"metrics keys={list(na.get('metrics', {}).keys())}"),
                  (all(isinstance(na.get("metrics", {}).get(k), (int, float)) for k in ("pvOutput", "storageOutput", "load", "soc"))
                   and 0 <= na["metrics"]["soc"] <= 100 and na["metrics"]["pvOutput"] >= 0,
                   f"Node-A metrics={na.get('metrics')}"), (nc.get("status") == "warning", "Node-C 状态"), iso_ok(na.get("lastSeenAt"))], na, r.status_code)
    r, b = req("GET", "/nodes/Node-A", "admin")
    C("API-NODE-02", "契约 2.8 GET /nodes/{id}", "节点详情", "200 id=Node-A", ok(r, b) + [(b["data"].get("id") == "Node-A", "id")], b, r.status_code)
    r, b = req("GET", "/nodes/Node-Z", "admin")
    C("API-NODE-03", "1005", "不存在节点", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("GET", "/nodes/Node-A/metrics", "admin", params={"interval": "hour"})
    d = (b.get("data") or {})
    C("API-NODE-04", "契约 2.8 metrics 历史 / 种子 30 天", "interval=hour（默认窗口）", "total ≥100 点；truncated=false；点含 ts 与四指标",
      ok(r, b) + [(d.get("total", 0) >= 100, f"total={d.get('total')}"), (d.get("truncated") is False, "truncated")], {"total": d.get("total"), "first": (d.get("items") or d.get("points") or [None])[0]}, r.status_code)
    r, b = req("GET", "/nodes/Node-A/metrics", "admin", params={"interval": "year"})
    C("API-NODE-05", "1001 interval 枚举", "interval=year", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    # 设备上线：伪造签名
    dev = CTX["dev"]; v2 = CTX["dev_v2"]
    r, b = req("POST", "/nodes/Node-A/online", "admin", {"did": dev["did"], "nonce": uniq("test-nonce"), "signature": "ab" * 64})
    C("API-NODE-06", "契约 2.8 online 无合法签名拒绝 / 文档(一)2 设备上线校验 DID / 1004", "伪造签名上线", "403/1004", expect_code(r, b, 403, 1004), b, r.status_code)
    nonce = uniq("test-nonce")
    sig = gm.sign(nonce, v2["privateKey"], v2["publicKey"])
    r, b = req("POST", "/nodes/Node-A/online", "admin", {"did": dev["did"], "nonce": nonce, "signature": sig})
    C("API-NODE-07a", "文档(一)5 设备身份绑定关系表·DID 与节点绑定校验", "用合法签名但未绑定到 Node-A 的 DID 上线", "403/1004 message 说明绑定不符", expect_code(r, b, 403, 1004) + [("绑定" in b.get("message", ""), b.get("message"))], b, r.status_code)
    # 取 Node-D 绑定的 edge DID，轮换一次密钥（custody=true 保持托管，不影响他人）拿到私钥
    rr, bb = req("GET", "/nodes/Node-D", "admin")
    edge_did = (bb.get("data") or {}).get("did")
    rr, bb = req("POST", f"/did/{edge_did}/rotate-key", "admin", {"reason": "test-取私钥做上线签名", "custody": True})
    ek = bb.get("data") or {}
    CTX["edge_d"] = {"did": edge_did, "privateKey": ek.get("privateKey"), "publicKey": ek.get("publicKey")}
    nonce = uniq("test-nonce")
    sig = gm.sign(nonce, ek.get("privateKey") or "0" * 64, ek.get("publicKey") or "04" + "0" * 128) if ek.get("privateKey") else "00"
    r, b = req("POST", "/nodes/Node-D/online", "admin", {"did": edge_did, "nonce": nonce, "signature": sig})
    d = (b.get("data") or {})
    C("API-NODE-07", "契约 2.8 online 正向（客户端持私钥 SM2 签 nonce）", "Node-D 绑定 edge DID 轮换后用新私钥签 nonce 上线", "accepted=true nodeId=Node-D sessionToken evidenceId",
      ok(r, b) + [(d.get("accepted") is True, "accepted"), has_keys(d, ["accepted", "nodeId", "sessionToken", "evidenceId"])], b, r.status_code)
    r, b = req("POST", "/nodes/Node-D/online", "admin", {"did": edge_did, "nonce": nonce, "signature": sig})
    C("API-NODE-08", "安全·nonce 重放", "同 nonce 同签名再上线", "403/1004", expect_code(r, b, 403, 1004), b, r.status_code)
    nonce2 = uniq("test-nonce")
    sig_old = gm.sign(nonce2, v2["privateKey"], v2["publicKey"])
    r, b = req("POST", "/nodes/Node-D/online", "admin", {"did": edge_did, "nonce": nonce2, "signature": sig_old})
    C("API-NODE-09", "安全·他人私钥冒用 edge DID", "用测试设备私钥为 Node-D DID 签名", "403/1004", expect_code(r, b, 403, 1004), b, r.status_code)
    req("POST", f"/did/{edge_did}/status", "admin", {"action": "freeze", "reason": "test-短暂冻结"})
    nonce3 = uniq("test-nonce")
    r, b = req("POST", "/nodes/Node-D/online", "admin", {"did": edge_did, "nonce": nonce3, "signature": gm.sign(nonce3, ek.get("privateKey") or "0" * 64, ek.get("publicKey") or "04" + "0" * 128)})
    req("POST", f"/did/{edge_did}/status", "admin", {"action": "unfreeze"})
    C("API-NODE-10", "文档(一)2 冻结 DID 上线拒绝", "冻结 Node-D DID 后合法签名上线（随后立即解冻）", "403/1004", expect_code(r, b, 403, 1004), b, r.status_code)
    r, b = req("POST", "/nodes/Node-A/online", None, {"did": dev["did"], "nonce": "x", "signature": "y"})
    C("API-NODE-11", "1002", "未登录上线", "401/1002", expect_code(r, b, 401, 1002), b, r.status_code)


# ============================================================ 2.9 FL
def sec_fl():
    r, b = req("POST", "/fl/tasks", "admin", {"name": uniq("test-fl"), "nodeIds": ["Node-A", "Node-B", "Node-C", "Node-D"], "rounds": 3,
                                             "dp": {"enabled": True, "epsilon": 1.0, "delta": 0.00001}, "topk": {"enabled": True, "ratio": 0.1}})
    d = (b.get("data") or {})
    fl = d.get("id"); CTX["fl"] = fl
    C("API-FL-01", "契约 2.9 POST /fl/tasks", "创建 FL 任务 3 轮", "id=fl-xxxxxx status=created createdAt traceId",
      ok(r, b) + [has_keys(d, ["id", "status", "createdAt", "traceId"]), (str(fl).startswith("fl-") and d.get("status") == "created", str(d))], b, r.status_code)
    r, b = req("POST", "/fl/tasks", "admin", {"name": "x", "nodeIds": ["Node-Z"], "rounds": 1})
    C("API-FL-02", "1005/1001 节点不存在", "nodeIds=[Node-Z]", "404/1005 或 400/1001", [(b.get("code") in (1005, 1001), str(b.get("code")))], b, r.status_code)
    r, b = req("POST", "/fl/tasks", "admin", {"name": "x", "nodeIds": ["Node-A"], "rounds": 0})
    C("API-FL-03", "1001", "rounds=0", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("POST", "/fl/tasks", "vpp", {"name": "x", "nodeIds": ["Node-A"], "rounds": 1})
    C("API-FL-04", "文档(三) 联邦聚合仅管理员可执行 / 1003", "vpp 创建 FL 任务", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("POST", f"/fl/tasks/{fl}/start", "vpp")
    C("API-FL-05", "algo:execute 仅 sys_admin/grid_dispatcher / 1003", "vpp 启动", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("POST", f"/fl/tasks/{fl}/start", "admin")
    C("API-FL-06", "契约 2.9 start / 验收 7", "admin 启动", "200 status=running", ok(r, b) + [(b["data"].get("status") == "running", str(b["data"]))], b, r.status_code)
    r, b = req("POST", f"/fl/tasks/{fl}/start", "admin")
    C("API-FL-07", "1006 任务已在运行", "重复启动", "409/1006", expect_code(r, b, 409, 1006), b, r.status_code)
    st = None
    for _ in range(60):
        time.sleep(2)
        r, b = req("GET", f"/fl/tasks/{fl}", "admin")
        st = (b.get("data") or {})
        if st.get("status") in ("success", "failed", "cancelled"):
            break
    rounds = st.get("rounds", [])
    losses = [x.get("loss") for x in rounds]
    C("API-FL-08", "契约 2.9 GET /fl/tasks/{id} / 文档(三) 梯度哈希上链", "轮询至完成", "status=success currentRound=3；nodes[nodeId,did,joined,samples]；dp.epsilonSpent 累加；rounds[round,loss,acc,compressionRatio,epsilonSpent,gradientHash,evidenceId]；每轮 evidenceId；modelVersion 非空",
      ok(r, b) + [(st.get("status") == "success", f"status={st.get('status')}"), (st.get("currentRound") == 3 and st.get("totalRounds") == 3, "轮次"),
                  has_keys(st, ["id", "name", "status", "currentRound", "totalRounds", "nodes", "dp", "topk", "rounds", "modelVersion", "traceId"]),
                  has_keys((st.get("nodes") or [{}])[0], ["nodeId", "did", "joined", "samples"]),
                  (len(rounds) == 3 and all(has_keys(x, ["round", "loss", "acc", "compressionRatio", "epsilonSpent", "gradientHash", "evidenceId"])[0] for x in rounds), "rounds 字段"),
                  (all(str(x.get("evidenceId", "")).startswith("ev-") for x in rounds), "轮次未上链"),
                  (st.get("dp", {}).get("epsilonSpent", 0) > 0, "epsilonSpent 未累加"), (bool(st.get("modelVersion")), "modelVersion 空")],
      {"status": st.get("status"), "losses": losses, "dp": st.get("dp"), "modelVersion": st.get("modelVersion"), "round1": rounds[:1]}, r.status_code)
    rows = db_query("SELECT round_no, loss, acc, evidence_id, gradient_hash FROM algo_fl_round WHERE task_id=%s ORDER BY round_no", (fl,)) if db_query("SHOW COLUMNS FROM algo_fl_round LIKE 'round_no'") else db_query("SELECT * FROM algo_fl_round WHERE task_id=%s", (fl,))
    C("API-FL-09", "DB-SCHEMA algo_fl_round 轮次指标入库", "查 algo_fl_round", "3 行，evidence_id 非空", [(len(rows) == 3, f"rows={len(rows)}"), (all(x.get("evidence_id") for x in rows), "evidence_id 空")], rows[:1])
    rows = db_query("SELECT * FROM algo_model_version WHERE version=%s", (st.get("modelVersion"),))
    C("API-FL-10", "DB-SCHEMA algo_model_version", "查模型版本表", "存在该版本", [(len(rows) == 1, f"rows={len(rows)}")], {k: str(v)[:40] for k, v in rows[0].items()} if rows else None)
    rows = db_query("SELECT evidence_id, payload_snapshot FROM chain_evidence WHERE category='algo' AND ref_id LIKE %s ORDER BY id DESC LIMIT 1", (f"{fl}%",))
    snap = json.dumps(rows[0]["payload_snapshot"] if rows else "")
    C("API-FL-11", "文档(三)(二) 梯度存证只存哈希", "查 algo 类存证 payload_snapshot", "含 gradientHash，不含原始梯度数组", [(bool(rows), "无 algo 存证"), ("gradientHash" in snap or "gradient_hash" in snap, "无梯度哈希"), (len(snap) < 2000, "存证过大疑似含原始梯度")], snap[:150])
    r, b = req("GET", f"/fl/tasks/{fl}/rounds", "admin")
    C("API-FL-12", "契约 2.9 rounds", "每轮指标", "200 3 条", ok(r, b) + [(len(b["data"] if isinstance(b["data"], list) else b["data"].get("items", [])) == 3, "数量")], {"n": len(b["data"] if isinstance(b["data"], list) else b["data"].get("items", []))}, r.status_code)
    r, b = req("GET", "/fl/tasks", "admin", params={"page": 1, "size": 5})
    C("API-FL-13", "契约 2.9 GET /fl/tasks 分页", "任务列表", "分页；含该任务", ok(r, b) + [page_ok(b.get("data")), (fl in json.dumps(b) or b["data"]["total"] > 5, "未命中")], {"total": (b.get("data") or {}).get("total")}, r.status_code)
    r, b = req("GET", "/fl/tasks/fl-999999", "admin")
    C("API-FL-14", "1005", "不存在任务", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("POST", f"/fl/tasks/{fl}/cancel", "admin")
    C("API-FL-15", "1006 已完成不可取消", "取消已完成任务", "409/1006", expect_code(r, b, 409, 1006), b, r.status_code)
    r, b = req("POST", "/fl/tasks", "admin", {"name": uniq("test-fl-cancel"), "nodeIds": ["Node-A", "Node-B"], "rounds": 10})
    fl2 = b["data"]["id"]
    req("POST", f"/fl/tasks/{fl2}/start", "admin")
    time.sleep(1)
    r, b = req("POST", f"/fl/tasks/{fl2}/cancel", "admin")
    C("API-FL-16", "契约 2.9 cancel", "取消运行中任务", "status=cancelled", ok(r, b) + [(b["data"].get("status") == "cancelled", str(b["data"]))], b, r.status_code)
    r, b = req("GET", "/fl/models", "admin")
    models = b.get("data") if isinstance(b.get("data"), list) else (b.get("data") or {}).get("items", [])
    C("API-FL-17", "契约 2.9 GET /fl/models", "模型版本列表", "含本次 modelVersion", ok(r, b) + [(st.get("modelVersion") in json.dumps(models), "未含新版本")], {"n": len(models)}, r.status_code)
    r, b = req("POST", f"/fl/models/{st.get('modelVersion')}/publish", "admin")
    r2, b2 = req("POST", f"/fl/models/{st.get('modelVersion')}/publish", "admin")
    C("API-FL-18", "契约 2.9 publish + 1006 重复发布", "发布模型；再次发布", "首次 200；重复 409/1006", ok(r, b) + expect_code(r2, b2, 409, 1006), b2, r2.status_code)
    r, b = req("POST", "/fl/models/v-none/publish", "admin")
    C("API-FL-19", "1005", "发布不存在版本", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("GET", f"/fl/tasks/{fl}", "subject")
    C("API-FL-20", "矩阵 energy_subject 无 model:read / 1003", "subject 查 FL 任务", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)


# ============================================================ 2.10 dispatch
def sec_dispatch():
    r, b = req("POST", "/dispatch/tasks", "admin", {"name": uniq("test-dp"), "nodeIds": ["Node-A", "Node-C", "Node-D"], "timeWindow": "2026-08-22T19:00~20:00+08:00"})
    d = (b.get("data") or {}); dp = d.get("id"); CTX["dp"] = dp
    C("API-DP-01", "契约 2.10 POST /dispatch/tasks", "创建调度任务", "id=dp-xxxxxx status=created", ok(r, b) + [(str(dp).startswith("dp-"), str(d))], b, r.status_code)
    r, b = req("POST", "/dispatch/tasks", "admin", {"name": "x", "nodeIds": []})
    C("API-DP-02", "1001", "nodeIds 为空", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("POST", f"/dispatch/tasks/{dp}/issue", "admin", {})
    C("API-DP-03", "1006 状态不允许", "未 run 先 issue", "409/1006", expect_code(r, b, 409, 1006), b, r.status_code)
    r, b = req("POST", f"/dispatch/tasks/{dp}/run", "subject")
    C("API-DP-04", "1003", "subject run", "403/1003", expect_code(r, b, 403, 1003), b, r.status_code)
    r, b = req("POST", f"/dispatch/tasks/{dp}/run", "admin")
    d = (b.get("data") or {}); CTX["dp_run"] = d
    acts = d.get("strategy", {}).get("actions", [])
    nc = next((a for a in acts if a.get("nodeId") == "Node-C"), {})
    C("API-DP-05", "契约 2.10 run DQN + DeepSeek 解释 / 验收 8", "admin run", "status=success；strategy.actions[nodeId,action∈charge|idle|discharge,powerKw,qValue,reason] 每节点一条；totalReward/timeWindow；explanation 中文；explanationSource∈live|cache|rule；evidenceId/traceId；signPayload",
      ok(r, b) + [(d.get("status") == "success", "status"), (len(acts) == 3 and all(a.get("action") in ("charge", "idle", "discharge") for a in acts), f"actions={[(a.get('nodeId'), a.get('action')) for a in acts]}"),
                  has_keys(nc, ["nodeId", "action", "powerKw", "qValue", "reason"]), has_keys(d.get("strategy"), ["actions", "totalReward", "timeWindow"]),
                  (d.get("explanationSource") in ("live", "cache", "rule"), f"src={d.get('explanationSource')}"), (len(d.get("explanation") or "") > 5, "explanation 空"),
                  has_keys(d, ["evidenceId", "traceId", "signPayload"])], {"actions": [(a.get("nodeId"), a.get("action"), a.get("powerKw")) for a in acts], "explanationSource": d.get("explanationSource"), "signPayload": d.get("signPayload")}, r.status_code)
    r, b = req("POST", f"/dispatch/tasks/{dp}/issue", "vpp", {})
    C("API-DP-06", "契约 2.10 issue 无权限 1003 + high 审计 / 验收 6", "vpp issue", "403/1003 message 含 dispatch:issue", expect_code(r, b, 403, 1003) + [("dispatch:issue" in b.get("message", ""), "message")], b, r.status_code)
    tid_vpp = b.get("traceId")
    time.sleep(0.3)
    r2, b2 = req("GET", "/audit/logs", "admin", params={"traceId": tid_vpp})
    it = ((b2.get("data") or {}).get("items") or [{}])[0]
    C("API-DP-07", "契约 2.10 两种失败都记 high 审计", "按 vpp issue 的 traceId 查审计", "result=denied riskLevel=high evidenceId 非空 action=dispatch:issue",
      ok(r2, b2) + [(it.get("result") == "denied" and it.get("riskLevel") == "high" and it.get("evidenceId"), str({k: it.get(k) for k in ('action', 'result', 'riskLevel', 'evidenceId')}))], it, r2.status_code)
    wrong_pub, wrong_priv = gm.generate_keypair()
    bad_sig = gm.sign(d.get("signPayload", ""), wrong_priv, wrong_pub)
    r, b = req("POST", f"/dispatch/tasks/{dp}/issue", "admin", {"signature": bad_sig})
    C("API-DP-08", "契约 2.10 签名无效 1004 / 文档(一) 调度指令 DID 验签", "admin 用错私钥签 signPayload 下发", "403/1004", expect_code(r, b, 403, 1004), b, r.status_code)
    r, b = req("POST", f"/dispatch/tasks/{dp}/issue", "admin", {"signature": gm.sign("dispatch:issue:" + dp + ":sm3:" + "0" * 64, wrong_priv, wrong_pub)})
    C("API-DP-09", "安全·签名绑定策略摘要", "签错误原文", "403/1004", expect_code(r, b, 403, 1004), b, r.status_code)
    r, b = req("POST", f"/dispatch/tasks/{dp}/issue", "admin", {})
    d2 = (b.get("data") or {})
    C("API-DP-10", "契约 2.10 issue 正向（托管私钥代签+验签）", "admin issue", "issued=true commandId/signerDid=admin did/evidenceId/targets",
      ok(r, b) + [(d2.get("issued") is True, "issued"), has_keys(d2, ["issued", "commandId", "signerDid", "evidenceId", "targets"]), (d2.get("signerDid") == CTX["admin_did"], "signerDid")], b, r.status_code)
    r, b = req("POST", f"/dispatch/tasks/{dp}/issue", "admin", {})
    C("API-DP-11", "1006 重复下发", "再次 issue", "409/1006", expect_code(r, b, 409, 1006), b, r.status_code)
    r, b = req("POST", f"/dispatch/tasks/{dp}/run", "admin")
    C("API-DP-12", "1006 已下发不可重跑", "下发后 run", "409/1006", expect_code(r, b, 409, 1006), b, r.status_code)
    r, b = req("POST", f"/dispatch/tasks/{dp}/ack", "admin", {"nodeId": "Node-C", "accepted": True})
    C("API-DP-13", "契约 2.10 ack", "Node-C 回执", "200 ackStatus", ok(r, b) + [("ackStatus" in (b.get("data") or {}), "无 ackStatus")], b, r.status_code)
    r, b = req("POST", f"/dispatch/tasks/{dp}/ack", "admin", {"nodeId": "Node-Z", "accepted": True})
    C("API-DP-14", "1001/1005 非目标节点回执", "Node-Z 回执", "400/1001 或 404/1005", [(b.get("code") in (1001, 1005), str(b.get("code")))], b, r.status_code)
    r, b = req("GET", f"/dispatch/tasks/{dp}", "admin")
    C("API-DP-15", "契约 2.10 GET /dispatch/tasks/{id}", "任务详情", "status=issued 或含 strategy", ok(r, b) + [(b["data"].get("id") == dp and b["data"].get("strategy"), "详情")], {k: b["data"].get(k) for k in ("id", "status", "ackStatus")}, r.status_code)
    r, b = req("GET", "/dispatch/tasks", "admin", params={"size": 5})
    C("API-DP-16", "契约 2.10 GET /dispatch/tasks", "列表", "分页", ok(r, b) + [page_ok(b.get("data"))], {"total": (b.get("data") or {}).get("total")}, r.status_code)
    r, b = req("GET", "/dispatch/tasks/dp-999999", "admin")
    C("API-DP-17", "1005", "不存在任务", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    rows = db_query("SELECT task_id, status, signature, signer_did FROM algo_dispatch_task WHERE task_id=%s", (dp,))
    rows = db_query("SELECT task_id, status, issued, command_id, signer_did, signature, evidence_id FROM algo_dispatch_task WHERE task_id=%s", (dp,))
    C("API-DP-18", "DB algo_dispatch_task 下发落库", "查 algo_dispatch_task", "issued=1 command_id/signer_did/evidence_id 非空", [(bool(rows) and rows[0].get("issued") == 1 and rows[0].get("command_id") and rows[0].get("signer_did"), str(rows))], {k: str(v)[:30] for k, v in rows[0].items()} if rows else None)
    C("API-DP-18b", "文档(一) 调度指令携带签发者 DID 签名·签名可追溯", "查 algo_dispatch_task.signature", "托管代签的签名也应落库（非 NULL），便于事后核验", [(bool(rows) and bool(rows[0].get("signature")), "signature 为 NULL")], {"signature": str(rows[0].get("signature"))[:30] if rows else None})
    # grid 调度员可下发
    r, b = req("POST", "/dispatch/tasks", "grid", {"name": uniq("test-dp-grid"), "nodeIds": ["Node-B"]})
    dp2 = (b.get("data") or {}).get("id")
    req("POST", f"/dispatch/tasks/{dp2}/run", "grid")
    r, b = req("POST", f"/dispatch/tasks/{dp2}/issue", "grid", {})
    C("API-DP-19", "矩阵 grid_dispatcher 有 dispatch:issue", "grid 创建→run→issue", "issued=true 或 1004（grid 无托管密钥时）", [(b.get("code") == 0 and b["data"].get("issued") or b.get("code") == 1004, str(b.get("code")))], b, r.status_code)


# ============================================================ 2.11 / 2.12
def sec_ai_risk():
    r, b = req("POST", "/ai/analyze", "admin", {"scene": "dispatch", "context": {"taskId": CTX.get("dp")}, "question": "为什么选择节点C放电？"})
    d = (b.get("data") or {})
    C("API-AI-01", "契约 2.11 analyze / 文档(三) DeepSeek 网关 审计+存证", "dispatch 场景问答", "answer/reasoning[]/source∈live|cache|rule/latencyMs/evidenceId/traceId",
      ok(r, b) + [has_keys(d, ["answer", "reasoning", "source", "latencyMs", "evidenceId", "traceId"]), (d.get("source") in ("live", "cache", "rule"), f"source={d.get('source')}")], {**b, "data": {**d, "answer": str(d.get("answer"))[:50]}}, r.status_code)
    CTX["ai_source"] = d.get("source")
    r, b = req("POST", "/ai/analyze", "admin", {"scene": "weather", "question": "x"})
    C("API-AI-02", "2.11 scene 枚举 / 1001", "scene=weather", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("POST", "/ai/analyze", "subject", {"scene": "qa", "question": "test-平台有几个节点？<script>alert(1)</script>"})
    C("API-AI-03", "文档(三) DeepSeek 对所有角色只读 / XSS 负载", "subject qa 含 script 标签", "200（只读允许）且不 500", ok(r, b), {"source": (b.get("data") or {}).get("source")}, r.status_code)
    r, b = req("GET", "/ai/history", "admin", params={"size": 5})
    C("API-AI-04", "契约 2.11 history", "分析历史", "分页且含刚才记录", ok(r, b) + [page_ok(b.get("data")), (b["data"].get("total", 0) >= 1, "空")], {"total": (b.get("data") or {}).get("total")}, r.status_code)
    rows = db_query("SELECT COUNT(*) n FROM algo_ai_analysis WHERE created_at > NOW() - INTERVAL 5 MINUTE")
    C("API-AI-05", "DB algo_ai_analysis 入库 / 文档(三) 大模型调用全量审计", "查 algo_ai_analysis", "≥2 条", [(rows[0]["n"] >= 2, f"n={rows[0]['n']}")], rows)
    r, b = req("POST", "/risk/assess", "admin", {"nodeId": "Node-A", "features": {"queryFreq": 12, "dataGranularity": "minute", "exposedFields": 6}})
    d = (b.get("data") or {})
    C("API-RISK-01", "契约 2.12 assess", "Node-A 风险评估", "nodeId/riskScore/level/factors[name,weight,score]/suggestion/evidenceId",
      ok(r, b) + [has_keys(d, ["nodeId", "riskScore", "level", "factors", "suggestion", "evidenceId"]), has_keys((d.get("factors") or [{}])[0], ["name", "weight", "score"])], b, r.status_code)
    r, b = req("POST", "/risk/assess", "admin", {"nodeId": "Node-Z", "features": {}})
    C("API-RISK-02", "1005", "不存在节点", "404/1005", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("GET", "/risk/history", "admin", params={"nodeId": "Node-A", "size": 5})
    C("API-RISK-03", "契约 2.12 history", "历史评分", "分页", ok(r, b) + [page_ok(b.get("data"))], {"total": (b.get("data") or {}).get("total")}, r.status_code)
    r, b = req("GET", "/risk/history", None)
    C("API-RISK-04", "1002", "未登录", "401/1002", expect_code(r, b, 401, 1002), b, r.status_code)


# ============================================================ 安全专项
def sec_security():
    r, b = req("GET", "/no-such-endpoint", "admin")
    C("API-SEC-01", "契约 1.1 404 也四段式", "GET /api/v1/no-such-endpoint", "404/1005 统一包裹", expect_code(r, b, 404, 1005), b, r.status_code)
    r, b = req("DELETE", "/auth/login", None)
    C("API-SEC-02", "契约 1.1 405 也统一包裹", "DELETE /auth/login", "统一包裹，code≠0，不裸 FastAPI", [envelope_ok(b), (r.status_code in (404, 405), f"HTTP {r.status_code}")], b, r.status_code)
    r = client.post("/auth/login", content=b"{bad json", headers={"Content-Type": "application/json"})
    try:
        b = r.json()
    except Exception:
        b = r.text
    C("API-SEC-03", "1001 非法 JSON", "body 非法 JSON", "400/1001 统一包裹", expect_code(r, b, 400, 1001), b, r.status_code)
    # 扫描：所有 /api/v1 接口不带 token → 1002（login 除外）
    spec = client.get("http://127.0.0.1:8000/openapi.json").json()["paths"]
    bad = []
    for p, ms in spec.items():
        if not p.startswith("/api/v1") or p.endswith("/auth/login"):
            continue
        for m in ms:
            rr = client.request(m.upper(), p.replace("/api/v1", "").replace("{", "test-").replace("}", ""))
            try:
                code = rr.json().get("code")
            except Exception:
                code = rr.text[:30]
            if code != 1002:
                bad.append(f"{m.upper()} {p} -> {rr.status_code}/{code}")
    C("API-SEC-04", "README A 段·全量无凭证扫描", f"遍历 openapi {sum(len(v) for k, v in spec.items() if k.startswith('/api/v1'))} 个接口不带 token", "除 login 外全部 401/1002", [(not bad, "; ".join(bad[:5]))], bad[:5] or "全部 1002")
    # 水平越权：subject 查他人申请/授权
    r, b = req("GET", "/permissions/applications", "subject", params={"size": 200})
    sdid = json.loads(base64.urlsafe_b64decode(token("subject").split(".")[1] + "==")).get("did")
    foreign = [i for i in (b.get("data") or {}).get("items", []) if i.get("applicantDid") != sdid]
    C("API-SEC-05", "README C 段·水平越权", "subject 查申请列表", "只能看到自己的申请", ok(r, b) + [(not foreign, f"{len(foreign)} 条他人申请")], {"total": (b.get("data") or {}).get("total")}, r.status_code)
    r, b = req("GET", f"/users/{CTX.get('user_id')}", "subject")
    C("API-SEC-06", "水平越权·用户资料（契约无 GET /users/{id}）", "subject GET /users/{id}", "非 200 且统一包裹（实际 405→1001，契约无 405 码，记录）", [envelope_ok(b), (b.get("code") != 0, "返回了数据")], b, r.status_code)
    # SQL 注入
    inj = "1' OR '1'='1"
    r, b = req("GET", f"/assets/{inj}", "admin")
    C("API-SEC-07", "README E 段·路径 SQL 注入", f"GET /assets/{inj}", "400/1001 或 404/1005，不 500", [(r.status_code in (400, 404) and b.get("code") in (1001, 1005), f"HTTP {r.status_code} {b.get('code')}")], b, r.status_code)
    r, b = req("GET", "/did", "admin", params={"keyword": "%' UNION SELECT password_hash FROM sys_user -- "})
    C("API-SEC-08", "SQL 注入·keyword UNION", "GET /did?keyword=UNION…", "200 无哈希泄露", ok(r, b) + [("$2b$" not in json.dumps(b), "泄露 bcrypt")], {"total": (b.get("data") or {}).get("total")}, r.status_code)
    r, b = req("GET", "/audit/logs", "admin", params={"from": "2026-08-01' OR 1=1 --"})
    C("API-SEC-09", "SQL 注入·时间参数", "from 注入", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    # XSS 存储：注册含脚本的 DID 名
    xss = "<img src=x onerror=alert(1)>test-xss"
    r, b = req("POST", "/did/register", "admin", {"subjectType": "device", "subjectName": xss})
    r2, b2 = req("GET", "/did", "admin", params={"keyword": "test-xss"})
    C("API-SEC-10", "XSS 负载存储与回显", "subjectName 含 <img onerror>", "接口 200；JSON 原样/转义返回（前端负责转义），content-type application/json",
      ok(r, b) + [("application/json" in r2.headers.get("content-type", ""), "content-type")], {"ct": r2.headers.get("content-type"), "hit": (b2.get("data") or {}).get("total")}, r2.status_code)
    big = "A" * 100000
    r, b = req("POST", "/did/register", "admin", {"subjectType": "device", "subjectName": big})
    C("API-SEC-11", "README E 段·超长输入", "subjectName 100KB", "400/1001", expect_code(r, b, 400, 1001), b, r.status_code)
    r, b = req("GET", "/did", "admin", params={"page": -1, "size": 0})
    C("API-SEC-12", "分页越界", "page=-1&size=0", "400/1001 或归一化为 page=1", [(b.get("code") == 1001 or (b.get("code") == 0 and b["data"]["page"] >= 1 and b["data"]["size"] >= 1), str(b.get("code")))], b, r.status_code)
    # 敏感信息不泄露
    r, b = req("GET", "/keys", "admin", params={"did": CTX["dev_custody"]["did"]})
    leak = CTX["dev_custody"]["privateKey"] in json.dumps(b) or "privateKeyEnc" in json.dumps(b) or "private_key_enc" in json.dumps(b)
    C("API-SEC-13", "README F 段·托管私钥不出接口", "GET /keys 托管 DID", "响应无明文私钥/密文字段", ok(r, b) + [(not leak, "泄露私钥")], {"keys": list(((b.get("data") or {}).get("items") or [{}])[0].keys())}, r.status_code)
    r, b = req("GET", f"/users/{CTX['user_id']}", "admin")
    r2, b2 = req("GET", "/auth/me", "admin")
    s = json.dumps(b) + json.dumps(b2)
    C("API-SEC-14", "F 段·口令哈希不出接口", "GET /users/{id} + /auth/me", "无 password/passwordHash 字段", [("password" not in s.lower() or '"password' not in s.lower(), "含 password 字段"), ("$2b$" not in s, "含 bcrypt")], {"keys": list((b.get("data") or {}).keys()) if isinstance(b.get("data"), dict) else b.get("code")}, r.status_code)
    r = client.post("/auth/login", json={"username": "admin", "password": {"$ne": 1}})
    b = r.json()
    C("API-SEC-15", "F 段·堆栈不外泄", "password 传对象触发校验错", "400/1001，message 不含 Traceback/File", expect_code(r, b, 400, 1001) + [("Traceback" not in json.dumps(b) and "File \"" not in json.dumps(b), "堆栈泄露")], b, r.status_code)
    # 暴力登录：用测试账号
    u = uniq("test-brute")
    req("POST", "/users", "admin", {"username": u, "password": "Brute-123456", "realName": "爆破测试", "roles": ["vpp_operator"]})
    codes = []
    for _ in range(6):
        rr, bb = req("POST", "/auth/login", None, {"username": u, "password": "wrong"})
        codes.append(bb.get("code"))
    rr, bb = req("POST", "/auth/login", None, {"username": u, "password": "Brute-123456"})
    C("API-SEC-16", "README 撞库锁定 5 次/5 分钟 → 1007 / 文档(四)3", "测试账号连错 6 次后用正确密码登录", "前 5 次 1002，第 6 次起 429/1007，正确密码也被锁",
      [(codes[:5] == [1002] * 5, f"codes={codes}"), (codes[5] == 1007, f"第 6 次={codes[5]}"), (bb.get("code") == 1007 and rr.status_code == 429, f"正确密码 {rr.status_code}/{bb.get('code')}")], {"codes": codes, "correct_after_lock": bb.get("code")}, rr.status_code)
    # CORS / 安全头
    r = client.options("/auth/me", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    r2 = client.get("/auth/me", headers={"Origin": "http://evil.example", **hdr("admin")})
    C("API-SEC-17", "CORS·后端不开放跨域", "OPTIONS/GET 带 Origin:evil", "响应无 Access-Control-Allow-Origin:*（同源经 nginx 反代）", [("access-control-allow-origin" not in r.headers and "access-control-allow-origin" not in r2.headers, f"ACAO={r2.headers.get('access-control-allow-origin')}")], dict(r2.headers), r2.status_code)
    conf = open(os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "nginx.conf"), encoding="utf-8").read()
    C("API-SEC-18", "deploy/nginx.conf 安全头审查", "静态审查 nginx.conf", "含 X-Content-Type-Options nosniff / X-Frame-Options / Referrer-Policy；/ws 带 Upgrade；/api 透传 Authorization 与 X-Trace-Id；无 CSP/HSTS（记录）",
      [("X-Content-Type-Options nosniff" in conf, "nosniff"), ("X-Frame-Options" in conf, "XFO"), ("Referrer-Policy" in conf, "Referrer"), ("Upgrade" in conf and "$http_upgrade" in conf, "ws upgrade"), ("X-Trace-Id" in conf and "Authorization" in conf, "透传")],
      {"csp": "Content-Security-Policy" in conf, "hsts": "Strict-Transport-Security" in conf, "server_tokens": "server_tokens" in conf})
    hs = {k: v for k, v in r2.headers.items() if k.lower() in ("server", "x-trace-id", "content-type")}
    C("API-SEC-19", "后端响应头", "观察 uvicorn 响应头", "含 X-Trace-Id；Server 头为 uvicorn（记录，生产由 nginx 前置）", [("x-trace-id" in r2.headers, "无 X-Trace-Id")], hs)
    # 直接改库被抓（G 段）
    r, b = req("POST", "/evidence", "admin", {"category": "data", "refId": "test-g", "payload": {"g": 1}})
    ev = b["data"]["evidenceId"]
    def _sql(q, args):
        conn = __import__("pymysql").connect(host="127.0.0.1", user="energy", password="energy123", database="energy_tds")
        try:
            with conn.cursor() as cur:
                cur.execute(q, args)
                rows_ = cur.fetchall()
            conn.commit()
            return rows_
        finally:
            conn.close()
    original = _sql("SELECT payload_snapshot FROM chain_evidence WHERE evidence_id=%s", (ev,))[0][0]
    _sql("UPDATE chain_evidence SET payload_snapshot=%s WHERE evidence_id=%s", ('{"g": 2}', ev))
    r, b = req("POST", "/evidence/verify", "admin", {"evidenceId": ev})
    C("API-SEC-20", "README G 段·绕过应用层直接改库被抓", "SQL 直接改 payload_snapshot 后 verify", "intact=false", ok(r, b) + [(b["data"].get("intact") is False, "未检出")], b, r.status_code)
    _sql("UPDATE chain_evidence SET payload_snapshot=%s WHERE evidence_id=%s", (original, ev))
    r, b = req("POST", "/evidence/verify", "admin", {"evidenceId": ev})
    C("API-SEC-21", "还原后该条完整", "恢复 payload_snapshot 后 verify", "intact=true", ok(r, b) + [(b["data"].get("intact") is True, str(b["data"]))], b, r.status_code)
    # 审计/存证没有 DELETE/PUT 接口
    ro = [p for p in spec if p.startswith("/api/v1/audit") or p.startswith("/api/v1/evidence")]
    mut = [f"{m} {p}" for p in ro for m in spec[p] if m.upper() in ("PUT", "DELETE", "PATCH")]
    C("API-SEC-22", "G 段·审计与存证无改删接口", "扫 openapi", "audit/evidence 无 PUT/DELETE/PATCH", [(not mut, str(mut))], ro)


# ============================================================ 数据库专项
def sec_db():
    rows = db_query("SELECT table_name t FROM information_schema.tables WHERE table_schema='energy_tds'")
    names = {r["t"] for r in rows}
    need = {"sys_user", "sys_role", "sys_user_role", "sys_role_permission", "did_identity", "did_key", "did_key_rotation_log", "did_device_binding",
            "energy_asset", "energy_asset_lineage", "perm_application", "perm_grant", "perm_change_log", "chain_evidence", "audit_alert",
            "algo_fl_task", "algo_fl_round", "algo_model_version", "algo_dispatch_task", "algo_ai_analysis", "node_info", "node_metric"}
    C("API-DB-01", "DB-SCHEMA 表清单", "information_schema 查表", "23 类表齐全 + audit_log_YYYYMM", [(need <= names, f"缺 {need - names}"), (any(n.startswith("audit_log_2") for n in names), "无审计分表")], sorted(names))
    for tbl, cols in {"did_identity": ["id", "did", "subject_type", "subject_name", "org_name", "controller_did", "did_document", "status", "metadata", "created_at", "updated_at"],
                      "chain_evidence": ["id", "evidence_id", "category", "ref_id", "actor_did", "payload_hash", "prev_hash", "block_hash", "block_height", "tx_id", "trace_id", "payload_snapshot", "created_at"],
                      "audit_log_202608": ["id", "trace_id", "actor_did", "actor_name", "module", "action", "resource_type", "resource_id", "result", "risk_level", "detail", "ip", "evidence_id", "hash", "created_at"]}.items():
        rows = db_query(f"SELECT COLUMN_NAME c, COLUMN_TYPE ty FROM information_schema.columns WHERE table_schema='energy_tds' AND table_name='{tbl}'")
        have = {r["c"]: r["ty"] for r in rows}
        C(f"API-DB-0{2 + list(['did_identity', 'chain_evidence', 'audit_log_202608']).index(tbl)}", f"DB-SCHEMA 关键表 {tbl} 字段逐字对照", f"information_schema.columns {tbl}", "契约列全部存在", [(set(cols) <= set(have), f"缺 {set(cols) - set(have)}")], {c: have.get(c) for c in cols if c in have and ("enum" in have[c] or "json" in have[c].lower())})
    rows = db_query("SELECT COLUMN_TYPE ty FROM information_schema.columns WHERE table_schema='energy_tds' AND table_name='chain_evidence' AND column_name='category'")
    C("API-DB-05", "DB-SCHEMA ENUM 值", "chain_evidence.category 枚举", "enum('data','identity','permission','audit','algo')", [(rows and "'data','identity','permission','audit','algo'" in rows[0]["ty"], str(rows))], rows)
    rows = db_query("SELECT CONSTRAINT_NAME n FROM information_schema.table_constraints WHERE table_schema='energy_tds' AND table_name='chain_evidence' AND constraint_type='UNIQUE'")
    C("API-DB-06", "DB-SCHEMA uk_height 唯一约束", "chain_evidence 唯一约束", "含 uk_height 与 evidence_id 唯一", [({"uk_height"} <= {r["n"] for r in rows}, str(rows))], rows)
    # 链重算
    rows = db_query("SELECT evidence_id, prev_hash, payload_hash, block_hash, block_height, created_at, payload_snapshot FROM chain_evidence ORDER BY block_height")
    prev = "sm3:" + "0" * 64; broken = []; heights_ok = True; tampered = []
    from datetime import timezone, timedelta
    cst = timezone(timedelta(hours=8))
    for i, x in enumerate(rows):
        if x["block_height"] != i:
            heights_ok = False
        ts = x["created_at"].replace(tzinfo=cst).isoformat(timespec="seconds")
        ph = gm.payload_hash(x["payload_snapshot"] if not isinstance(x["payload_snapshot"], str) else json.loads(x["payload_snapshot"]))
        if x["prev_hash"] != prev or gm.block_hash(x["prev_hash"], x["payload_hash"], ts) != x["block_hash"]:
            broken.append(x["evidence_id"])
        if ph != x["payload_hash"]:
            tampered.append(x["evidence_id"])
        prev = x["block_hash"]
    C("API-DB-07", "文档(二) 存证链 SM3 重算 / DB-SCHEMA block_hash=SM3(prev+payload_hash+ts)", f"Python 逐条重算 {len(rows)} 条：prev_hash 衔接 + block_hash 重算", "断链 0；高度 0..N-1 连续",
      [(not broken, f"断链 {broken[:3]}"), (heights_ok, "高度不连续")], {"total": len(rows), "broken": broken[:3], "last": rows[-1]["block_hash"][:24] if rows else None})
    C("API-DB-07b", "文档(二)4 payload_snapshot 重算 SM3 与 payload_hash 比对", f"Python 逐条 canonical_json+SM3 重算 {len(rows)} 条", "不一致 0（其他 Agent 未还原的 tamper 演示会在此出现，均带 pvOutput=999.9 标记）",
      [(not tampered, f"{len(tampered)} 条快照与摘要不一致 {tampered[:3]}")], {"tampered": tampered[:5]})
    rows = db_query("SELECT COUNT(*) n FROM chain_evidence a JOIN chain_evidence b ON a.block_height=b.block_height+1 WHERE a.prev_hash<>b.block_hash")
    C("API-DB-08", "验证清单 1.3 SQL 断链检查", "JOIN 比对 prev_hash", "断链数 0", [(rows[0]["n"] == 0, str(rows))], rows)
    rows = db_query("SELECT 'sys_user' t, COUNT(*) n FROM sys_user WHERE username NOT LIKE 'test-%%' UNION ALL SELECT 'sys_role_permission', COUNT(*) FROM sys_role_permission WHERE role_code NOT LIKE 'test_%%' "
                    "UNION ALL SELECT 'energy_asset', COUNT(*) FROM energy_asset WHERE id<=1080 UNION ALL SELECT 'node_info', COUNT(*) FROM node_info UNION ALL SELECT 'node_metric', COUNT(*) FROM node_metric "
                    "UNION ALL SELECT 'audit_log_202608_seed', COUNT(*) FROM audit_log_202608 WHERE id<=160 UNION ALL SELECT 'perm_application', COUNT(*) FROM perm_application WHERE id<=12 UNION ALL SELECT 'algo_fl_round_seed', COUNT(*) FROM algo_fl_round WHERE id<=10")
    got = {r["t"]: r["n"] for r in rows}
    exp = {"sys_user": 6, "sys_role_permission": 35, "energy_asset": 80, "node_info": 4, "node_metric": 2880, "audit_log_202608_seed": 160, "perm_application": 12, "algo_fl_round_seed": 10}
    diff = {k: (got.get(k), v) for k, v in exp.items() if got.get(k) != v}
    C("API-DB-09", "验证清单 1.1 种子数据数量", "按表计数（排除 test- 实体）", str(exp), [(not diff, f"差异 {diff}")], got)
    rows = db_query("SELECT username, password_hash FROM sys_user WHERE username IN ('admin','grid','vpp','subject','regulator','edge')")
    import bcrypt
    okk = all(r["password_hash"].startswith("$2") and bcrypt.checkpw(ACCOUNTS[r["username"]].encode(), r["password_hash"].encode()) for r in rows)
    C("API-DB-10", "DB-SCHEMA 密码 bcrypt 存储（6 演示账号可验）", "bcrypt.checkpw 逐个校验", "6 个均 $2b$ 且可验", [(len(rows) == 6 and okk, "校验失败")], {r["username"]: r["password_hash"][:7] for r in rows})
    rows = db_query("SELECT COUNT(*) n, SUM(private_key_enc IS NULL OR private_key_enc='') nulls, SUM(custody=1) cust FROM did_key")
    r2 = db_query("SELECT COUNT(*) n FROM did_key WHERE private_key_enc REGEXP '^[0-9a-f]{64}$'")
    C("API-DB-11", "私钥不明文落库（全表）", "did_key.private_key_enc 全表扫描", "托管行为密文（非 64hex 裸私钥），非托管行为 NULL", [(r2[0]["n"] == 0, f"{r2[0]['n']} 行疑似 64hex 明文")], {**rows[0], "hex64_rows": r2[0]["n"]})
    rows = db_query("SELECT username, real_name, org_name FROM sys_user WHERE username='admin'")
    C("API-DB-12", "验证清单 1.2 utf8mb4 中文", "sys_user admin", "系统管理员/平台运营方", [(rows[0]["real_name"] == "系统管理员" and rows[0]["org_name"] == "平台运营方", str(rows))], rows)
    rows = db_query("SELECT did, JSON_EXTRACT(did_document,'$.verificationMethod[0].type') t FROM did_identity LIMIT 1")
    C("API-DB-13", "验证清单 1.5 JSON 列可查", "JSON_EXTRACT did_document", "SM2VerificationKey2023", [("SM2VerificationKey2023" in str(rows), str(rows))], rows)
    rows = db_query("SELECT table_name t FROM information_schema.tables WHERE table_schema='energy_tds' AND table_name LIKE 'audit_log_%%' ORDER BY 1")
    cur = dt.date.today().strftime("audit_log_%Y%m")
    C("API-DB-14", "DB-SCHEMA 按月分表，当月表存在", "列出 audit_log_* 表", f"含 {cur}", [(cur in {r["t"] for r in rows}, str(rows))], rows)
    rows = db_query("SELECT COUNT(*) n FROM did_device_binding")
    C("API-DB-15", "文档(一)5 设备身份绑定关系表", "did_device_binding 行数", "≥4（四节点 edge DID 绑定）", [(rows[0]["n"] >= 4, str(rows))], rows)
    rows = db_query("SELECT COUNT(*) n FROM audit_log_202608 WHERE trace_id IS NULL OR trace_id=''")
    C("API-DB-16", "文档(四)1 日志携带 TraceID", "audit_log trace_id 空值计数", "0", [(rows[0]["n"] == 0, str(rows))], rows)


if __name__ == "__main__":
    only = sys.argv[1:]  # 可选：只跑指定节，如 auth did（复用上次 ctx.json 里的实体）
    if only and os.path.exists(os.path.join(OUT, "ctx.json")):
        CTX.update(json.load(open(os.path.join(OUT, "ctx.json"))))
        R.out_path = os.path.join(OUT, "run_" + "_".join(only) + ".json")
    for name, fn in [("auth", sec_auth), ("users", sec_users), ("did", sec_did), ("keys", sec_keys), ("assets", sec_assets), ("perm", sec_perm),
                     ("evidence", sec_evidence), ("audit", sec_audit), ("nodes", sec_nodes), ("fl", sec_fl), ("dispatch", sec_dispatch),
                     ("ai", sec_ai_risk), ("security", sec_security), ("db", sec_db)]:
        if only and name not in only:
            continue
        print(f"\n===== {name} =====")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback; traceback.print_exc()
            R.case(f"API-{name.upper()}-ERR", "脚本异常", name, "-", [], blocked=f"脚本异常：{exc}")
    R.save()
    if not only:
        json.dump(CTX, open(os.path.join(OUT, "ctx.json"), "w"), ensure_ascii=False, indent=1, default=str)
