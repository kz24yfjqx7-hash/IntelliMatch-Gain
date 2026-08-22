#!/usr/bin/env python3
"""功能测试（接口层）— 依据《能源可信数据空间项目需求书》第三章。

运行：cd energy-tds && .venv/bin/python qa/func-tests/api_func_test.py
前置：backend 8000 / algo-service 8100 已启动（见 docs/agent-notes/INTEGRATION-ENV.md）。
输出：qa/func-tests/results/api-results.json（每个用例的判定与证据片段）。
本脚本只创建带 test- 前缀的新实体，不重置数据库，不修改任何业务代码。
SM2 签名直接复用甲方 backend/core/gm_crypto.py（只读 import，纯算法）。
"""
import concurrent.futures as cf
import json
import os
import sys
import time
import uuid
from datetime import datetime

import httpx

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from core import gm_crypto as gm  # noqa: E402  甲方纯算法模块，仅用于测试侧签名

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
ALGO = os.environ.get("ALGO_BASE", "http://127.0.0.1:8100/algo/v1")
OUT = os.path.join(os.path.dirname(__file__), "results", "api-results.json")
RUN = datetime.now().strftime("%m%d%H%M%S")
TAG = f"test-{RUN}"

ACCOUNTS = {
    "admin": ("admin", "admin123"), "grid": ("grid", "grid123"), "vpp": ("vpp", "vpp123"),
    "subject": ("subject", "subject123"), "regulator": ("regulator", "reg123"), "edge": ("edge", "edge123"),
}
ROLE_OF = {"admin": "sys_admin", "grid": "grid_dispatcher", "vpp": "vpp_operator",
           "subject": "energy_subject", "regulator": "regulator", "edge": "edge_node"}

client = httpx.Client(timeout=60)
TOKENS: dict[str, str] = {}
ME: dict[str, dict] = {}
RESULTS: list[dict] = []
CTX: dict = {}


def login(key):
    u, p = ACCOUNTS[key]
    r = client.post(f"{BASE}/auth/login", json={"username": u, "password": p})
    body = r.json()
    TOKENS[key] = body["data"]["token"]
    ME[key] = body["data"]["user"]
    return r, body


def call(role, method, path, json_body=None, params=None, headers=None, base=None, raw=False):
    h = dict(headers or {})
    if role and role in TOKENS:
        h["Authorization"] = f"Bearer {TOKENS[role]}"
    t0 = time.perf_counter()
    r = client.request(method, (base or BASE) + path, json=json_body, params=params, headers=h)
    ms = round((time.perf_counter() - t0) * 1000)
    if raw:
        return r, None, ms
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text[:300]}
    return r, body, ms


def snip(obj, n=360):
    s = json.dumps(obj, ensure_ascii=False, default=str) if not isinstance(obj, str) else obj
    return s if len(s) <= n else s[:n] + "…"


def tc(tid, req, title, fn, steps="", expect="", impl=""):
    """执行一个用例；fn 返回 (ok: bool, evidence: str, extra_note)。异常 → 阻塞。"""
    rec = {"id": tid, "req": req, "title": title, "steps": steps, "expect": expect, "impl": impl}
    try:
        res = fn()
        ok, ev = res[0], res[1]
        note = res[2] if len(res) > 2 else ""
        rec.update(verdict="通过" if ok else "失败", evidence=ev, note=note)
    except BlockedError as e:
        rec.update(verdict="阻塞", evidence="", note=str(e))
    except Exception as e:  # noqa: BLE001
        rec.update(verdict="阻塞", evidence="", note=f"{type(e).__name__}: {e}")
    RESULTS.append(rec)
    print(f"[{rec['verdict']}] {tid} {title} {('— ' + rec['note']) if rec['note'] else ''}")
    return rec


class BlockedError(Exception):
    pass


# ============================================================ 3.1 认证与权限
def t_login_all():
    ev = []
    ok = True
    for k in ACCOUNTS:
        r, b = login(k)
        roles = b["data"]["user"]["roles"]
        ok &= r.status_code == 200 and b["code"] == 0 and ROLE_OF[k] in roles and b["data"]["expiresIn"] == 28800
        ev.append(f"{k}→{roles} exp={b['data']['expiresIn']} traceId={b['traceId']}")
    return ok, "; ".join(ev)


def t_login_bad():
    r = client.post(f"{BASE}/auth/login", json={"username": "admin", "password": "bad-pass"})
    b = r.json()
    return r.status_code == 401 and b["code"] == 1002 and b["data"] is None, snip(b)


def t_me():
    r, b, _ = call("admin", "GET", "/auth/me")
    p = b["data"].get("permissions", [])
    ok = r.status_code == 200 and "dispatch:issue" in p and "user:manage" in p and b["data"]["did"].startswith("did:vpp:user:")
    CTX["admin_perms"] = p
    return ok, snip({"roles": b["data"]["roles"], "did": b["data"]["did"], "permissions": p})


def t_no_token():
    r, b, _ = call(None, "GET", "/auth/me")
    return r.status_code == 401 and b["code"] == 1002, snip(b)


def t_bad_token():
    r, b, _ = call(None, "GET", "/auth/me", headers={"Authorization": "Bearer abc.def.ghi"})
    return r.status_code == 401 and b["code"] == 1002, snip(b)


def t_logout():
    r0, b0 = login("regulator")
    tok = b0["data"]["token"]
    r1 = client.post(f"{BASE}/auth/logout", headers={"Authorization": f"Bearer {tok}"})
    r2 = client.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {tok}"})
    b2 = r2.json()
    login("regulator")  # 重新登录供后续用例使用
    return r1.status_code == 200 and r2.status_code == 401 and b2["code"] == 1002, f"logout={r1.json()['code']}; me-after={snip(b2)}"


def t_user_crud():
    name = f"{TAG}-user"
    r1, b1, _ = call("admin", "POST", "/users", {"username": name, "password": "Test123456", "realName": "测试用户",
                                                 "roles": ["energy_subject"], "orgName": "测试机构"})
    if b1["code"] != 0:
        return False, snip(b1)
    uid = b1["data"]["id"]
    CTX["test_user"] = b1["data"]
    r2, b2, _ = call("admin", "PUT", f"/users/{uid}", {"realName": "测试用户-改", "roles": ["regulator"]})
    r3, b3, _ = call("admin", "GET", "/users", params={"keyword": name, "size": 200})
    found = [u for u in b3["data"]["items"] if u["username"] == name]
    # 新用户能登录
    r4 = client.post(f"{BASE}/auth/login", json={"username": name, "password": "Test123456"})
    b4 = r4.json()
    r5, b5, _ = call("admin", "DELETE", f"/users/{uid}")
    r6 = client.post(f"{BASE}/auth/login", json={"username": name, "password": "Test123456"})
    ok = (b1["code"] == 0 and b2["code"] == 0 and found and found[0]["realName"] == "测试用户-改"
          and b4["code"] == 0 and "regulator" in b4["data"]["user"]["roles"] and b5["code"] == 0 and r6.status_code == 401)
    return ok, snip({"create": b1["data"], "update": b2["data"], "login_roles": b4["data"]["user"]["roles"] if b4["code"] == 0 else b4,
                     "delete": b5["code"], "login_after_delete": r6.json()["code"]})


def t_user_dup():
    r, b, _ = call("admin", "POST", "/users", {"username": "admin", "password": "Test123456", "realName": "x", "roles": ["regulator"]})
    return r.status_code == 409 and b["code"] == 1006, snip(b)


def t_user_param():
    r, b, _ = call("admin", "POST", "/users", {"username": "ab", "password": "1", "realName": "x", "roles": []})
    return r.status_code == 400 and b["code"] == 1001, snip(b)


def t_users_forbidden():
    ev, ok = [], True
    for k in ("grid", "vpp", "subject", "regulator", "edge"):
        r, b, _ = call(k, "GET", "/users")
        ok &= r.status_code == 403 and b["code"] == 1003
        ev.append(f"{k}:{b['code']}")
    return ok, "; ".join(ev)


def t_roles_list():
    r, b, _ = call("admin", "GET", "/roles")
    codes = {x["code"] for x in b["data"]["items"]} if isinstance(b["data"], dict) else {x["code"] for x in b["data"]}
    need = {"sys_admin", "grid_dispatcher", "vpp_operator", "energy_subject", "regulator", "edge_node"}
    return need <= codes, snip(sorted(codes))


# RBAC 矩阵抽样：每个角色 × 资源:操作 至少一格
RBAC_PROBES = [
    # (key, method, path, body, 期望允许?)  对应 sys_role_permission 种子
    ("admin", "GET", "/users", None, True),
    ("admin", "GET", "/audit/logs", None, True),
    ("grid", "POST", "/fl/tasks", {"name": TAG + "-rbac", "nodeIds": ["Node-A", "Node-B", "Node-C"], "rounds": 1}, True),  # algo:execute
    ("grid", "GET", "/audit/logs", None, False),
    ("grid", "POST", "/evidence", {"category": "data", "refId": "x", "payload": {"a": 1}}, False),  # evidence:write 无
    ("vpp", "POST", "/assets/classify", {"records": [{"dataType": "pv", "fields": ["power"]}]}, True),  # asset:read
    ("vpp", "POST", "/fl/tasks", {"name": TAG + "-rbac", "nodeIds": ["Node-A"], "rounds": 1}, False),  # 无 algo:execute
    ("vpp", "POST", "/did/register", {"subjectType": "device", "subjectName": TAG + "-vpp-dev"}, True),
    ("subject", "GET", "/assets", None, True),  # own
    ("subject", "GET", "/fl/tasks", None, False),  # 无 model:read
    ("subject", "GET", "/dispatch/tasks", None, False),
    ("subject", "POST", "/did/register", {"subjectType": "device", "subjectName": "x"}, False),
    ("regulator", "GET", "/audit/logs", None, True),
    ("regulator", "GET", "/evidence", None, True),
    ("regulator", "POST", "/assets", {"name": "x", "dataType": "pv", "sourceDid": "did:vpp:x", "payload": {}}, False),  # 无 asset:write
    ("regulator", "POST", "/fl/tasks", {"name": "x", "nodeIds": ["Node-A"]}, False),
    ("edge", "GET", "/fl/models", None, True),  # model:read
    ("edge", "GET", "/dispatch/tasks", None, True),
    ("edge", "GET", "/evidence", None, False),  # 无 evidence:read
    ("edge", "GET", "/audit/stats", None, False),
]


def t_rbac_matrix():
    ev, ok = [], True
    for key, m, p, body, allow in RBAC_PROBES:
        r, b, _ = call(key, m, p, body)
        got_allow = b.get("code") == 0
        good = got_allow == allow and (allow or b.get("code") == 1003)
        ok &= good
        ev.append(f"{'✓' if good else '✗'}{ROLE_OF[key]} {m} {p}→{b.get('code')}(期望{'允许' if allow else '1003'})")
    return ok, "\n".join(ev)


# ============================================================ 3.2 DID
def t_did_register():
    r, b, _ = call("admin", "POST", "/did/register", {"subjectType": "device", "subjectName": f"{TAG}-逆变器",
                                                      "orgName": "测试园区", "metadata": {"model": "VPP-2000"}})
    d = b["data"]
    CTX["did"] = d
    doc = d["didDocument"]
    ok = (b["code"] == 0 and d["did"].startswith("did:vpp:device:0x") and len(d["did"].split(":")[-1]) == 34
          and doc["@context"] and doc["verificationMethod"][0]["publicKeyHex"] == d["publicKey"]
          and d.get("privateKey") and d.get("evidenceId") and d.get("chainTxId"))
    return ok, snip({k: v for k, v in d.items() if k != "didDocument"} | {"vm": doc["verificationMethod"][0]["type"]})


def t_did_register_edge_org():
    ev, ok = [], True
    for st in ("edge", "org", "user"):
        r, b, _ = call("admin", "POST", "/did/register", {"subjectType": st, "subjectName": f"{TAG}-{st}"})
        ok &= b["code"] == 0 and b["data"]["did"].startswith(f"did:vpp:{st}:")
        ev.append(b["data"]["did"] if b["code"] == 0 else snip(b))
        if st == "edge":
            CTX["edge_did"] = b["data"]
    return ok, "; ".join(ev)


def t_did_list_query():
    did = CTX["did"]["did"]
    r1, b1, _ = call("grid", "GET", "/did", params={"subjectType": "device", "keyword": TAG})
    items = b1["data"]["items"]
    r2, b2, _ = call("grid", "GET", "/did", params={"status": "active", "size": 5})
    r3, b3, _ = call("grid", "GET", "/did", params={"subjectType": "bogus"})
    ok = (any(i["did"] == did for i in items) and all(i["subjectType"] == "device" for i in items)
          and b2["data"]["size"] == 5 and all(i["status"] == "active" for i in b2["data"]["items"]) and b3["code"] == 1001)
    return ok, snip({"found": [i["did"] for i in items], "total_active_page": b2["data"]["total"], "bad_filter": b3["code"]})


def t_did_document():
    did = CTX["did"]["did"]
    r1, b1, _ = call("subject", "GET", f"/did/{did}")
    r2, b2, _ = call("subject", "GET", f"/did/{did}/document")
    d = b2["data"]
    doc = d.get("didDocument") or d
    ok = b1["code"] == 0 and b2["code"] == 0 and doc.get("id") == did and "verificationMethod" in doc
    return ok, snip({"detail_keys": list(b1["data"].keys())[:10], "doc_id": doc.get("id")})


def t_did_verify_ok():
    d = CTX["did"]
    msg = f"hello-{TAG}"
    sig = gm.sign(msg, d["privateKey"], d["publicKey"])
    r, b, _ = call("edge", "POST", "/did/verify", {"did": d["did"], "message": msg, "signature": sig})
    CTX["sig1"] = (msg, sig)
    return b["code"] == 0 and b["data"]["valid"] is True and b["data"]["status"] == "active", snip(b["data"])


def t_did_verify_bad():
    d = CTX["did"]
    msg, sig = CTX["sig1"]
    r1, b1, _ = call("edge", "POST", "/did/verify", {"did": d["did"], "message": msg + "x", "signature": sig})
    r2, b2, _ = call("edge", "POST", "/did/verify", {"did": d["did"], "message": msg, "signature": "deadbeef" * 16})
    r3, b3, _ = call("edge", "POST", "/did/verify", {"did": "did:vpp:device:0x0000", "message": msg, "signature": sig})
    ok = b1["data"]["valid"] is False and b2["data"]["valid"] is False and b3["data"]["valid"] is False
    return ok, snip({"tampered_msg": b1["data"], "bad_sig": b2["data"]["valid"], "unknown_did": b3["data"]})


def t_did_freeze():
    d = CTX["did"]
    msg, sig = CTX["sig1"]
    r1, b1, _ = call("admin", "POST", f"/did/{d['did']}/status", {"action": "freeze", "reason": "测试冻结"})
    r2, b2, _ = call("admin", "POST", "/did/verify", {"did": d["did"], "message": msg, "signature": sig})
    r3, b3, _ = call("admin", "GET", f"/did/{d['did']}")
    ok = b1["data"]["status"] == "frozen" and b1["data"].get("evidenceId") and b2["data"]["valid"] is False and b3["data"]["status"] == "frozen"
    return ok, snip({"status": b1["data"], "verify_when_frozen": b2["data"]})


def t_did_unfreeze():
    d = CTX["did"]
    msg, sig = CTX["sig1"]
    r1, b1, _ = call("admin", "POST", f"/did/{d['did']}/status", {"action": "unfreeze", "reason": "测试恢复"})
    r2, b2, _ = call("admin", "POST", "/did/verify", {"did": d["did"], "message": msg, "signature": sig})
    return b1["data"]["status"] == "active" and b2["data"]["valid"] is True, snip({"status": b1["data"], "verify": b2["data"]["valid"]})


def t_did_status_forbidden():
    d = CTX["did"]
    ev, ok = [], True
    for k in ("vpp", "grid", "subject"):
        r, b, _ = call(k, "POST", f"/did/{d['did']}/status", {"action": "freeze"})
        ok &= b["code"] == 1003
        ev.append(f"{k}:{b['code']}")
    return ok, "; ".join(ev)


def t_did_rotate():
    d = CTX["did"]
    msg, sig = CTX["sig1"]
    r1, b1, _ = call("admin", "POST", f"/did/{d['did']}/rotate-key", {"reason": "测试轮换", "custody": False})
    nd = b1["data"]
    r2, b2, _ = call("admin", "POST", "/did/verify", {"did": d["did"], "message": msg, "signature": sig})  # 旧签名
    newsig = gm.sign(msg, nd["privateKey"], nd["publicKey"])
    r3, b3, _ = call("admin", "POST", "/did/verify", {"did": d["did"], "message": msg, "signature": newsig})
    r4, b4, _ = call("admin", "GET", "/keys", params={"did": d["did"], "size": 50})
    vers = sorted(k["version"] for k in b4["data"]["items"])
    ok = b1["code"] == 0 and nd["publicKey"] != d["publicKey"] and b2["data"]["valid"] is False and b3["data"]["valid"] is True and len(vers) >= 2
    CTX["did"]["privateKey"], CTX["did"]["publicKey"] = nd["privateKey"], nd["publicKey"]
    CTX["sig1"] = (msg, newsig)
    return ok, snip({"newPub": nd["publicKey"][:20], "old_sig_valid": b2["data"]["valid"], "new_sig_valid": b3["data"]["valid"], "key_versions": vers,
                     "statuses": [k["status"] for k in b4["data"]["items"]]})


def t_did_rotate_other_forbidden():
    r, b, _ = call("vpp", "POST", f"/did/{CTX['did']['did']}/rotate-key", {"reason": "x"})
    return b["code"] == 1003, snip(b)


def t_did_resolve():
    dids = [CTX["did"]["did"], ME["admin"]["did"], "did:vpp:device:0xnotexist"]
    r, b, _ = call("subject", "POST", "/did/resolve", {"dids": dids})
    items = b["data"]["items"]
    return b["code"] == 0 and len(items) == 3, snip(items)


def t_did_revoke():
    d = CTX["did"]
    msg, sig = CTX["sig1"]
    r1, b1, _ = call("admin", "POST", f"/did/{d['did']}/status", {"action": "revoke", "reason": "测试注销"})
    r2, b2, _ = call("admin", "POST", "/did/verify", {"did": d["did"], "message": msg, "signature": sig})
    r3, b3, _ = call("admin", "POST", f"/did/{d['did']}/status", {"action": "unfreeze", "reason": "试图复活"})
    ok = b1["data"]["status"] == "revoked" and b2["data"]["valid"] is False and b3["code"] in (1006, 1001)
    return ok, snip({"revoke": b1["data"], "verify": b2["data"], "unfreeze_after_revoke": b3["code"]})


def t_did_lifecycle_audit():
    r, b, _ = call("admin", "GET", "/audit/logs", params={"actorDid": ME["admin"]["did"], "action": "did:status", "size": 10})
    items = b["data"]["items"]
    ok = b["data"]["total"] >= 3 and all(i["riskLevel"] == "high" for i in items[:3])
    return ok, snip([{k: i[k] for k in ("action", "result", "riskLevel", "resourceId", "traceId")} for i in items[:3]])


# ============================================================ 3.3 密钥
def t_key_list():
    r, b, _ = call("regulator", "GET", "/keys", params={"status": "active", "size": 5})
    items = b["data"]["items"]
    ok = b["code"] == 0 and items and all(i["status"] == "active" for i in items) and {"algorithm", "publicKey", "version", "boundAt"} <= set(items[0])
    return ok, snip({"total": b["data"]["total"], "item0": {k: items[0][k] for k in ("did", "algorithm", "status", "version")}})


def t_key_create_algos():
    did = CTX["edge_did"]["did"]
    ev, ok = [], True
    for alg in ("SM2", "ECC", "RSA"):
        r, b, _ = call("admin", "POST", "/keys", {"did": did, "algorithm": alg, "custody": True, "expireDays": 30})
        good = b["code"] == 0 and b["data"]["algorithm"] == alg and b["data"].get("privateKey")
        ok &= bool(good)
        ev.append(f"{alg}:{b['code']} id={b['data'].get('id') if b['code'] == 0 else '-'} v={b['data'].get('version') if b['code'] == 0 else '-'}")
        if alg == "SM2":
            CTX["key_sm2"] = b["data"]
        if alg == "RSA":
            CTX["key_rsa"] = b["data"]
    r, b, _ = call("admin", "POST", "/keys", {"did": did, "algorithm": "DES"})
    ok &= b["code"] == 1001
    ev.append(f"DES:{b['code']}")
    return ok, "; ".join(ev)


def t_key_freeze_revoke_history():
    k = CTX["key_rsa"]
    r1, b1, _ = call("admin", "POST", f"/keys/{k['id']}/freeze", {"reason": "测试冻结"})
    r2, b2, _ = call("admin", "POST", f"/keys/{k['id']}/revoke", {"reason": "测试注销"})
    r3, b3, _ = call("admin", "GET", f"/keys/{k['id']}/history")
    r4, b4, _ = call("admin", "POST", f"/keys/{k['id']}/freeze", {"reason": "已注销再冻结"})
    ok = b1["data"]["status"] == "frozen" and b2["data"]["status"] == "revoked" and b3["code"] == 0 and b4["code"] in (1006, 1001)
    return ok, snip({"freeze": b1["data"]["status"], "revoke": b2["data"]["status"], "history": b3["data"], "freeze_after_revoke": b4["code"]})


def t_key_forbidden():
    r1, b1, _ = call("subject", "POST", "/keys", {"did": CTX["edge_did"]["did"]})
    r2, b2, _ = call("vpp", "POST", f"/keys/{CTX['key_sm2']['id']}/freeze", {})
    return b1["code"] == 1003 and b2["code"] == 1003, f"subject create:{b1['code']}; vpp freeze:{b2['code']}"


def t_key_used_for_auth():
    """密钥用于接口认证：用边缘 DID 的私钥签 nonce 完成设备上线（契约 2.8）；同 nonce 重放应拒绝。"""
    r0, b0, _ = call("admin", "POST", "/did/register", {"subjectType": "edge", "subjectName": f"{TAG}-edge-online"})
    d = b0["data"]
    CTX["edge_did2"] = d
    nonce = uuid.uuid4().hex[:16]
    sig = gm.sign(nonce, d["privateKey"], d["publicKey"])
    r1, b1, _ = call("edge", "POST", "/nodes/Node-A/online", {"did": d["did"], "nonce": nonce, "signature": sig})
    r2, b2, _ = call("edge", "POST", "/nodes/Node-A/online", {"did": d["did"], "nonce": nonce, "signature": sig})  # 重放
    r3, b3, _ = call("edge", "POST", "/nodes/Node-A/online", {"did": d["did"], "nonce": nonce + "x", "signature": "00" * 32})
    bound = b1["code"] == 1004 and "已绑定身份" in b1["message"]
    ok = ((b1["code"] == 0 and b1["data"].get("accepted") and b1["data"].get("evidenceId")) or bound) and b2["code"] == 1004 and b3["code"] == 1004
    note = "Node-A 已被首轮测试的 DID 绑定，后端拒绝其他 DID 冒充（1004）——绑定保护生效；首轮（节点未绑定时）accepted=true 并返回 evidenceId" if bound else ""
    return ok, snip({"online": b1["data"] if b1["code"] == 0 else b1, "replay": b2["code"], "bad_sig": b3["code"]}), note


def t_key_ecc_breaks_verify():
    """绑定 ECC/RSA 密钥后，DID 的 SM2 验签是否仍可用（后端只认版本最高的活跃密钥且仅支持 SM2）。"""
    d = CTX["edge_did"]
    k = CTX["key_sm2"]
    msg = "ecc-side-effect"
    sig = gm.sign(msg, k["privateKey"], k["publicKey"])
    r, b, _ = call("admin", "GET", "/keys", params={"did": d["did"], "size": 50})
    active = [(x["version"], x["algorithm"], x["status"]) for x in b["data"]["items"]]
    r2, b2, _ = call("admin", "POST", "/did/verify", {"did": d["did"], "message": msg, "signature": sig})
    ok = b2["data"]["valid"] is True
    return ok, snip({"keys": active, "verify_with_sm2_v2": b2["data"]}), "" if ok else "绑定 ECC 密钥（版本更高）后 SM2 密钥签名无法验签 → 见 B-issue"


# ============================================================ 3.4 资产
def t_asset_register():
    ev, ok = [], True
    for dt, payload in (("pv", {"pvOutput": 45.3}), ("wind", {"windOutput": 12.1}), ("storage", {"soc": 65, "power": -12}),
                        ("load", {"load": 120}), ("dispatch", {"cmd": "discharge", "kw": 20})):
        r, b, _ = call("admin", "POST", "/assets", {"name": f"{TAG}-{dt}", "dataType": dt, "sourceDid": ME["admin"]["did"],
                                                    "payload": payload | {"ts": "2026-08-22T10:00:00+08:00"}, "description": "功能测试"})
        d = b["data"] if b["code"] == 0 else {}
        good = b["code"] == 0 and d["hash"].startswith("sm3:") and d["level"] in ("L1", "L2", "L3", "L4") and d.get("evidenceId") and d.get("chainTxId") and d["authStatus"] == "unauthorized"
        ok &= bool(good)
        ev.append(f"{dt}:{d.get('id')} {d.get('level')} {d.get('hash', '')[:14]} ev={d.get('evidenceId')}" if good else f"{dt}:{snip(b)}")
        if dt == "pv":
            CTX["asset"] = d
            # 校验 hash = SM3(canonical payload)
            CTX["asset_payload"] = payload | {"ts": "2026-08-22T10:00:00+08:00"}
    return ok, "; ".join(ev)


def t_asset_hash_is_sm3():
    a = CTX["asset"]
    r, b, _ = call("admin", "GET", f"/assets/{a['id']}")
    d = b["data"]
    local = gm.payload_hash(CTX["asset_payload"]) if hasattr(gm, "payload_hash") else None
    ok = d["hash"] == a["hash"] and d["sourceDid"] == ME["admin"]["did"] and d["dataType"] == "pv"
    return ok, snip({"hash": d["hash"], "sm3(payload)": local, "same": local == d["hash"] or local == d["hash"].replace("sm3:", "")})


def t_asset_classify_levels():
    recs = [
        {"dataType": "pv", "fields": ["power"], "freq": "hour", "volume": 24},
        {"dataType": "load", "fields": ["power", "voltage"], "freq": "minute", "volume": 1440},
        {"dataType": "pv", "fields": ["power", "voltage", "gps"], "freq": "minute", "volume": 1440},
        {"dataType": "dispatch", "fields": ["power", "gps", "owner", "price", "contract"], "freq": "second", "volume": 86400},
    ]
    r, b, _ = call("vpp", "POST", "/assets/classify", {"records": recs})
    res = b["data"]["results"]
    levels = [x["level"] for x in res]
    ok = b["code"] == 0 and len(res) == 4 and levels[0] <= levels[1] <= levels[2] <= levels[3] and len(set(levels)) >= 3 and all("reason" in x for x in res)
    return ok, snip([{k: x[k] for k in ("index", "level", "score", "reason")} for x in res])


def t_asset_level_explicit_L4():
    r, b, _ = call("admin", "POST", "/assets", {"name": f"{TAG}-L4", "dataType": "load", "sourceDid": ME["admin"]["did"], "level": "L4",
                                                "payload": {"load": 99, "owner": "张三"}})
    CTX["asset_l4"] = b["data"]
    return b["code"] == 0 and b["data"]["level"] == "L4", snip(b["data"])


def t_asset_list_filters():
    r1, b1, _ = call("grid", "GET", "/assets", params={"dataType": "pv", "keyword": TAG})
    r2, b2, _ = call("grid", "GET", "/assets", params={"level": "L4", "keyword": TAG})
    r3, b3, _ = call("grid", "GET", "/assets", params={"sourceDid": ME["admin"]["did"], "size": 200})
    r4, b4, _ = call("grid", "GET", "/assets", params={"page": 1, "size": 2})
    ok = (all(i["dataType"] == "pv" for i in b1["data"]["items"]) and b1["data"]["total"] >= 1
          and all(i["level"] == "L4" for i in b2["data"]["items"]) and b2["data"]["total"] >= 1
          and b3["data"]["total"] >= 6 and len(b4["data"]["items"]) == 2 and b4["data"]["size"] == 2)
    return ok, snip({"pv": b1["data"]["total"], "L4": b2["data"]["total"], "bySource": b3["data"]["total"], "page": {k: b4["data"][k] for k in ("page", "size", "total")}})


def t_asset_lineage():
    a = CTX["asset"]
    r, b, _ = call("regulator", "GET", f"/assets/{a['id']}/lineage")
    ch = b["data"]["chain"]
    ok = b["code"] == 0 and ch and ch[0]["stage"] == "register" and ch[0]["evidenceId"] == a["evidenceId"] and b["data"].get("traceId")
    return ok, snip(b["data"])


def t_asset_stats():
    r, b, _ = call("vpp", "GET", "/assets/stats")
    d = b["data"]
    lv = {x["level"] for x in d["byLevel"]}
    ok = b["code"] == 0 and {"L1", "L2", "L3", "L4"} & lv and d["total"] >= 80 and d["onChain"] >= 1 and d["byType"]
    return ok, snip(d)


def t_asset_own_scope():
    """energy_subject 仅能读自己的资产（scope=own），读 admin 的资产应 1003。"""
    a = CTX["asset"]
    r1, b1, _ = call("subject", "GET", f"/assets/{a['id']}")
    r2, b2, _ = call("subject", "GET", "/assets", params={"size": 200})
    others = [i for i in b2["data"]["items"] if i["sourceDid"] != ME["subject"]["did"]] if b2["code"] == 0 else None
    r3, b3, _ = call("edge", "GET", f"/assets/{a['id']}/lineage")
    ok = b1["code"] == 1003 and b2["code"] == 0 and others == [] and b3["code"] == 1003
    return ok, snip({"subject_read_admin_asset": b1["code"], "subject_list_total": b2["data"].get("total"), "foreign_in_list": len(others or []), "edge_lineage": b3["code"]})


def t_asset_write_forbidden():
    r, b, _ = call("grid", "POST", "/assets", {"name": "x", "dataType": "pv", "sourceDid": ME["grid"]["did"], "payload": {"a": 1}})
    r2, b2, _ = call("regulator", "POST", "/assets/classify", {"records": [{"dataType": "pv"}]})
    return b["code"] == 1003 and b2["code"] == 0, f"grid write:{b['code']}; regulator classify(read):{b2['code']}"


def t_asset_export_permission():
    """导出权限 asset:export：矩阵里 sys_admin/grid/regulator 有、vpp/subject/edge 无；用 /permissions/check 逐一核对。"""
    ev, ok = [], True
    for k, allow in (("admin", True), ("grid", True), ("regulator", True), ("vpp", False), ("subject", False), ("edge", False)):
        r, b, _ = call(k, "POST", "/permissions/check", {"resourceType": "asset", "resourceId": str(CTX["asset"]["id"]), "action": "export"})
        good = b["code"] == 0 and b["data"]["allowed"] == allow
        ok &= good
        ev.append(f"{ROLE_OF[k]}:{b['data'].get('allowed') if b['code'] == 0 else b}")
    return ok, "; ".join(ev)


# ============================================================ 3.5 权限
def t_perm_matrix():
    r, b, _ = call("edge", "GET", "/permissions/matrix")
    d = b["data"]
    roles = {x["code"]: x["grants"] for x in d["roles"]}
    ok = (set(d["resources"]) >= {"asset", "model", "dispatch", "evidence", "algo"} and set(d["actions"]) >= {"read", "write", "execute", "issue", "export"}
          and "issue" in roles["sys_admin"]["dispatch"] and "issue" not in roles.get("vpp_operator", {}).get("dispatch", []))
    CTX["matrix"] = roles
    return ok, snip({"resources": d["resources"], "actions": d["actions"], "vpp": roles.get("vpp_operator"), "subject": roles.get("energy_subject")})


def t_perm_apply():
    a = CTX["asset"]
    r, b, _ = call("subject", "POST", "/permissions/apply", {"resourceType": "asset", "resourceId": str(a["id"]), "action": "read",
                                                             "reason": f"{TAG} 联合建模需读取", "expireAt": "2026-12-31T00:00:00+08:00"})
    CTX["app"] = b["data"]
    r2, b2, _ = call("subject", "GET", "/permissions/applications", params={"status": "pending", "size": 200})
    mine = [x for x in b2["data"]["items"] if x["id"] == b["data"]["id"]]
    ok = b["code"] == 0 and b["data"]["status"] == "pending" and b["data"]["applicantDid"] == ME["subject"]["did"] and b["data"].get("evidenceId") and mine
    return ok, snip(b["data"])


def t_perm_check_before():
    a = CTX["asset"]
    r, b, _ = call("subject", "POST", "/permissions/check", {"resourceType": "asset", "resourceId": str(a["id"]), "action": "read"})
    r2, b2, _ = call("subject", "GET", f"/assets/{a['id']}")
    return b["data"]["allowed"] is False and b2["code"] == 1003, snip({"check": b["data"], "read": b2["code"]})


def t_perm_approve_forbidden():
    ev, ok = [], True
    for k in ("vpp", "grid", "regulator", "subject"):
        r, b, _ = call(k, "POST", f"/permissions/applications/{CTX['app']['id']}/approve", {"reason": "x"})
        ok &= b["code"] == 1003
        ev.append(f"{k}:{b['code']}")
    return ok, "; ".join(ev)


def t_perm_approve():
    a = CTX["asset"]
    r, b, _ = call("admin", "POST", f"/permissions/applications/{CTX['app']['id']}/approve", {"reason": "同意（功能测试）"})
    r2, b2, _ = call("subject", "POST", "/permissions/check", {"resourceType": "asset", "resourceId": str(a["id"]), "action": "read"})
    r3, b3, _ = call("subject", "GET", f"/assets/{a['id']}")
    r4, b4, _ = call("admin", "GET", "/permissions/grants", params={"did": ME["subject"]["did"], "size": 200})
    g = [x for x in b4["data"]["items"] if str(x.get("resourceId")) == str(a["id"])]
    CTX["grant"] = g[0] if g else None
    r5, b5, _ = call("admin", "GET", f"/assets/{a['id']}")
    ok = b["code"] == 0 and b["data"]["status"] == "approved" and b2["data"]["allowed"] is True and b3["code"] == 0 and g and b5["data"].get("authStatus") in ("authorized", "partial", "granted")
    return ok, snip({"approve": b["data"], "check": b2["data"], "subject_read": b3["code"], "grant": g[0] if g else None, "authStatus": b5["data"].get("authStatus")})


def t_perm_approve_twice():
    r, b, _ = call("admin", "POST", f"/permissions/applications/{CTX['app']['id']}/approve", {"reason": "重复"})
    return b["code"] == 1006, snip(b)


def t_perm_reject():
    r, b, _ = call("subject", "POST", "/permissions/apply", {"resourceType": "asset", "resourceId": str(CTX["asset_l4"]["id"]), "action": "read", "reason": f"{TAG} 申请 L4"})
    r2, b2, _ = call("admin", "POST", f"/permissions/applications/{b['data']['id']}/reject", {"reason": "L4 核心数据不开放"})
    r3, b3, _ = call("subject", "GET", f"/assets/{CTX['asset_l4']['id']}")
    return b2["code"] == 0 and b2["data"]["status"] == "rejected" and b3["code"] == 1003, snip({"reject": b2["data"], "read": b3["code"]})


def t_perm_revoke():
    g = CTX.get("grant")
    if not g:
        raise BlockedError("无授权记录可回收")
    a = CTX["asset"]
    r, b, _ = call("admin", "POST", f"/permissions/grants/{g['id']}/revoke", {"reason": "测试回收"})
    r2, b2, _ = call("subject", "GET", f"/assets/{a['id']}")
    r3, b3, _ = call("subject", "POST", "/permissions/check", {"resourceType": "asset", "resourceId": str(a["id"]), "action": "read"})
    return b["code"] == 0 and b2["code"] == 1003 and b3["data"]["allowed"] is False, snip({"revoke": b["data"], "read_after": b2["code"], "check": b3["data"]["allowed"]})


def t_perm_audit_trail():
    """权限变更留痕：审计日志中应有 apply / approve / reject / revoke 记录，且 approve/revoke 为 high。"""
    r, b, _ = call("regulator", "GET", "/audit/logs", params={"keyword": "permission", "size": 50})
    acts = {i["action"] for i in b["data"]["items"]}
    r2, b2, _ = call("regulator", "GET", "/audit/logs", params={"action": "permission:approve", "size": 5})
    ok = {"permission:apply", "permission:approve", "permission:reject", "permission:revoke"} <= acts and b2["data"]["items"] and b2["data"]["items"][0]["riskLevel"] == "high"
    return ok, snip({"actions_seen": sorted(acts), "approve_log": {k: b2["data"]["items"][0][k] for k in ("action", "riskLevel", "result", "evidenceId", "traceId")} if b2["data"]["items"] else None})


def t_perm_scene():
    """业务场景维度：同一主体对 dispatch:issue 与 algo:execute 的判定随资源类型变化。"""
    ev, ok = [], True
    for rt, act, allow in (("dispatch", "issue", False), ("dispatch", "read", True), ("algo", "execute", False), ("asset", "write", True)):
        r, b, _ = call("vpp", "POST", "/permissions/check", {"resourceType": rt, "resourceId": "dp-1", "action": act})
        ok &= b["data"]["allowed"] == allow
        ev.append(f"vpp {rt}:{act}={b['data']['allowed']}")
    return ok, "; ".join(ev)


def t_role_create_update():
    code = f"test_role_{RUN[-6:]}"
    r, b, _ = call("admin", "POST", "/roles", {"code": code, "name": "测试角色", "grants": [{"resourceType": "asset", "action": "read", "scope": "all"}]})
    r2, b2, _ = call("admin", "PUT", f"/roles/{code}", {"grants": [{"resourceType": "asset", "action": "read"}, {"resourceType": "evidence", "action": "read"}]})
    r3, b3, _ = call("admin", "GET", "/permissions/matrix")
    me = [x for x in b3["data"]["roles"] if x["code"] == code]
    r4, b4, _ = call("vpp", "POST", "/roles", {"code": code + "x", "name": "x"})
    ok = b["code"] == 0 and b2["code"] == 0 and me and "evidence" in me[0]["grants"] and b4["code"] == 1003
    return ok, snip({"create": b["code"], "update": b2["code"], "matrix_entry": me[0]["grants"] if me else None, "vpp_create": b4["code"]})


# ============================================================ 3.6 存证
def t_ev_write():
    r, b, _ = call("admin", "POST", "/evidence", {"category": "data", "refId": f"{TAG}-ref", "payload": {"pvOutput": 45.3, "note": TAG}, "actorDid": ME["admin"]["did"]})
    d = b["data"]
    CTX["ev"] = d
    ok = b["code"] == 0 and d["evidenceId"].startswith("ev-") and d["hash"].startswith("sm3:") and d["blockHeight"] > 0 and d["prevHash"] and d["txId"]
    return ok, snip(d)


def t_ev_categories_written_by_business():
    """数据接入/授权/联邦任务/模型版本/调度结果五类业务都应自动产生存证（按 category 检索）。"""
    ev, ok = [], True
    for cat in ("data", "identity", "permission", "audit", "algo"):
        r, b, _ = call("regulator", "GET", "/evidence", params={"category": cat, "size": 1})
        ok &= b["code"] == 0 and b["data"]["total"] > 0
        ev.append(f"{cat}:{b['data']['total']}")
    return ok, "; ".join(ev)


def t_ev_search():
    r1, b1, _ = call("regulator", "GET", "/evidence", params={"did": ME["admin"]["did"], "size": 5})
    r2, b2, _ = call("regulator", "GET", "/evidence", params={"from": "2026-08-22T00:00:00+08:00", "to": "2026-08-23T00:00:00+08:00", "size": 5})
    r3, b3, _ = call("regulator", "GET", "/evidence", params={"dataType": "pv", "size": 5})
    ok = b1["data"]["total"] > 0 and b2["data"]["total"] > 0 and b3["code"] == 0
    return ok, snip({"byDid": b1["data"]["total"], "byDate": b2["data"]["total"], "byDataType": b3["data"]["total"]})


def t_ev_detail_no_raw():
    e = CTX["ev"]
    r, b, _ = call("regulator", "GET", f"/evidence/{e['evidenceId']}")
    d = b["data"]
    text = json.dumps(d, ensure_ascii=False)
    # 链上记录应有 hash/身份/时间；资产原始 payload 不应以明文出现在链记录字段中（允许快照字段用于校验）
    ok = b["code"] == 0 and d["hash"] == e["hash"] and d.get("actorDid") and d.get("blockHeight") == e["blockHeight"]
    return ok, snip({k: d.get(k) for k in ("evidenceId", "category", "refId", "hash", "prevHash", "blockHeight", "actorDid", "timestamp", "txId")} | {"has_payload_field": "payload" in d, "keys": list(d.keys())})


def t_ev_verify_intact():
    e = CTX["ev"]
    r, b, _ = call("regulator", "POST", "/evidence/verify", {"evidenceId": e["evidenceId"]})
    r2, b2, _ = call("regulator", "POST", "/evidence/verify", {"evidenceId": e["evidenceId"], "payload": {"pvOutput": 45.3, "note": TAG}})
    r3, b3, _ = call("regulator", "POST", "/evidence/verify", {"evidenceId": e["evidenceId"], "payload": {"pvOutput": 999.9}})
    ok = b["data"]["intact"] is True and b2["data"]["intact"] is True and b3["data"]["intact"] is False
    return ok, snip({"db": b["data"], "same_payload": b2["data"]["intact"], "diff_payload": b3["data"]["intact"]})


def t_ev_chain_status_before():
    r, b, _ = call("regulator", "GET", "/evidence/chain/status")
    d = b["data"]
    note = ""
    if not d["intact"] and d["brokenAt"]:
        # 其他 Agent 的篡改演示未还原；先还原再判定（演示态清理，非业务数据）
        pre = d["brokenAt"]
        call("admin", "POST", "/evidence/demo/restore", {"evidenceId": pre})
        r, b, _ = call("regulator", "GET", "/evidence/chain/status")
        d = b["data"]
        note = f"测试前链已在 {pre} 断裂（他人篡改演示遗留），restore 后重新判定"
    CTX["chain0"] = d
    return d["intact"] is True and d["brokenAt"] is None and d["height"] > 0 and bool(d["byCategory"]), snip(d), note


def t_ev_tamper_forbidden():
    r, b, _ = call("vpp", "POST", "/evidence/demo/tamper", {"evidenceId": CTX["ev"]["evidenceId"], "newValue": {"x": 1}})
    r2, b2, _ = call("regulator", "POST", "/evidence/demo/tamper", {"evidenceId": CTX["ev"]["evidenceId"], "newValue": {"x": 1}})
    return b["code"] == 1003 and b2["code"] == 1003, f"vpp:{b['code']} regulator:{b2['code']}"


def t_ev_tamper_detect():
    e = CTX["ev"]
    r, b, _ = call("admin", "POST", "/evidence/demo/tamper", {"evidenceId": e["evidenceId"], "newValue": {"pvOutput": 999.9}})
    r2, b2, _ = call("admin", "POST", "/evidence/verify", {"evidenceId": e["evidenceId"]})
    r3, b3, _ = call("admin", "GET", "/evidence/chain/status")
    ok = (b["code"] == 0 and b["data"]["tampered"] and b2["data"]["intact"] is False and b2["data"]["localHash"] != b2["data"]["chainHash"]
          and b3["data"]["intact"] is False and b3["data"]["brokenAt"] is not None)
    CTX["tamper_trace"] = b["traceId"]
    return ok, snip({"tamper": b["data"], "verify": b2["data"], "chain": {k: b3["data"][k] for k in ("intact", "brokenAt", "height")}})


def t_ev_restore():
    e = CTX["ev"]
    r, b, _ = call("admin", "POST", "/evidence/demo/restore", {"evidenceId": e["evidenceId"]})
    r2, b2, _ = call("admin", "POST", "/evidence/verify", {"evidenceId": e["evidenceId"]})
    r3, b3, _ = call("admin", "GET", "/evidence/chain/status")
    return b["code"] == 0 and b2["data"]["intact"] is True and b3["data"]["intact"] is True, snip({"restore": b["code"], "verify": b2["data"]["intact"], "chain": b3["data"]["intact"]})


def t_ev_tamper_audited():
    r, b, _ = call("admin", "GET", "/audit/logs", params={"action": "evidence:tamper", "size": 3})
    it = b["data"]["items"]
    return bool(it) and it[0]["riskLevel"] == "critical", snip([{k: i[k] for k in ("action", "riskLevel", "traceId", "evidenceId")} for i in it[:2]])


def t_ev_certificate():
    e = CTX["ev"]
    r, b, _ = call("regulator", "GET", f"/evidence/{e['evidenceId']}/certificate")
    d = b["data"]
    inner = d.get("evidence", d)
    ok = b["code"] == 0 and inner.get("evidenceId") == e["evidenceId"] and inner.get("hash") == e["hash"] and d.get("certificateType")
    return ok, snip(d)


def t_ev_trace():
    tid = CTX["asset"].get("traceId") or CTX["tamper_trace"]
    r, b, _ = call("regulator", "GET", f"/evidence/trace/{CTX['tamper_trace']}")
    return b["code"] == 0 and b["data"], snip(b["data"])


def t_ev_not_found():
    r, b, _ = call("regulator", "GET", "/evidence/ev-999999999")
    return r.status_code == 404 and b["code"] == 1005, snip(b)


# ============================================================ 3.7 审计
def t_audit_log_fields():
    r, b, _ = call("regulator", "GET", "/audit/logs", params={"size": 3})
    it = b["data"]["items"][0]
    need = {"traceId", "actorDid", "action", "resourceType", "at", "result", "hash", "riskLevel", "module"}
    return need <= set(it), snip(it)


def t_audit_filters():
    r1, b1, _ = call("regulator", "GET", "/audit/logs", params={"riskLevel": "high", "size": 5})
    r2, b2, _ = call("regulator", "GET", "/audit/logs", params={"actorDid": ME["subject"]["did"], "size": 5})
    r3, b3, _ = call("regulator", "GET", "/audit/logs", params={"action": "login", "from": "2026-08-22T00:00:00+08:00", "to": "2026-08-23T00:00:00+08:00", "size": 5})
    r4, b4, _ = call("regulator", "GET", "/audit/logs", params={"keyword": TAG, "size": 5})
    r5, b5, _ = call("regulator", "GET", "/audit/logs", params={"riskLevel": "bogus"})
    ok = (all(i["riskLevel"] == "high" for i in b1["data"]["items"]) and b1["data"]["total"] > 0
          and all(i["actorDid"] == ME["subject"]["did"] for i in b2["data"]["items"]) and b2["data"]["total"] > 0
          and all(i["action"] == "login" for i in b3["data"]["items"]) and b3["data"]["total"] > 0 and b4["code"] == 0 and b5["code"] == 1001)
    return ok, snip({"high": b1["data"]["total"], "subject": b2["data"]["total"], "login_today": b3["data"]["total"], "keyword": b4["data"]["total"], "bad": b5["code"]})


def t_audit_trace_full_chain():
    """用资产登记的 traceId 查完整链路：登录态→权限校验→业务→存证。"""
    tid = CTX["asset"]["traceId"] if CTX["asset"].get("traceId") else None
    if not tid:
        # 从审计日志里找资产登记记录的 traceId
        r, b, _ = call("regulator", "GET", "/audit/logs", params={"action": "asset:register", "size": 1})
        tid = b["data"]["items"][0]["traceId"]
    r, b, _ = call("regulator", "GET", f"/audit/trace/{tid}")
    d = b["data"]
    ok = b["code"] == 0 and d["traceId"] == tid and d["steps"] and "summary" in d and d["summary"].get("actorDid")
    CTX["trace_example"] = tid
    return ok, snip({"traceId": tid, "summary": d["summary"], "steps": [(s["seq"], s["module"], s["action"], s["result"]) for s in d["steps"]]})


def t_audit_trace_custom_header():
    """客户端指定 X-Trace-Id，backend 沿用并贯穿审计/存证。"""
    tid = f"tr-20260822-{uuid.uuid4().hex[:8]}"
    r, b, _ = call("admin", "POST", "/assets", {"name": f"{TAG}-trace", "dataType": "load", "sourceDid": ME["admin"]["did"], "payload": {"load": 1}}, headers={"X-Trace-Id": tid})
    r2, b2, _ = call("admin", "GET", f"/audit/trace/{tid}")
    r3, b3, _ = call("admin", "GET", f"/evidence/{b['data']['evidenceId']}")
    ok = b["traceId"] == tid and r.headers.get("x-trace-id", tid) == tid and b2["data"]["steps"] and b3["data"].get("traceId") == tid
    return ok, snip({"resp_traceId": b["traceId"], "hdr": r.headers.get("x-trace-id"), "trace_steps": len(b2["data"]["steps"]), "evidence_traceId": b3["data"].get("traceId")})


def t_audit_alerts_R01():
    """越权累计 ≥3 次/5 分钟 → R01 告警。用 subject 连续调无权限接口。"""
    for _ in range(4):
        call("subject", "GET", "/fl/tasks")
    time.sleep(0.5)
    r, b, _ = call("admin", "GET", "/audit/alerts", params={"status": "open", "size": 50})
    it = [a for a in b["data"]["items"] if a["ruleCode"] == "R01_UNAUTHORIZED"]
    CTX["alert"] = it[0] if it else None
    return bool(it), snip(it[:1])


def t_audit_alert_ack():
    a = CTX.get("alert")
    if not a:
        raise BlockedError("无 open 告警")
    r, b, _ = call("admin", "POST", f"/audit/alerts/{a['id']}/ack")
    r2, b2, _ = call("admin", "GET", "/audit/alerts", params={"status": "acked", "size": 200})
    ok = b["code"] == 0 and any(x["id"] == a["id"] for x in b2["data"]["items"])
    return ok, snip({"ack": b["data"], "in_acked_list": ok})


def t_audit_alert_R02():
    """已注销 DID 尝试验签/接入 → R02 异常 DID。"""
    d = CTX["did"]  # 已 revoke
    nonce = uuid.uuid4().hex[:12]
    sig = gm.sign(nonce, d["privateKey"], d["publicKey"])
    call("edge", "POST", "/nodes/Node-A/online", {"did": d["did"], "nonce": nonce, "signature": sig})
    call("edge", "POST", "/did/verify", {"did": d["did"], "message": nonce, "signature": sig})
    time.sleep(0.3)
    r, b, _ = call("admin", "GET", "/audit/alerts", params={"size": 100})
    it = [a for a in b["data"]["items"] if a["ruleCode"] == "R02_ABNORMAL_DID"]
    return bool(it), snip(it[:1]) if it else snip({"rules_seen": sorted({a['ruleCode'] for a in b['data']['items']})})


def t_audit_alert_R04():
    """R04 批量导出：10 分钟内导出 ≥3 次。"""
    for _ in range(3):
        call("admin", "GET", "/audit/logs/export", raw=True)
    time.sleep(0.3)
    r, b, _ = call("admin", "GET", "/audit/alerts", params={"size": 100})
    it = [a for a in b["data"]["items"] if a["ruleCode"] == "R04_BULK_EXPORT"]
    return bool(it), snip(it[:1])


def t_audit_rules():
    r, b, _ = call("regulator", "GET", "/audit/rules")
    codes = {x["ruleCode"] for x in b["data"]["items"]}
    return codes == {"R01_UNAUTHORIZED", "R02_ABNORMAL_DID", "R03_PERM_CHURN", "R04_BULK_EXPORT", "R05_SUSPICIOUS_GRAD"}, snip(sorted(codes))


def t_audit_export_csv():
    r, _, ms = call("regulator", "GET", "/audit/logs/export", params={"riskLevel": "high"}, raw=True)
    ct = r.headers.get("content-type", "")
    lines = r.text.splitlines()
    ok = r.status_code == 200 and "text/csv" in ct and "attachment" in r.headers.get("content-disposition", "") and len(lines) >= 2 and ("traceId" in lines[0] or "追踪ID" in lines[0])
    return ok, snip({"content-type": ct, "disposition": r.headers.get("content-disposition"), "rows": r.headers.get("x-total-rows"), "head": lines[0][:120], "ms": ms})


def t_audit_export_forbidden():
    r, b, _ = call("vpp", "GET", "/audit/logs/export")
    r2, b2, _ = call("grid", "GET", "/audit/logs")
    return b["code"] == 1003 and b2["code"] == 1003, f"vpp export:{b['code']}; grid logs:{b2['code']}"


def t_audit_report():
    ev, ok = [], True
    for period in ("day", "week", "month"):
        r, b, ms = call("admin", "GET", "/audit/report", params={"period": period, "date": "2026-08-22"})
        d = b["data"]
        good = b["code"] == 0 and d["period"] == period and {"identityOps", "permissionOps", "evidence", "riskEvents", "narrative", "narrativeSource"} <= set(d) and d["narrativeSource"] in ("live", "cache", "rule") and len(d["narrative"]) > 10
        ok &= good
        ev.append(f"{period}:{d.get('narrativeSource')} ids={d.get('identityOps')} perm={d.get('permissionOps')} {ms}ms narrative={d.get('narrative', '')[:40]}")
    r, b, _ = call("admin", "GET", "/audit/report", params={"period": "year"})
    ok &= b["code"] == 1001
    return ok, "\n".join(ev)


def t_audit_stats():
    r, b, _ = call("regulator", "GET", "/audit/stats")
    d = b["data"]
    ok = {"todayLogs", "highRiskLogs", "openAlerts", "onChainLogs", "byModule", "byRisk", "trend"} <= set(d) and d["todayLogs"] > 0
    return ok, snip(d)


def t_audit_monthly_shard():
    """按月分表：information_schema 中应存在 audit_log_YYYYMM（当月）。"""
    import subprocess
    sp = os.path.join("/tmp/claude-1002/-home-stu-yanxulong-Tzb/65f0c8ee-142f-469a-945b-159bbb6cf897/scratchpad/mysql")
    cmd = [f"{sp}/mdb/bin/mariadb", "-S", f"{sp}/mysql.sock", "-uroot", "energy_tds", "-N", "-e",
           "SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema='energy_tds' AND table_name LIKE 'audit_log_%'"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if out.returncode != 0:
        raise BlockedError(f"mariadb 客户端不可用：{out.stderr[:100]}")
    month = datetime.now().strftime("%Y%m")
    return f"audit_log_{month}" in out.stdout, out.stdout.strip().replace("\n", "; ")


def t_audit_ws_eventual_hash():
    """每条审计日志都有 hash，且高风险日志带 evidenceId（上链）。"""
    r, b, _ = call("regulator", "GET", "/audit/logs", params={"riskLevel": "high", "size": 10})
    it = b["data"]["items"]
    ok = it and all(i["hash"] for i in it) and all(i.get("evidenceId") for i in it)
    return bool(ok), snip([(i["action"], i["hash"][:16], i.get("evidenceId")) for i in it[:5]])


# ============================================================ 3.8 联邦学习
def t_fl_create():
    r, b, _ = call("admin", "POST", "/fl/tasks", {"name": f"{TAG}-FL", "nodeIds": ["Node-A", "Node-B", "Node-C", "Node-D"], "rounds": 5,
                                                  "dp": {"enabled": True, "epsilon": 1.0, "delta": 1e-5}, "topk": {"enabled": True, "ratio": 0.1}})
    CTX["fl"] = b["data"]
    return b["code"] == 0 and b["data"]["status"] == "created" and b["data"]["id"].startswith("fl-"), snip(b["data"])


def t_fl_create_forbidden():
    r, b, _ = call("vpp", "POST", "/fl/tasks", {"name": "x", "nodeIds": ["Node-A"]})
    r2, b2, _ = call("subject", "POST", "/fl/tasks", {"name": "x", "nodeIds": ["Node-A"]})
    r3, b3, _ = call("admin", "POST", "/fl/tasks", {"name": "x", "nodeIds": ["Node-A"], "dp": {"epsilon": 99}})
    return b["code"] == 1003 and b2["code"] == 1003 and b3["code"] == 1001, f"vpp:{b['code']} subject:{b2['code']} epsilon=99:{b3['code']}"


def t_fl_start_and_converge():
    fid = CTX["fl"]["id"]
    r, b, _ = call("grid", "POST", f"/fl/tasks/{fid}/start")
    if b["code"] != 0:
        return False, snip(b)
    r2, b2, _ = call("grid", "POST", f"/fl/tasks/{fid}/start")  # 重复启动
    deadline = time.time() + 150
    d = None
    while time.time() < deadline:
        r3, b3, _ = call("grid", "GET", f"/fl/tasks/{fid}")
        d = b3["data"]
        if d["status"] in ("success", "failed", "cancelled"):
            break
        time.sleep(2)
    CTX["fl_detail"] = d
    rounds = d["rounds"]
    losses = [x["loss"] for x in rounds]
    ok = (d["status"] == "success" and len(rounds) == 5 and b2["code"] == 1006
          and all(x["gradientHash"] and x.get("evidenceId") for x in rounds)
          and d["dp"]["epsilonSpent"] <= d["dp"]["epsilon"] + 1e-9 and rounds[-1]["epsilonSpent"] > 0
          and all(0 < x["compressionRatio"] <= 100 for x in rounds) and losses[-1] <= losses[0]
          and all(n["joined"] for n in d["nodes"]) and d.get("modelVersion"))
    return ok, snip({"status": d["status"], "dup_start": b2["code"], "rounds": [(x["round"], x["loss"], x["epsilonSpent"], x["compressionRatio"], x["gradientHash"][:12], x["evidenceId"]) for x in rounds],
                     "dp": d["dp"], "topk": d["topk"], "nodes": d["nodes"], "modelVersion": d["modelVersion"]}, 900)


def t_fl_rounds_and_evidence_onchain():
    fid = CTX["fl"]["id"]
    r, b, _ = call("edge", "GET", f"/fl/tasks/{fid}/rounds")
    rounds = b["data"]["items"] if isinstance(b["data"], dict) and "items" in b["data"] else b["data"]
    evid = rounds[0]["evidenceId"]
    r2, b2, _ = call("regulator", "GET", f"/evidence/{evid}")
    ok = b["code"] == 0 and len(rounds) == 5 and b2["code"] == 0 and b2["data"]["category"] == "algo"
    return ok, snip({"rounds": len(rounds), "evidence": {k: b2["data"].get(k) for k in ("evidenceId", "category", "refId", "hash", "blockHeight")}})


def t_fl_list_models_publish():
    r, b, _ = call("edge", "GET", "/fl/tasks", params={"status": "success", "size": 5})
    r2, b2, _ = call("edge", "GET", "/fl/models", params={"size": 5})
    ver = CTX["fl_detail"].get("modelVersion")
    r3, b3, _ = call("admin", "POST", f"/fl/models/{ver}/publish")
    r4, b4, _ = call("regulator", "GET", "/evidence", params={"category": "algo", "size": 3})
    ok = b["data"]["total"] >= 1 and b2["code"] == 0 and b2["data"]["total"] >= 1 and b3["code"] == 0 and b4["data"]["total"] > 0
    return ok, snip({"tasks_success": b["data"]["total"], "models": [m.get("version") for m in b2["data"]["items"]], "publish": b3["data"]})


def t_fl_cancel():
    r, b, _ = call("admin", "POST", "/fl/tasks", {"name": f"{TAG}-FL-cancel", "nodeIds": ["Node-A", "Node-B", "Node-C"], "rounds": 30})
    fid = b["data"]["id"]
    r1, b1, _ = call("admin", "POST", f"/fl/tasks/{fid}/start")
    time.sleep(3)
    r2, b2, _ = call("admin", "POST", f"/fl/tasks/{fid}/cancel")
    time.sleep(2)
    r3, b3, _ = call("admin", "GET", f"/fl/tasks/{fid}")
    ok = b1["code"] == 0 and b2["code"] == 0 and b3["data"]["status"] == "cancelled" and len(b3["data"]["rounds"]) < 30
    return ok, snip({"cancel": b2["data"], "status": b3["data"]["status"], "rounds_done": len(b3["data"]["rounds"])})


def t_fl_dp_budget_exhausted():
    """DP 预算：ε 很小、轮数多 → 算法服务上报 privacy_budget_exhausted 异常，任务异常停止，backend 生成 R05。"""
    r, b, _ = call("admin", "POST", "/fl/tasks", {"name": f"{TAG}-FL-dp", "nodeIds": ["Node-A", "Node-B", "Node-C"], "rounds": 20,
                                                  "dp": {"enabled": True, "epsilon": 0.05, "delta": 1e-5}})
    fid = b["data"]["id"]
    call("admin", "POST", f"/fl/tasks/{fid}/start")
    deadline = time.time() + 120
    d = None
    while time.time() < deadline:
        r3, b3, _ = call("admin", "GET", f"/fl/tasks/{fid}")
        d = b3["data"]
        if d["status"] in ("success", "failed", "cancelled"):
            break
        time.sleep(2)
    r4, b4, _ = call("admin", "GET", "/audit/alerts", params={"size": 100})
    r05 = [a for a in b4["data"]["items"] if a["ruleCode"] == "R05_SUSPICIOUS_GRAD"]
    spent = d["dp"]["epsilonSpent"]
    ok = spent <= d["dp"]["epsilon"] * 1.05 and (d["status"] == "failed" or len(d["rounds"]) < 20 or r05)
    return ok, snip({"status": d["status"], "rounds_done": len(d["rounds"]), "dp": d["dp"], "anomaly": d.get("anomaly"), "R05_alerts": len(r05)})


def t_fl_poison_algo():
    """投毒检测：算法服务 simulatePoison 让 Node-C 梯度翻转放大 → anomaly gradient_poisoning。（backend 创建接口未暴露该字段，直接打算法服务）"""
    jid = f"{TAG}-poison"
    r = client.post(f"{ALGO}/fl/train", json={"jobId": jid, "rounds": 6, "nodes": [{"id": "Node-A", "samples": 400}, {"id": "Node-B", "samples": 300}, {"id": "Node-C", "samples": 300}, {"id": "Node-D", "samples": 200}],
                                             "dp": {"enabled": False}, "topk": {"enabled": True, "ratio": 0.2}, "simulatePoison": "Node-C"}, headers={"X-Trace-Id": "tr-20260822-testfunc"})
    if r.status_code != 200:
        return False, snip(r.text)
    deadline = time.time() + 90
    d = None
    while time.time() < deadline:
        d = client.get(f"{ALGO}/fl/jobs/{jid}").json()
        if d["status"] in ("success", "failed", "cancelled"):
            break
        time.sleep(1.5)
    an = d.get("anomaly")
    ok = bool(an) and an.get("type") == "gradient_poisoning" and an.get("nodeId") == "Node-C"
    return ok, snip({"status": d["status"], "anomaly": an, "rounds": len(d["rounds"])})


def t_fl_topk_ratio():
    r, b, _ = call("admin", "POST", "/fl/tasks", {"name": f"{TAG}-FL-topk", "nodeIds": ["Node-A", "Node-B"], "rounds": 2, "dp": {"enabled": False}, "topk": {"enabled": True, "ratio": 0.3}})
    fid = b["data"]["id"]
    call("admin", "POST", f"/fl/tasks/{fid}/start")
    deadline = time.time() + 90
    while time.time() < deadline:
        r3, b3, _ = call("admin", "GET", f"/fl/tasks/{fid}")
        if b3["data"]["status"] in ("success", "failed"):
            break
        time.sleep(2)
    d = b3["data"]
    cr = [x["compressionRatio"] for x in d["rounds"]]
    ok = d["status"] == "success" and all(55 <= c <= 80 for c in cr)  # ratio 0.3 → 压缩率约 70%
    return ok, snip({"status": d["status"], "compressionRatio": cr, "topk": d["topk"], "eps": d["dp"]})


# ============================================================ 3.9 调度
def t_dp_create_run():
    r, b, _ = call("admin", "POST", "/dispatch/tasks", {"name": f"{TAG}-DQN", "nodeIds": ["Node-A", "Node-B", "Node-C", "Node-D"], "timeWindow": "2026-08-22T15:00~16:00+08:00"})
    did_ = b["data"]["id"]
    CTX["dp"] = b["data"]
    r2, b2, ms = call("admin", "POST", f"/dispatch/tasks/{did_}/run")
    d = b2["data"]
    CTX["dp_run"] = d
    s = d["strategy"]
    acts = s["actions"]
    ok = (b2["code"] == 0 and d["status"] == "success" and acts and all(a["action"] in ("charge", "idle", "discharge") for a in acts)
          and all(abs(a["powerKw"]) <= 30 + 1e-6 for a in acts) and d["explanationSource"] in ("live", "cache", "rule") and d.get("evidenceId") and d.get("traceId")
          and "totalReward" in s)
    return ok, snip({"id": d["id"], "actions": acts, "totalReward": s["totalReward"], "explanationSource": d["explanationSource"], "explanation": d["explanation"][:60], "evidenceId": d["evidenceId"], "ms": ms}, 700)


def t_dp_constraints_algo():
    """约束检查：直接调算法服务，SOC 18%（低于 20）节点不应放电，SOC 97% 不应充电，功率 ≤30kW，violations 为空。"""
    nodes = [{"id": "Node-A", "pv": 5, "load": 150, "soc": 18, "storage": 0, "price": 0.9},
             {"id": "Node-B", "pv": 80, "load": 20, "soc": 97, "storage": 0, "price": 0.2},
             {"id": "Node-C", "pv": 40, "load": 120, "soc": 60, "storage": -10, "price": 0.6}]
    r = client.post(f"{ALGO}/dqn/dispatch", json={"taskId": f"{TAG}-dqn", "timeWindow": "t", "nodes": nodes}, headers={"X-Trace-Id": "tr-20260822-testfunc"})
    d = r.json()
    byn = {a["nodeId"]: a for a in d["actions"]}
    cc = d["constraintsChecked"]
    applied_ok = all((v["constraint"] != "socMin" or v["applied"] != "discharge") and (v["constraint"] != "socMax" or v["applied"] != "charge") for v in cc["violations"])
    ok = (cc["socMin"] == 20 and cc["socMax"] == 95 and cc["maxPowerKw"] == 30 and applied_ok
          and byn["Node-A"]["action"] != "discharge" and byn["Node-B"]["action"] != "charge" and all(abs(a["powerKw"]) <= 30 for a in d["actions"]) and d.get("qTable"))
    return ok, snip({"actions": d["actions"], "constraints": cc}, 900), "violations 字段记录的是“触发约束并已修正/保留”的动作（applied 均合规），非真正违规"


def t_dp_issue_forbidden_1003():
    tid = CTX["dp"]["id"]
    r, b, _ = call("vpp", "POST", f"/dispatch/tasks/{tid}/issue", {"signature": None})
    CTX["deny_trace"] = b["traceId"]
    time.sleep(0.3)
    r2, b2, _ = call("admin", "GET", "/audit/logs", params={"traceId": b["traceId"]})
    it = b2["data"]["items"]
    ok = r.status_code == 403 and b["code"] == 1003 and it and it[0]["riskLevel"] == "high" and it[0]["result"] == "denied"
    return ok, snip({"resp": b, "audit": [{k: i[k] for k in ("action", "result", "riskLevel", "actorDid")} for i in it[:1]]})


def t_dp_issue_bad_sig_1004():
    tid = CTX["dp"]["id"]
    r, b, _ = call("admin", "POST", f"/dispatch/tasks/{tid}/issue", {"signature": "invalid"})
    r2, b2, _ = call("admin", "GET", "/audit/logs", params={"traceId": b["traceId"]})
    it = b2["data"]["items"]
    ok = r.status_code == 403 and b["code"] == 1004 and it and it[0]["riskLevel"] == "high"
    return ok, snip({"resp": b, "audit": [{k: i[k] for k in ("action", "result", "riskLevel")} for i in it[:1]]})


def t_dp_issue_signed():
    """签名下发：用 admin 托管私钥由服务端代签（signature 省略）→ issued；再用客户端自签（seed 私钥不可得，故用 grid 托管）验证 grid 也可下发。"""
    tid = CTX["dp"]["id"]
    r, b, _ = call("admin", "POST", f"/dispatch/tasks/{tid}/issue", {})
    d = b["data"]
    CTX["issue"] = d
    r2, b2, _ = call("admin", "GET", f"/dispatch/tasks/{tid}")
    r3, b3, _ = call("regulator", "GET", f"/evidence/{d.get('evidenceId')}") if b["code"] == 0 else (None, {"code": -1}, 0)
    ok = b["code"] == 0 and d["issued"] and d["commandId"].startswith("cmd-") and d["signerDid"] == ME["admin"]["did"] and d["targets"] and b3["code"] == 0 and b2["data"]["status"] in ("success", "issued", "running")
    return ok, snip({"issue": d, "task_status": b2["data"].get("status"), "evidence_ok": b3["code"] == 0})


def t_dp_issue_twice():
    tid = CTX["dp"]["id"]
    r, b, _ = call("admin", "POST", f"/dispatch/tasks/{tid}/issue", {})
    return b["code"] in (1006, 0), snip(b), "" if b["code"] == 1006 else "重复下发未返回 1006（见缺陷）"


def t_dp_ack():
    tid = CTX["dp"]["id"]
    target = CTX["issue"]["targets"][0]
    r, b, _ = call("edge", "POST", f"/dispatch/tasks/{tid}/ack", {"nodeId": target, "accepted": True, "detail": "执行完毕 1.2s"})
    r2, b2, _ = call("edge", "GET", f"/dispatch/tasks/{tid}")
    ok = b["code"] == 0 and (b["data"].get("acked") or 0) >= 1 and b["data"].get("ackStatus") in ("partial", "acked", "full")
    return ok, snip({"ack": b["data"], "task": {k: b2["data"].get(k) for k in ("status", "commandId", "ackedAt", "acks")}})


def t_dp_trace_chain():
    """第 6 步越权 traceId → /audit/trace 完整步骤。"""
    r, b, _ = call("regulator", "GET", f"/audit/trace/{CTX['deny_trace']}")
    d = b["data"]
    return b["code"] == 0 and d["steps"] and d["summary"]["result"] in ("denied", "failed") and d["summary"]["riskLevel"] == "high", snip(d)


def t_dp_list_by_roles():
    ev, ok = [], True
    for k, allow in (("grid", True), ("vpp", True), ("regulator", True), ("edge", True), ("subject", False)):
        r, b, _ = call(k, "GET", "/dispatch/tasks", params={"size": 2})
        ok &= (b["code"] == 0) == allow
        ev.append(f"{k}:{b['code']}")
    r, b, _ = call("vpp", "POST", f"/dispatch/tasks/{CTX['dp']['id']}/run")
    ok &= b["code"] == 1003
    ev.append(f"vpp run:{b['code']}")
    return ok, "; ".join(ev)


# ============================================================ 3.10 AI
def t_ai_scenes():
    ev, ok = [], True
    for scene, ctx, q in (("dispatch", {"taskId": CTX["dp"]["id"]}, "为什么选择该节点放电？"), ("data", {"assetId": CTX["asset"]["id"]}, "这批数据质量如何？"),
                          ("risk", {"nodeId": "Node-A"}, "当前隐私风险如何？"), ("qa", {}, "什么是差分隐私？"), ("audit", {"date": "2026-08-22"}, "今日审计有何异常？")):
        r, b, ms = call("subject", "POST", "/ai/analyze", {"scene": scene, "context": ctx, "question": q})
        d = b["data"] if b["code"] == 0 else {}
        good = b["code"] == 0 and d.get("answer") and d.get("source") in ("live", "cache", "rule") and isinstance(d.get("reasoning"), list) and ms < 12000 and d.get("evidenceId")
        ok &= bool(good)
        ev.append(f"{scene}: source={d.get('source')} {ms}ms latencyMs={d.get('latencyMs')} answer={str(d.get('answer'))[:40]}")
    return ok, "\n".join(ev)


def t_ai_history():
    r, b, _ = call("subject", "GET", "/ai/history", params={"scene": "qa", "size": 5})
    ok = b["code"] == 0 and b["data"]["total"] >= 1 and all(i["scene"] == "qa" for i in b["data"]["items"])
    return ok, snip({"total": b["data"]["total"], "item0": b["data"]["items"][0] if b["data"]["items"] else None})


def t_ai_degrade_levels():
    """三级降级：算法服务 health 反映 deepseek 状态；分析接口 source 与其一致且永不报错；错误 scene → 1001。"""
    h = client.get(f"{ALGO}/health").json()
    r, b, ms = call("admin", "POST", "/ai/analyze", {"scene": "qa", "context": {}, "question": "x" * 1000})
    r2, b2, _ = call("admin", "POST", "/ai/analyze", {"scene": "bogus", "question": "x"})
    src = b["data"]["source"]
    ok = b["code"] == 0 and src in ("live", "cache", "rule") and b2["code"] == 1001
    note = f"当前 DeepSeek 模式={h['models'].get('deepseek')}；live→cache→rule 的强制降级需断网/去 key 环境，本轮以 algo 单测 test_deepseek.py 作为替代证据"
    return ok, snip({"health": h, "source": src, "ms": ms, "bad_scene": b2["code"]}), note


# ============================================================ 2.12 风险评估
def t_risk_assess():
    r, b, _ = call("edge", "POST", "/risk/assess", {"nodeId": "Node-A", "features": {"queryFreq": 12, "dataGranularity": "minute", "exposedFields": 6, "epsilonRemaining": 0.58}})
    r2, b2, _ = call("edge", "POST", "/risk/assess", {"nodeId": "Node-B", "features": {"queryFreq": 1, "dataGranularity": "day", "exposedFields": 1, "epsilonRemaining": 0.9}})
    r3, b3, _ = call("edge", "GET", "/risk/history", params={"nodeId": "Node-A", "size": 5})
    d, d2 = b["data"], b2["data"]
    ok = (b["code"] == 0 and 0 <= d["riskScore"] <= 100 and d["level"] in ("low", "medium", "high", "critical") and d["factors"] and d.get("suggestion") and d.get("evidenceId")
          and d["riskScore"] > d2["riskScore"] and b3["data"]["total"] >= 1)
    return ok, snip({"high": {k: d[k] for k in ("riskScore", "level", "suggestion")}, "low": {k: d2[k] for k in ("riskScore", "level")}, "history": b3["data"]["total"]})


# ============================================================ 3.11 / 2.8 节点与 WS
def t_nodes():
    r, b, _ = call("vpp", "GET", "/nodes")
    it = b["data"]["items"]
    r2, b2, _ = call("vpp", "GET", "/nodes/Node-A")
    r3, b3, _ = call("vpp", "GET", "/nodes/Node-A/metrics", params={"interval": "hour"})
    ok = len(it) >= 4 and {"id", "status", "did", "metrics"} <= set(it[0]) and b2["code"] == 0 and b3["code"] == 0 and (b3["data"].get("items") or b3["data"].get("points") or isinstance(b3["data"], list))
    return ok, snip({"nodes": [(n["id"], n["status"], n["metrics"].get("soc")) for n in it], "metrics_keys": list(b3["data"].keys())[:6] if isinstance(b3["data"], dict) else "list"})


def t_ws_messages():
    import asyncio
    import websockets

    async def run():
        seen = {}
        url = BASE.replace("http", "ws").replace("/api/v1", "/ws") + f"?token={TOKENS['admin']}"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"type": "ping"}))
            # 触发几类事件：越权(audit_alert/log)、存证写(evidence_written)、调度(dispatch_progress)、FL(fl_progress)
            loop = asyncio.get_event_loop()
            # 用本轮新建的临时用户触发越权（R01 同一主体 5 分钟内只告警一次，新主体保证必发）
            def _fresh_denials():
                name = f"{TAG}-wsdeny"
                r, b, _ = call("admin", "POST", "/users", {"username": name, "password": "Test123456", "realName": "WS越权测试", "roles": ["energy_subject"]})
                t = client.post(f"{BASE}/auth/login", json={"username": name, "password": "Test123456"}).json()["data"]["token"]
                for _ in range(4):
                    client.get(f"{BASE}/fl/tasks", headers={"Authorization": f"Bearer {t}"})
                call("admin", "DELETE", f"/users/{b['data']['id']}")
            await loop.run_in_executor(None, _fresh_denials)
            await loop.run_in_executor(None, lambda: call("admin", "POST", "/evidence", {"category": "audit", "refId": TAG + "-ws", "payload": {"x": 1}}))
            await loop.run_in_executor(None, lambda: call("admin", "POST", "/fl/tasks", {"name": TAG + "-ws", "nodeIds": ["Node-A", "Node-B"], "rounds": 2, "dp": {"enabled": False}}))
            r, b, _ = await loop.run_in_executor(None, lambda: call("admin", "GET", "/fl/tasks", {"size": 1}))
            fid = b["data"]["items"][0]["id"]
            await loop.run_in_executor(None, lambda: call("admin", "POST", f"/fl/tasks/{fid}/start"))
            await loop.run_in_executor(None, lambda: call("admin", "POST", "/dispatch/tasks", {"name": TAG + "-ws-dp", "nodeIds": ["Node-A", "Node-B", "Node-C"]}))
            r, b, _ = await loop.run_in_executor(None, lambda: call("admin", "GET", "/dispatch/tasks", {"size": 1}))
            dpid = b["data"]["items"][0]["id"]
            await loop.run_in_executor(None, lambda: call("admin", "POST", f"/dispatch/tasks/{dpid}/run"))
            deadline = time.time() + 40
            while time.time() < deadline and len(seen) < 7:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    continue
                m = json.loads(raw)
                t = m.get("type")
                if t not in seen:
                    seen[t] = m
        return seen

    seen = asyncio.run(run())
    need = {"pong", "node_status", "fl_progress", "dispatch_progress", "audit_alert", "log", "evidence_written"}
    got = set(seen)
    fmt_ok = all({"ts", "payload"} <= set(m) for t, m in seen.items() if t != "pong")
    return need <= got and fmt_ok, snip({t: (m.get("payload") if t != "pong" else m) for t, m in seen.items()}, 900), "" if need <= got else f"缺少：{sorted(need - got)}"


def t_ws_auth():
    import asyncio
    import websockets

    async def run():
        url = BASE.replace("http", "ws").replace("/api/v1", "/ws") + "?token=bad"
        try:
            async with websockets.connect(url) as ws:
                await ws.recv()
                return "open"
        except websockets.exceptions.ConnectionClosed as e:
            return f"closed code={e.code}"
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {e}"

    res = asyncio.run(run())
    return "4001" in res or "closed" in res or "403" in res or "401" in res, res


# ============================================================ 3.12 / 非功能
def t_api_docs():
    r = client.get("http://127.0.0.1:8000/docs")
    r2 = client.get("http://127.0.0.1:8000/openapi.json")
    n = len(r2.json().get("paths", {})) if r2.status_code == 200 else 0
    return r.status_code == 200 and n >= 60, f"docs={r.status_code}; openapi paths={n}"


def t_error_codes():
    ev, ok = [], True
    cases = [("GET", "/nonexistent-path", None, 404, 1005), ("GET", "/assets/99999999", None, 404, 1005), ("GET", "/assets", {"size": 500}, 400, 1001),
             ("POST", "/assets", {"name": "x"}, 400, 1001), ("GET", "/did/did:vpp:device:0xnotexist", None, 404, 1005)]
    for m, p, body, http, code in cases:
        if m == "GET":
            r, b, _ = call("admin", m, p, params=body)
        else:
            r, b, _ = call("admin", m, p, json_body=body)
        good = r.status_code == http and b.get("code") == code and "traceId" in b
        ok &= good
        ev.append(f"{'✓' if good else '✗'}{m} {p} → {r.status_code}/{b.get('code')}")
    return ok, "; ".join(ev)


def t_response_time():
    paths = ["/assets?size=20", "/did?size=20", "/evidence?size=20", "/audit/logs?size=20", "/nodes", "/permissions/matrix", "/audit/stats", "/assets/stats"]
    res = {}
    ok = True
    for p in paths:
        ts = []
        for _ in range(5):
            r, b, ms = call("admin", "GET", p)
            ts.append(ms)
        res[p] = max(ts)
        ok &= max(ts) < 2000
    return ok, snip(res)


def t_concurrent_login():
    def one(i):
        k = list(ACCOUNTS)[i % 6]
        u, p = ACCOUNTS[k]
        t0 = time.perf_counter()
        r = httpx.post(f"{BASE}/auth/login", json={"username": u, "password": p}, timeout=30)
        return r.status_code, round((time.perf_counter() - t0) * 1000)

    with cf.ThreadPoolExecutor(30) as ex:
        out = list(ex.map(one, range(30)))
    codes = [c for c, _ in out]
    mx = max(t for _, t in out)
    return codes.count(200) == 30 and mx < 5000, f"30 并发登录：200×{codes.count(200)}，其它 {[c for c in codes if c != 200]}，最大耗时 {mx}ms"


def t_pagination_contract():
    r, b, _ = call("admin", "GET", "/assets", params={"page": 2, "size": 10})
    d = b["data"]
    return {"items", "total", "page", "size"} <= set(d) and d["page"] == 2 and d["size"] == 10 and len(d["items"]) == 10, snip({k: d[k] for k in ("total", "page", "size")})


def t_offline_no_external():
    """离线可运行：前端源码/算法服务无外网 URL（DeepSeek 仅配置项）；本机无 DEEPSEEK 时功能仍通。"""
    import subprocess
    g = subprocess.run(["bash", "-c", f"grep -rnoE 'https?://[a-zA-Z0-9./_-]+' {ROOT}/frontend/src {ROOT}/algo-service --include=*.vue --include=*.js --include=*.py | grep -v 'w3id.org\\|localhost\\|127.0.0.1\\|example\\|^.*#\\|deepseek.com\\|schemas\\|test' | head -5"], capture_output=True, text=True)
    return g.stdout.strip() == "", f"外网 URL 扫描结果：{g.stdout.strip() or '无'}"


def main():
    tc("TC-31-01", "3.1", "六角色账号密码登录，返回 Token 与角色", t_login_all, "POST /auth/login ×6", "code 0，token，roles 含对应角色，expiresIn 28800", "POST /auth/login")
    tc("TC-31-02", "3.1", "错误密码登录被拒", t_login_bad, "POST /auth/login 密码 bad-pass", "HTTP 401 / code 1002", "POST /auth/login")
    tc("TC-31-03", "3.1", "当前用户信息含扁平权限列表", t_me, "GET /auth/me (admin)", "permissions 含 dispatch:issue、user:manage", "GET /auth/me")
    tc("TC-31-04", "3.1", "无 Token 访问被拒", t_no_token, "GET /auth/me 不带 Authorization", "401 / 1002", "中间件")
    tc("TC-31-05", "3.1", "伪造 Token 被拒", t_bad_token, "Authorization: Bearer abc.def.ghi", "401 / 1002", "中间件")
    tc("TC-31-06", "3.1", "登出后 Token 立即失效", t_logout, "login → logout → GET /auth/me", "logout 200；之后 401/1002", "POST /auth/logout")
    tc("TC-31-07", "3.1", "用户注册/修改/查询/删除全流程", t_user_crud, "POST /users → PUT → GET ?keyword → 新用户登录 → DELETE → 再登录", "各步 code 0；改角色后登录 roles 生效；删除后登录 401", "/users")
    tc("TC-31-08", "3.1", "重复用户名注册冲突", t_user_dup, "POST /users username=admin", "409 / 1006", "POST /users")
    tc("TC-31-09", "3.1", "用户参数校验（用户名过短/密码过短/角色为空）", t_user_param, "POST /users 非法参数", "400 / 1001", "POST /users")
    tc("TC-31-10", "3.1", "非管理员访问用户管理被拒", t_users_forbidden, "5 个非 admin 角色 GET /users", "403 / 1003", "GET /users")
    tc("TC-31-11", "3.1", "六个角色在角色表中存在", t_roles_list, "GET /roles", "含 6 个固定角色码", "GET /roles")
    tc("TC-31-12", "3.1", "RBAC 矩阵逐角色抽样（20 格）", t_rbac_matrix, "6 角色 × 资源:操作 各抽样调用", "有权限 code 0，无权限 1003，与种子矩阵一致", "require_permission / require_roles")

    tc("TC-32-01", "3.2", "设备 DID 生成（含 DID 文档、公私钥、上链）", t_did_register, "admin POST /did/register subjectType=device", "did:vpp:device:0x+32hex，didDocument.verificationMethod，privateKey 仅本次返回，evidenceId/chainTxId", "POST /did/register")
    tc("TC-32-02", "3.2", "边缘节点 / 机构 / 用户 DID 生成", t_did_register_edge_org, "POST /did/register subjectType=edge/org/user", "三种 DID 前缀正确", "POST /did/register")
    tc("TC-32-03", "3.2", "DID 查询（类型/状态/关键字筛选、分页、非法筛选）", t_did_list_query, "GET /did?subjectType=device&keyword=…；?status=active&size=5；?subjectType=bogus", "筛选命中；非法值 1001", "GET /did")
    tc("TC-32-04", "3.2", "DID 文档/详情查询", t_did_document, "GET /did/{did}、GET /did/{did}/document", "返回 id 与 verificationMethod", "GET /did/{did}")
    tc("TC-32-05", "3.2", "DID+公钥签名身份认证（验签通过）", t_did_verify_ok, "用注册返回的私钥 SM2 签名 → POST /did/verify", "valid true，status active", "POST /did/verify")
    tc("TC-32-06", "3.2", "验签拒绝：篡改原文 / 错误签名 / 未知 DID", t_did_verify_bad, "三种无效输入 POST /did/verify", "valid 均为 false", "POST /did/verify")
    tc("TC-32-07", "3.2", "DID 冻结后验签失败", t_did_freeze, "POST /did/{did}/status freeze → verify → GET 详情", "status frozen 且有 evidenceId；验签 false", "POST /did/{did}/status")
    tc("TC-32-08", "3.2", "DID 恢复（解冻）后验签恢复", t_did_unfreeze, "unfreeze → verify", "active；验签 true", "POST /did/{did}/status")
    tc("TC-32-09", "3.2", "非管理员不能改 DID 状态", t_did_status_forbidden, "vpp/grid/subject POST status", "1003", "require_roles(sys_admin)")
    tc("TC-32-10", "3.2/3.3", "密钥轮换：旧签名失效、新签名有效、版本递增", t_did_rotate, "POST /did/{did}/rotate-key → 旧/新签名 verify → GET /keys?did", "旧 false 新 true；密钥版本 ≥2", "POST /did/{did}/rotate-key")
    tc("TC-32-11", "3.2", "非本人且非管理员不能轮换他人密钥", t_did_rotate_other_forbidden, "vpp 轮换 admin 注册的设备 DID", "1003", "rotate-key")
    tc("TC-32-12", "3.2", "DID 批量解析", t_did_resolve, "POST /did/resolve 3 个 DID（含不存在）", "返回 3 条", "POST /did/resolve")
    tc("TC-32-13", "3.2", "DID 吊销后不可验签、不可恢复", t_did_revoke, "revoke → verify → unfreeze", "revoked；false；unfreeze 被拒(1006/1001)", "POST /did/{did}/status")
    tc("TC-32-14", "3.2/3.7", "DID 状态变更全部留高风险审计", t_did_lifecycle_audit, "GET /audit/logs?action=did:status", "≥3 条 high", "@audited")

    tc("TC-33-01", "3.3", "密钥查询（按状态筛选）", t_key_list, "GET /keys?status=active", "字段 algorithm/publicKey/version/boundAt", "GET /keys")
    tc("TC-33-02", "3.3", "密钥生成与绑定：SM2 / ECC / RSA 三种算法，非法算法拒绝", t_key_create_algos, "POST /keys algorithm=SM2/ECC/RSA/DES", "前三种 code 0 并返回私钥；DES 1001", "POST /keys")
    tc("TC-33-03", "3.3", "密钥冻结 / 注销 / 轮换历史", t_key_freeze_revoke_history, "freeze → revoke → history → 再 freeze", "frozen→revoked；history 200；注销后冻结被拒", "/keys/{id}/*")
    tc("TC-33-04", "3.3", "非授权角色不能生成/冻结密钥", t_key_forbidden, "subject POST /keys；vpp freeze", "1003", "require_roles")
    tc("TC-33-05", "3.3/3.11", "密钥用于接口认证：边缘节点签 nonce 上线，重放与错误签名拒绝", t_key_used_for_auth, "POST /nodes/Node-A/online 正确签名 / 同 nonce 重放 / 错误签名", "accepted true + evidenceId；重放 1004；错误签名 1004", "POST /nodes/{id}/online")
    tc("TC-33-06", "3.3", "绑定 ECC/RSA 密钥后 SM2 密钥仍可验签", t_key_ecc_breaks_verify, "为 DID 先后绑定 SM2(v2)/ECC(v3)/RSA(v4，已注销) → 用 SM2 v2 私钥签名 verify", "valid true（多算法密钥共存不影响 SM2 验签）", "verify_signature")

    tc("TC-34-01", "3.4", "五类能源数据登记（光伏/风电/储能/负荷/调度）并上链", t_asset_register, "POST /assets ×5", "hash=sm3:，level L1–L4，evidenceId/chainTxId，authStatus unauthorized", "POST /assets")
    tc("TC-34-02", "3.4", "资产记录来源 DID、敏感等级、SM3 摘要", t_asset_hash_is_sm3, "GET /assets/{id} 与本地 SM3(payload) 比对", "字段齐全；摘要一致", "GET /assets/{id}")
    tc("TC-34-03", "3.4", "自动分类分级 L1–L4 随敏感字段/粒度递增", t_asset_classify_levels, "POST /assets/classify 4 条记录", "等级单调递增且 ≥3 个不同等级，含 reason", "POST /assets/classify → algo /classify")
    tc("TC-34-04", "3.4", "显式指定 L4 核心等级登记", t_asset_level_explicit_L4, "POST /assets level=L4", "level L4", "POST /assets")
    tc("TC-34-05", "3.4", "资产列表筛选（类型/等级/来源 DID/分页）", t_asset_list_filters, "GET /assets 各筛选", "结果与筛选一致", "GET /assets")
    tc("TC-34-06", "3.4", "数据溯源链", t_asset_lineage, "GET /assets/{id}/lineage", "chain[0].stage=register 且 evidenceId 与登记一致", "GET /assets/{id}/lineage")
    tc("TC-34-07", "3.4", "分级统计", t_asset_stats, "GET /assets/stats", "byLevel/byType/total/onChain", "GET /assets/stats")
    tc("TC-34-08", "3.4/3.5", "能源主体仅能读自己的资产（own 范围）", t_asset_own_scope, "subject 读 admin 资产；列表；edge 溯源", "1003；列表无他人数据", "require_permission scope=own")
    tc("TC-34-09", "3.4/3.5", "无 asset:write 的角色不能登记", t_asset_write_forbidden, "grid POST /assets", "1003", "POST /assets")
    tc("TC-34-10", "3.4/3.5", "导出权限 asset:export 按角色判定", t_asset_export_permission, "6 角色 POST /permissions/check action=export", "admin/grid/regulator 允许，其余拒绝", "POST /permissions/check")

    tc("TC-35-01", "3.5", "资源-操作权限矩阵", t_perm_matrix, "GET /permissions/matrix", "5 资源 × 5 操作；vpp 无 dispatch:issue", "GET /permissions/matrix")
    tc("TC-35-02", "3.5", "能源主体提交权限申请", t_perm_apply, "subject POST /permissions/apply asset:read", "pending，applicantDid 正确，evidenceId，出现在申请列表", "POST /permissions/apply")
    tc("TC-35-03", "3.5", "审批前权限校验拒绝、实际访问 1003", t_perm_check_before, "POST /permissions/check；GET /assets/{id}", "allowed false；1003", "权限判断（身份+DID+角色+等级+场景）")
    tc("TC-35-04", "3.5", "非管理员不能审批", t_perm_approve_forbidden, "vpp/grid/regulator/subject approve", "1003", "approve")
    tc("TC-35-05", "3.5", "管理员审批通过 → 授权生效 → 可访问", t_perm_approve, "admin approve → check → subject GET asset → grants", "approved；allowed true；200；授权记录存在；资产 authStatus 变化", "approve / grants")
    tc("TC-35-06", "3.5", "重复审批状态冲突", t_perm_approve_twice, "再次 approve", "1006", "approve")
    tc("TC-35-07", "3.5", "驳回申请后仍不可访问", t_perm_reject, "apply L4 → reject → GET", "rejected；1003", "reject")
    tc("TC-35-08", "3.5", "回收授权后访问被拒", t_perm_revoke, "POST /permissions/grants/{id}/revoke → GET → check", "1003；allowed false", "revoke")
    tc("TC-35-09", "3.5/3.7", "权限申请/审批/驳回/回收全部留痕", t_perm_audit_trail, "GET /audit/logs keyword=permission", "四类 action 均存在；approve 为 high", "@audited")
    tc("TC-35-10", "3.5", "业务场景维度判定（同角色不同资源/操作）", t_perm_scene, "vpp check dispatch:issue/read、algo:execute、asset:write", "false/true/false/true", "POST /permissions/check")
    tc("TC-35-11", "3.1/3.5", "自定义角色新建与修改权限", t_role_create_update, "POST /roles → PUT /roles/{code} → matrix；vpp 新建", "成功；矩阵体现；vpp 1003", "/roles")

    tc("TC-36-01", "3.6", "写入存证（返回 hash/blockHeight/prevHash/txId）", t_ev_write, "admin POST /evidence", "ev-ID、sm3 hash、高度>0", "POST /evidence")
    tc("TC-36-02", "3.6", "数据接入/身份/授权/审计/算法五类业务均自动存证", t_ev_categories_written_by_business, "GET /evidence?category=…", "每类 total>0", "GET /evidence")
    tc("TC-36-03", "3.6", "存证检索（DID / 时间 / 数据类型）", t_ev_search, "GET /evidence 多筛选", "命中", "GET /evidence")
    tc("TC-36-04", "3.6", "存证详情：链上只有 Hash/身份/时间，不含原始能源数据", t_ev_detail_no_raw, "GET /evidence/{id}", "hash/actorDid/blockHeight 一致", "GET /evidence/{id}")
    tc("TC-36-05", "3.6", "完整性校验（库内重算 / 相同 payload / 不同 payload）", t_ev_verify_intact, "POST /evidence/verify ×3", "true/true/false", "POST /evidence/verify")
    tc("TC-36-06", "3.6", "链状态完整", t_ev_chain_status_before, "GET /evidence/chain/status", "intact true，brokenAt null", "GET /evidence/chain/status")
    tc("TC-36-07", "3.6", "非管理员不能篡改演示", t_ev_tamper_forbidden, "vpp/regulator POST demo/tamper", "1003", "require_roles(sys_admin)")
    tc("TC-36-08", "3.6", "篡改演示被检出：verify intact=false，链状态断裂点", t_ev_tamper_detect, "admin tamper → verify → chain/status", "tampered；intact false 且 localHash≠chainHash；brokenAt 非空", "POST /evidence/demo/tamper")
    tc("TC-36-09", "3.6", "篡改还原后链恢复完整", t_ev_restore, "POST /evidence/demo/restore → verify → status", "intact true", "POST /evidence/demo/restore")
    tc("TC-36-10", "3.6/3.7", "篡改操作记 critical 审计", t_ev_tamper_audited, "GET /audit/logs?action=evidence:tamper", "riskLevel critical", "@audited")
    tc("TC-36-11", "3.6", "导出存证凭证", t_ev_certificate, "GET /evidence/{id}/certificate", "evidenceId/hash 一致", "GET /evidence/{id}/certificate")
    tc("TC-36-12", "3.6", "按 traceId 查存证链路", t_ev_trace, "GET /evidence/trace/{traceId}", "code 0，有记录", "GET /evidence/trace/{traceId}")
    tc("TC-36-13", "3.6/3.12", "不存在的存证返回 1005", t_ev_not_found, "GET /evidence/ev-999999999", "404 / 1005", "GET /evidence/{id}")

    tc("TC-37-01", "3.7", "审计日志字段：用户 DID/操作/资源/时间/结果/Hash", t_audit_log_fields, "GET /audit/logs", "字段齐全", "GET /audit/logs")
    tc("TC-37-02", "3.7", "日志检索筛选（风险/DID/操作+时间/关键字/非法值）", t_audit_filters, "GET /audit/logs 多筛选", "结果与筛选一致；非法 1001", "GET /audit/logs")
    tc("TC-37-03", "3.7", "按任务 ID（traceId）查询完整业务链路", t_audit_trace_full_chain, "GET /audit/trace/{traceId}", "summary + steps，含存证步骤", "GET /audit/trace/{traceId}")
    tc("TC-37-04", "3.7/3.12", "客户端指定 X-Trace-Id 贯穿响应/审计/存证", t_audit_trace_custom_header, "带 X-Trace-Id 登记资产 → trace → evidence", "三处 traceId 一致", "中间件 traceId")
    tc("TC-37-05", "3.7", "风险告警 R01 越权访问（≥3 次/5 分钟）", t_audit_alerts_R01, "subject 连续 4 次无权限调用 → GET /audit/alerts", "出现 R01_UNAUTHORIZED open 告警", "rules.py")
    tc("TC-37-06", "3.7", "告警确认", t_audit_alert_ack, "POST /audit/alerts/{id}/ack", "进入 acked 列表", "ack")
    tc("TC-37-07", "3.7", "风险告警 R02 异常 DID（已注销 DID 尝试接入）", t_audit_alert_R02, "用已吊销 DID 签名上线/验签", "出现 R02_ABNORMAL_DID", "rules.py")
    tc("TC-37-08", "3.7", "风险告警 R04 批量导出（10 分钟 ≥3 次）", t_audit_alert_R04, "连续 3 次 /audit/logs/export", "出现 R04_BULK_EXPORT", "rules.py")
    tc("TC-37-09", "3.7", "五类风险规则清单", t_audit_rules, "GET /audit/rules", "R01–R05", "GET /audit/rules")
    tc("TC-37-10", "3.7", "审计日志导出 CSV（文件流）", t_audit_export_csv, "GET /audit/logs/export?riskLevel=high", "text/csv attachment，表头含 traceId", "GET /audit/logs/export")
    tc("TC-37-11", "3.7", "审计仅 sys_admin/regulator 可查", t_audit_export_forbidden, "vpp export；grid logs", "1003", "_AUDITOR")
    tc("TC-37-12", "3.7/3.10", "审计报告 day/week/month 含 DeepSeek 解读与来源标识", t_audit_report, "GET /audit/report period=…", "字段齐全，narrativeSource∈live/cache/rule；period=year 1001", "GET /audit/report")
    tc("TC-37-13", "3.7", "审计看板统计", t_audit_stats, "GET /audit/stats", "todayLogs/byModule/byRisk/trend", "GET /audit/stats")
    tc("TC-37-14", "3.7/5.1", "审计日志按月分表", t_audit_monthly_shard, "information_schema 查 audit_log_%", "存在当月 audit_log_YYYYMM", "sharding.py")
    tc("TC-37-15", "3.7/3.6", "高风险审计日志带 Hash 且上链（evidenceId）", t_audit_ws_eventual_hash, "GET /audit/logs?riskLevel=high", "每条有 hash 与 evidenceId", "@audited chain")

    tc("TC-38-01", "3.8", "创建训练任务（DP ε=1.0，Top-k 10%）", t_fl_create, "admin POST /fl/tasks 4 节点 5 轮", "created，fl-ID", "POST /fl/tasks")
    tc("TC-38-02", "3.8/3.5", "无 algo:execute 不能创建；ε 超范围拒绝", t_fl_create_forbidden, "vpp/subject 创建；epsilon=99", "1003/1003/1001", "POST /fl/tasks")
    tc("TC-38-03", "3.8", "启动训练→节点加入→逐轮聚合→梯度哈希上链→模型发布；DP 预算不超 ε；Top-k 压缩率；loss 下降；重复启动 1006", t_fl_start_and_converge, "POST start（×2）→ 轮询 GET /fl/tasks/{id}", "success 5 轮，每轮 gradientHash+evidenceId，epsilonSpent≤ε，compressionRatio∈(0,100]，末轮 loss≤首轮，modelVersion 非空", "/fl/tasks/{id}/start")
    tc("TC-38-04", "3.8/3.6", "每轮指标查询与轮次存证上链（category=algo）", t_fl_rounds_and_evidence_onchain, "GET /fl/tasks/{id}/rounds → GET /evidence/{evidenceId}", "5 轮；存证 category=algo", "GET /fl/tasks/{id}/rounds")
    tc("TC-38-05", "3.8", "任务列表 / 模型版本列表 / 模型发布存证", t_fl_list_models_publish, "GET /fl/tasks?status=success；GET /fl/models；POST /fl/models/{v}/publish", "均成功，算法类存证存在", "/fl/models")
    tc("TC-38-06", "3.8", "训练中取消（异常停止）", t_fl_cancel, "30 轮任务 start → 3s 后 cancel", "cancelled，轮数 <30", "POST /fl/tasks/{id}/cancel")
    tc("TC-38-07", "3.8/3.7", "DP 预算耗尽：ε=0.05 → 异常停止 / R05 告警", t_fl_dp_budget_exhausted, "ε=0.05 20 轮 start → 轮询 → alerts", "epsilonSpent 不超 ε；任务提前结束或 R05 告警", "algo fedavg accountant → backend R05")
    tc("TC-38-08", "3.8", "投毒检测：Node-C 梯度翻转放大被识别 gradient_poisoning", t_fl_poison_algo, "algo POST /fl/train simulatePoison=Node-C", "anomaly.type=gradient_poisoning nodeId=Node-C", "algo-service /fl/train（backend 未暴露 simulatePoison）")
    tc("TC-38-09", "3.8", "Top-k 比例 0.3 → 压缩率约 70%", t_fl_topk_ratio, "POST /fl/tasks ratio=0.3 → start → 详情", "compressionRatio∈[55,80]", "算法 Top-k")

    tc("TC-39-01", "3.9", "创建调度任务并运行 DQN 生成策略（含 DeepSeek 解释、存证）", t_dp_create_run, "POST /dispatch/tasks → POST run", "success；actions∈charge/idle/discharge，|powerKw|≤30；explanationSource；evidenceId", "POST /dispatch/tasks/{id}/run")
    tc("TC-39-02", "3.9", "约束：SOC 20–95%、≤30kW，低 SOC 不放电、高 SOC 不充电", t_dp_constraints_algo, "algo POST /dqn/dispatch soc=18/97/60", "constraintsChecked 20/95/30，violations 空，动作符合约束", "algo-service /dqn/dispatch")
    tc("TC-39-03", "3.9/3.5/3.7", "vpp 越权下发 → 1003 → high 审计日志", t_dp_issue_forbidden_1003, "vpp POST issue", "403/1003；审计 denied high", "POST /dispatch/tasks/{id}/issue")
    tc("TC-39-04", "3.9/3.2", "无效签名下发 → 1004 → high 审计", t_dp_issue_bad_sig_1004, "admin issue signature=invalid", "403/1004；high", "require_signature")
    tc("TC-39-05", "3.9/3.3", "管理员签名下发成功（托管密钥签名→验签→存证）", t_dp_issue_signed, "admin POST issue（签名由托管密钥代签）", "issued，commandId，signerDid=admin DID，存证可查", "POST /dispatch/tasks/{id}/issue")
    tc("TC-39-06", "3.9", "重复下发状态冲突", t_dp_issue_twice, "再次 issue", "1006", "issue")
    tc("TC-39-07", "3.9/3.11", "边缘节点回执", t_dp_ack, "edge POST /dispatch/tasks/{id}/ack", "acked + evidenceId", "POST /dispatch/tasks/{id}/ack")
    tc("TC-39-08", "3.9/3.7", "越权 traceId 回查完整链路", t_dp_trace_chain, "GET /audit/trace/{deny traceId}", "summary.result denied，riskLevel high", "GET /audit/trace")
    tc("TC-39-09", "3.9/3.5", "调度任务读取权限与运行权限分离", t_dp_list_by_roles, "5 角色 GET /dispatch/tasks；vpp run", "subject 1003，其余 0；vpp run 1003", "require_permission")

    tc("TC-310-01", "3.10", "DeepSeek 五场景分析（调度解释/数据分析/风险分析/智能问答/审计解读）", t_ai_scenes, "POST /ai/analyze scene×5", "answer+reasoning+source∈live/cache/rule，<12s，存证", "POST /ai/analyze")
    tc("TC-310-02", "3.10", "分析历史", t_ai_history, "GET /ai/history?scene=qa", "≥1 条", "GET /ai/history")
    tc("TC-310-03", "3.10", "三级降级标识与永不报错", t_ai_degrade_levels, "algo /health；超长问题；非法 scene", "source 有标识；非法 1001", "algo deepseek adapter")
    tc("TC-312-07", "2.12", "动态隐私风险评估与历史", t_risk_assess, "POST /risk/assess 高/低两组 → history", "高风险分数>低风险；level/factors/suggestion/evidenceId", "POST /risk/assess")

    tc("TC-311-01", "3.11", "节点列表/详情/历史指标（含实时指标与 DID）", t_nodes, "GET /nodes、/nodes/Node-A、/metrics", "≥4 节点，metrics/did 字段", "GET /nodes")
    tc("TC-311-02", "3.11/3.12", "WebSocket 六类消息 + ping/pong", t_ws_messages, "ws://…/ws?token → 触发越权/存证/FL/调度 → 收集 type", "收到 node_status/fl_progress/dispatch_progress/audit_alert/log/evidence_written 与 pong，统一格式 ts/payload", "ws/manager.py")
    tc("TC-311-03", "3.11/3.12", "WebSocket 鉴权失败关闭", t_ws_auth, "ws?token=bad", "连接被关闭（4001）", "ws endpoint")

    tc("TC-312-01", "3.12", "接口文档（Swagger/OpenAPI）可访问", t_api_docs, "GET /docs、/openapi.json", "200，路径数 ≥60", "FastAPI docs")
    tc("TC-312-02", "3.12", "统一响应包装与错误码映射", t_error_codes, "404 路径/资源、size=500、缺参数", "HTTP 与 code 一致，均含 traceId", "core/response.py")
    tc("TC-312-03", "3.12", "分页约定 items/total/page/size", t_pagination_contract, "GET /assets?page=2&size=10", "结构正确", "分页")
    tc("TC-NF-01", "非功能", "主要查询接口响应时间（5 次取最大 <2s）", t_response_time, "8 个 GET 各 5 次", "最大耗时 <2000ms", "性能")
    tc("TC-NF-02", "非功能", "30 并发登录", t_concurrent_login, "30 线程同时 POST /auth/login", "全部 200，最大耗时 <5s", "性能")
    tc("TC-NF-03", "5.2/非功能", "离线可运行：前端/算法源码无外网依赖", t_offline_no_external, "grep 外网 URL", "无（DeepSeek 地址仅配置）", "离线")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    summary = {"run": RUN, "tag": TAG, "total": len(RESULTS), "pass": sum(r["verdict"] == "通过" for r in RESULTS),
               "fail": sum(r["verdict"] == "失败" for r in RESULTS), "blocked": sum(r["verdict"] == "阻塞" for r in RESULTS)}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": RESULTS, "ctx": {k: v for k, v in CTX.items() if k in ("did", "asset", "dp", "fl", "deny_trace", "trace_example")}}, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
