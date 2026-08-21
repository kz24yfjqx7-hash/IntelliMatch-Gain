#!/usr/bin/env python3
"""
契约逐字段一致性审计（可重复运行）。

用法：
    .venv/bin/python qa/contract_audit.py                 # 全量（mock 探针 + views 扫描 + algo 若可达）
    .venv/bin/python qa/contract_audit.py --algo-port 8197   # 指定已启动的 algo 服务端口
    .venv/bin/python qa/contract_audit.py --start-algo       # 自行起 uvicorn（测完按 pid 杀掉）
    .venv/bin/python qa/contract_audit.py --json out.json    # 额外输出机器可读结果

检查项：
  A1 契约第二部分「方法+路径」 ⇄ frontend/src/api/*.js 调用 ⇄ mocks/handlers/*.js handler（缺失/多余/拼写），
     以及 handler 注册顺序（静态路径必须先于能匹配它的动态路径）。
  A2 契约示例 JSON 字段集合 ⇄ mock 真实返回（缺字段 / 类型不符 / 枚举越界 / 时间格式 / 分页结构）。
  A3 views 引用了契约（含 mock 文档化扩展字段）之外的字段 → 粗扫清单。
  A4 algo 服务 6 个接口真实返回 ⇄ 契约第三部分；以及 mock 中 FL/DQN/AI/classify/risk 形态 ⇄ algo 真实返回。
  B1 RBAC：6 账号 × 全部写接口按 DB-SCHEMA 矩阵断言 0/1003；仅自有约束；无 token / 伪造 / 过期 → 1002；tamper 非 sys_admin → 1003。
退出码：有「阻断级」不一致（缺失接口、缺字段、类型/枚举/时间格式错误、RBAC 违例）时为 1。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contract" / "API-CONTRACT.md"
FRONTEND = ROOT / "frontend"
API_DIR = FRONTEND / "src" / "api"
HANDLER_DIR = FRONTEND / "src" / "mocks" / "handlers"
VIEWS_DIR = FRONTEND / "src" / "views"
COMPONENTS_DIR = FRONTEND / "src" / "components"
ALGO_DIR = ROOT / "algo-service"
PY = ROOT / ".venv" / "bin" / "python"

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?[+-]\d{2}:\d{2}$")
TIME_KEYS = re.compile(r"^(ts|at|timestamp|created|updated|[a-z]+[a-z]At)$")

ENUMS = {
    "roles": {"sys_admin", "grid_dispatcher", "vpp_operator", "energy_subject", "regulator", "edge_node"},
    "subjectType": {"user", "device", "org", "edge"},
    "didStatus": {"active", "frozen", "revoked"},
    "dataType": {"pv", "wind", "storage", "load", "dispatch"},
    "level": {"L1", "L2", "L3", "L4"},
    "resourceType": {"asset", "model", "dispatch", "evidence", "algo"},
    "appStatus": {"pending", "approved", "rejected", "expired"},
    "category": {"data", "identity", "permission", "audit", "algo"},
    "riskLevel": {"low", "medium", "high", "critical"},
    "taskStatus": {"created", "running", "success", "failed", "cancelled"},
    "nodeStatus": {"online", "warning", "offline"},
    "source": {"live", "cache", "rule"},
    "dqnAction": {"charge", "idle", "discharge"},
    "algorithm": {"SM2", "ECC", "RSA"},
}

# ---------------------------------------------------------------- 工具
class Report:
    def __init__(self):
        self.blocking: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []
        self.sections: dict[str, dict] = {}

    def block(self, msg):
        self.blocking.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def note(self, msg):
        self.info.append(msg)


def norm_path(p: str) -> str:
    """把 {id} / :id / ${id} 统一成 {}，去掉查询串"""
    p = p.split("?")[0].strip()
    p = re.sub(r"\$\{[^}]+\}", "{}", p)
    p = re.sub(r"\{[^}]+\}", "{}", p)
    p = re.sub(r":[A-Za-z_]\w*", "{}", p)
    return p.rstrip("/") or "/"


def shape(v):
    """值 → 形状：dict 递归、list 取首元素、标量取类型名"""
    if isinstance(v, dict):
        return {k: shape(x) for k, x in v.items()}
    if isinstance(v, list):
        return [shape(v[0])] if v else []
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    return "string"


def diff_shape(exp, got, path=""):
    """对比契约形状 exp 与实际形状 got。返回 (missing, extra, type_mismatch)"""
    missing, extra, mism = [], [], []
    if isinstance(exp, dict):
        if not isinstance(got, dict):
            mism.append(f"{path or '$'}: 期望 object，实际 {got if isinstance(got, str) else type(got).__name__}")
            return missing, extra, mism
        for k, ev in exp.items():
            if k not in got:
                missing.append(f"{path}.{k}" if path else k)
            else:
                m, e, t = diff_shape(ev, got[k], f"{path}.{k}" if path else k)
                missing += m; extra += e; mism += t
        for k in got:
            if k not in exp:
                extra.append(f"{path}.{k}" if path else k)
    elif isinstance(exp, list):
        if not isinstance(got, list):
            mism.append(f"{path}: 期望 array，实际 {got}")
        elif exp and got:
            m, e, t = diff_shape(exp[0], got[0], path + "[]")
            missing += m; extra += e; mism += t
    else:
        if isinstance(got, (dict, list)):
            mism.append(f"{path}: 期望 {exp}，实际 {'object' if isinstance(got, dict) else 'array'}")
        elif exp != got and got != "null" and exp != "null":
            mism.append(f"{path}: 期望 {exp}，实际 {got}")
    return missing, extra, mism


def tolerant_json(text: str):
    """契约示例里带 // 注释、... 省略号，尽量解析成 JSON"""
    t = re.sub(r"(^|\s)//\s[^\n]*", "", text)
    t = re.sub(r'"[^"\n]*\.\.\.[^"\n]*"', '"..."', t)  # 字符串内的省略号保留为字符串
    t = re.sub(r"\{\s*\.\.\.\s*\}", "{}", t)
    t = re.sub(r",\s*\.\.\.", "", t)
    t = re.sub(r"\.\.\.\s*,", "", t)
    t = re.sub(r"\.\.\.", "", t)
    t = re.sub(r",\s*([}\]])", r"\1", t)
    return json.loads(t)


def extract_balanced(lines, start_line, start_col):
    """从 lines[start_line][start_col]（'{'）起做括号匹配，返回文本"""
    depth = 0
    buf = []
    in_str = False
    esc = False
    for li in range(start_line, len(lines)):
        line = lines[li]
        seg = line[start_col:] if li == start_line else line
        out = []
        for ch in seg:
            out.append(ch)
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{" or ch == "[":
                depth += 1
            elif ch == "}" or ch == "]":
                depth -= 1
                if depth == 0:
                    buf.append("".join(out))
                    return "\n".join(buf)
        # 去掉行尾 // 注释后再进入下一行（注释里可能有括号）
        buf.append("".join(out))
    return "\n".join(buf)


# ---------------------------------------------------------------- 解析契约
def parse_contract():
    text = CONTRACT.read_text(encoding="utf8")
    part2 = text.split("## 第二部分")[1].split("## 第三部分")[0]
    part3 = text.split("## 第三部分")[1].split("## 第四部分")[0]

    endpoints = []  # (method, path, desc, paged)
    for m in re.finditer(r"^\|\s*(GET|POST|PUT|DELETE)\s*\|\s*`([^`]+)`\s*\|\s*([^|]*)\|", part2, re.M):
        method, path, desc = m.group(1), m.group(2).strip(), m.group(3).strip()
        endpoints.append({"method": method, "path": path, "norm": norm_path(path), "desc": desc, "paged": "分页" in desc, "stream": "文件流" in desc})

    def parse_examples(section, wrapped):
        """返回 { 'METHOD norm_path': {'request': shape|None, 'response': shape|None, 'raw': str} }"""
        out = {}
        blocks = re.findall(r"```\n(.*?)```", section, re.S)
        for blk in blocks:
            lines = blk.split("\n")
            cur = None
            i = 0
            while i < len(lines):
                line = lines[i]
                mm = re.match(r"^(GET|POST|PUT|DELETE)\s+(/\S+)", line)
                if mm:
                    cur = f"{mm.group(1)} {norm_path(mm.group(2).replace('/algo/v1', ''))}"
                    out.setdefault(cur, {"request": None, "response": None})
                    i += 1
                    continue
                if cur and (line.lstrip().startswith("响应") or line.lstrip().startswith("请求")):
                    kind = "response" if line.lstrip().startswith("响应") else "request"
                    col = line.find("{")
                    if kind == "response" and wrapped:
                        # 形如 "响应  data: {"
                        dcol = line.find("data:")
                        if dcol >= 0:
                            col = line.find("{", dcol)
                        elif "data:" not in line:
                            i += 1
                            continue
                    if col < 0:
                        i += 1
                        continue
                    raw = extract_balanced(lines, i, col)
                    try:
                        obj = tolerant_json(raw)
                        out[cur][kind] = shape(obj)
                        out[cur][kind + "_raw"] = raw
                    except Exception as e:  # noqa: BLE001
                        out[cur][kind + "_error"] = f"{e}: {raw[:80]}"
                    i += raw.count("\n") + 1
                    continue
                i += 1
        return out

    ex2 = parse_examples(part2, wrapped=True)
    ex3 = parse_examples(part3, wrapped=False)
    return endpoints, ex2, ex3


# ---------------------------------------------------------------- A1 扫描 api / handlers
def scan_api_js():
    found = {}  # "METHOD norm" -> file:line
    for f in sorted(API_DIR.glob("*.js")):
        if f.name in ("request.js", "ws.js", "index.js"):
            continue
        for ln, line in enumerate(f.read_text(encoding="utf8").splitlines(), 1):
            m = re.search(r"request\.(get|post|put|delete)\(\s*[`'\"]([^`'\"]+)[`'\"]", line)
            if m:
                found[f"{m.group(1).upper()} {norm_path(m.group(2))}"] = f"{f.name}:{ln}"
            m = re.search(r"download\(\s*[`'\"]([^`'\"]+)[`'\"]", line)
            if m:
                found[f"GET {norm_path(m.group(1))}"] = f"{f.name}:{ln}"
    return found


def scan_handlers():
    """返回有序列表 [(method, raw_pattern, norm, file:line)]，顺序 = index.js 汇总顺序"""
    index = (HANDLER_DIR / "index.js").read_text(encoding="utf8")
    order = re.findall(r"from\s+'\./(\w+)\.js'", index)
    result = []
    for mod in order:
        f = HANDLER_DIR / f"{mod}.js"
        for ln, line in enumerate(f.read_text(encoding="utf8").splitlines(), 1):
            m = re.search(r"http\.(get|post|put|delete)\(\s*`\$\{BASE\}([^`]+)`", line)
            if m:
                result.append((m.group(1).upper(), m.group(2), norm_path(m.group(2)), f"{f.name}:{ln}"))
    return result


def pattern_to_regex(raw: str):
    return re.compile("^" + re.sub(r":\w+", r"[^/]+", re.escape(raw).replace(r"\:", ":")) + "$")


def check_handler_order(handlers, rep: Report):
    """静态路径必须先于能匹配它的动态路径"""
    seen_dynamic = []  # (method, regex, where)
    for method, raw, norm, where in handlers:
        is_dynamic = ":" in raw
        if not is_dynamic:
            for m2, rx, where2 in seen_dynamic:
                if m2 == method and rx.match(raw):
                    rep.block(f"[A1-order] 静态路径 {method} {raw}（{where}）注册在动态路径（{where2}）之后，会被动态路径截获")
        else:
            seen_dynamic.append((method, pattern_to_regex(raw), f"{where} {raw}"))


# ---------------------------------------------------------------- A2/B1 mock 探针（Node 内跑 MSW）
PROBE_JS = r"""
import { server } from './src/mocks/node.js'
import { db } from './src/mocks/db.js'
import { issueToken } from './src/mocks/helpers.js'
const BASE = ['http:', '', 'localhost', 'api', 'v1'].join('/')
server.listen({ onUnhandledRequest: 'error' })
const out = { endpoints: {}, rbac: {}, own: {}, tokens: {} }
async function call(method, path, { token, body, raw } = {}) {
  const res = await fetch(BASE + path, { method, headers: { 'Content-Type': 'application/json', 'X-Trace-Id': 'tr-20260821-0000abcd', ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) })
  if (raw) return { status: res.status, contentType: res.headers.get('content-type'), text: await res.text() }
  return { status: res.status, json: await res.json() }
}
const T = {}
for (const [u, p] of [['admin','admin123'],['grid','grid123'],['vpp','vpp123'],['subject','subject123'],['regulator','reg123'],['edge','edge123']]) {
  const r = await call('POST', '/auth/login', { body: { username: u, password: p } })
  T[u] = r.json.data.token
}
const A = T.admin
function rec(key, r) { out.endpoints[key] = r }

// ---- 逐接口（admin）----
rec('POST /auth/login', await call('POST', '/auth/login', { body: { username: 'admin', password: 'admin123' } }))
rec('GET /auth/me', await call('GET', '/auth/me', { token: A }))
rec('GET /users', await call('GET', '/users?page=1&size=5', { token: A }))
const nu = await call('POST', '/users', { token: A, body: { username: 'audit_u1', password: 'x12345678', realName: '审计用户', roles: ['regulator'] } })
rec('POST /users', nu)
rec('PUT /users/{}', await call('PUT', `/users/${nu.json.data.id}`, { token: A, body: { realName: '审计用户2' } }))
rec('DELETE /users/{}', await call('DELETE', `/users/${nu.json.data.id}`, { token: A }))
const reg = await call('POST', '/did/register', { token: A, body: { subjectType: 'device', subjectName: '审计逆变器-<img src=x onerror=alert(1)>', orgName: 'XX园区', metadata: { model: 'VPP-2000', location: 'A区' } } })
rec('POST /did/register', reg)
const did = reg.json.data.did
rec('GET /did', await call('GET', '/did?subjectType=device&page=1&size=5', { token: A }))
rec('GET /did/{}', await call('GET', `/did/${encodeURIComponent(did)}`, { token: A }))
rec('POST /did/verify', await call('POST', '/did/verify', { token: A, body: { did, message: 'hello', signature: 'deadbeefcafe' } }))
rec('POST /did/{}/rotate-key', await call('POST', `/did/${encodeURIComponent(did)}/rotate-key`, { token: A }))
rec('POST /did/{}/status', await call('POST', `/did/${encodeURIComponent(did)}/status`, { token: A, body: { action: 'freeze', reason: '设备离线超 24 小时' } }))
rec('POST /did/resolve', await call('POST', '/did/resolve', { token: A, body: { dids: [did] } }))
rec('GET /keys', await call('GET', `/keys?did=${encodeURIComponent(did)}`, { token: A }))
const nk = await call('POST', '/keys', { token: A, body: { did: db.nodes[0].did, algorithm: 'SM2' } })
rec('POST /keys', nk)
rec('GET /keys/{}/history', await call('GET', `/keys/${nk.json.data.id}/history`, { token: A }))
rec('POST /keys/{}/freeze', await call('POST', `/keys/${nk.json.data.id}/freeze`, { token: A }))
rec('POST /keys/{}/revoke', await call('POST', `/keys/${nk.json.data.id}/revoke`, { token: A }))
const na = await call('POST', '/assets', { token: A, body: { name: '节点A光伏出力-审计{{7*7}}', dataType: 'pv', sourceDid: db.nodes[0].did, level: 'L2', payload: { pvOutput: 45.3, ts: '2026-08-17T14:00:00+08:00' }, description: '分钟级采集' } })
rec('POST /assets', na)
const assetId = na.json.data.id
rec('GET /assets', await call('GET', '/assets?dataType=pv&page=1&size=5', { token: A }))
rec('GET /assets/{}', await call('GET', `/assets/${assetId}`, { token: A }))
rec('GET /assets/{}/lineage', await call('GET', `/assets/${assetId}/lineage`, { token: A }))
rec('POST /assets/classify', await call('POST', '/assets/classify', { token: A, body: { records: [{ dataType: 'pv', fields: ['power', 'voltage', 'gps'], freq: 'minute', volume: 1440 }] } }))
rec('GET /assets/stats', await call('GET', '/assets/stats', { token: A }))
rec('GET /roles', await call('GET', '/roles', { token: A }))
rec('POST /roles', await call('POST', '/roles', { token: A, body: { code: 'audit_role', name: '审计角色', grants: { asset: ['read'] } } }))
rec('PUT /roles/{}', await call('PUT', '/roles/audit_role', { token: A, body: { name: '审计角色2', grants: { asset: ['read', 'export'] } } }))
rec('GET /permissions/matrix', await call('GET', '/permissions/matrix', { token: A }))
const ap = await call('POST', '/permissions/apply', { token: T.subject, body: { resourceType: 'asset', resourceId: String(assetId), action: 'read', reason: '联合建模需要读取节点A光伏数据', expireAt: '2026-09-17T00:00:00+08:00' } })
rec('POST /permissions/apply', ap)
rec('GET /permissions/applications', await call('GET', '/permissions/applications?status=pending', { token: A }))
rec('POST /permissions/applications/{}/approve', await call('POST', `/permissions/applications/${ap.json.data.id}/approve`, { token: A, body: { comment: 'ok' } }))
const ap2 = await call('POST', '/permissions/apply', { token: T.subject, body: { resourceType: 'asset', resourceId: '1002', action: 'export', reason: 'x' } })
rec('POST /permissions/applications/{}/reject', await call('POST', `/permissions/applications/${ap2.json.data.id}/reject`, { token: A, body: { reason: 'no' } }))
const gl = await call('GET', `/permissions/grants?did=${encodeURIComponent(ap.json.data.applicantDid)}`, { token: A })
rec('GET /permissions/grants', gl)
const g = gl.json.data.items.find(x => String(x.resourceId) === String(assetId))
rec('POST /permissions/grants/{}/revoke', await call('POST', `/permissions/grants/${g.id}/revoke`, { token: A }))
rec('POST /permissions/check', await call('POST', '/permissions/check', { token: A, body: { did: db.users[2].did, resourceType: 'dispatch', resourceId: 'task-9', action: 'issue' } }))
const ev = await call('POST', '/evidence', { token: A, body: { category: 'data', refId: String(assetId), payload: { pvOutput: 1.5 }, actorDid: db.users[0].did } })
rec('POST /evidence', ev)
const evId = ev.json.data.evidenceId
rec('GET /evidence', await call('GET', '/evidence?category=data&page=1&size=5', { token: A }))
rec('GET /evidence/{}', await call('GET', `/evidence/${evId}`, { token: A }))
rec('GET /evidence/{}/certificate', await call('GET', `/evidence/${evId}/certificate`, { token: A }))
rec('GET /evidence/chain/status', await call('GET', '/evidence/chain/status', { token: A }))
rec('POST /evidence/demo/tamper', await call('POST', '/evidence/demo/tamper', { token: A, body: { evidenceId: evId, newValue: { pvOutput: 999.9 } } }))
rec('POST /evidence/verify', await call('POST', '/evidence/verify', { token: A, body: { evidenceId: evId } }))
rec('GET /evidence/trace/{}', await call('GET', `/evidence/trace/${db.DEMO_TRACE}`, { token: A }))
rec('GET /audit/logs', await call('GET', '/audit/logs?riskLevel=high&page=1&size=5', { token: A }))
rec('GET /audit/trace/{}', await call('GET', `/audit/trace/${db.DEMO_TRACE}`, { token: A }))
rec('GET /audit/alerts', await call('GET', '/audit/alerts?status=open', { token: A }))
const al = await call('GET', '/audit/alerts?status=open', { token: A })
rec('POST /audit/alerts/{}/ack', await call('POST', `/audit/alerts/${al.json.data.items[0].id}/ack`, { token: A }))
rec('GET /audit/report', await call('GET', `/audit/report?period=day&date=${new Date().toISOString().slice(0, 10)}`, { token: A }))
rec('GET /audit/stats', await call('GET', '/audit/stats', { token: A }))
rec('GET /audit/logs/export', await call('GET', '/audit/logs/export?riskLevel=high', { token: A, raw: true }))
rec('GET /nodes', await call('GET', '/nodes', { token: A }))
rec('GET /nodes/{}', await call('GET', '/nodes/Node-A', { token: A }))
rec('GET /nodes/{}/metrics', await call('GET', '/nodes/Node-A/metrics?interval=day', { token: A }))
rec('POST /nodes/{}/online', await call('POST', '/nodes/Node-A/online', { token: A, body: { did: db.nodes[0].did, nonce: 'abc123', signature: 'deadbeefcafe' } }))
const fl = await call('POST', '/fl/tasks', { token: A, body: { name: '负荷预测联合建模-审计', nodeIds: ['Node-A', 'Node-B', 'Node-C', 'Node-D'], rounds: 3, dp: { enabled: true, epsilon: 1.0, delta: 1e-5 }, topk: { enabled: true, ratio: 0.1 } } })
rec('POST /fl/tasks', fl)
const flId = fl.json.data.id
rec('POST /fl/tasks/{}/start', await call('POST', `/fl/tasks/${flId}/start`, { token: A }))
let flTask
for (let i = 0; i < 200; i++) { flTask = await call('GET', `/fl/tasks/${flId}`, { token: A }); if (['success', 'failed'].includes(flTask.json.data.status)) break; await new Promise(r => setTimeout(r, 100)) }
rec('GET /fl/tasks/{}', flTask)
rec('GET /fl/tasks', await call('GET', '/fl/tasks?page=1&size=5', { token: A }))
rec('GET /fl/tasks/{}/rounds', await call('GET', `/fl/tasks/${flId}/rounds`, { token: A }))
const fl2 = await call('POST', '/fl/tasks', { token: A, body: { name: 'cancel', nodeIds: ['Node-A'], rounds: 50 } })
await call('POST', `/fl/tasks/${fl2.json.data.id}/start`, { token: A })
rec('POST /fl/tasks/{}/cancel', await call('POST', `/fl/tasks/${fl2.json.data.id}/cancel`, { token: A }))
rec('GET /fl/models', await call('GET', '/fl/models', { token: A }))
rec('POST /fl/models/{}/publish', await call('POST', `/fl/models/${flTask.json.data.modelVersion}/publish`, { token: A }))
const dp = await call('POST', '/dispatch/tasks', { token: A, body: { name: '审计调度', nodeIds: ['Node-A', 'Node-B', 'Node-C', 'Node-D'] } })
rec('POST /dispatch/tasks', dp)
const dpId = dp.json.data.id
rec('POST /dispatch/tasks/{}/run', await call('POST', `/dispatch/tasks/${dpId}/run`, { token: A }))
rec('GET /dispatch/tasks', await call('GET', '/dispatch/tasks', { token: A }))
rec('GET /dispatch/tasks/{}', await call('GET', `/dispatch/tasks/${dpId}`, { token: A }))
rec('POST /dispatch/tasks/{}/issue', await call('POST', `/dispatch/tasks/${dpId}/issue`, { token: A, body: { signature: 'sig:0123456789abcdef' } }))
rec('POST /dispatch/tasks/{}/ack', await call('POST', `/dispatch/tasks/${dpId}/ack`, { token: A, body: { nodeId: 'Node-C', status: 'success' } }))
rec('POST /ai/analyze', await call('POST', '/ai/analyze', { token: A, body: { scene: 'dispatch', context: { taskId: dpId }, question: '为什么选择节点C放电？<script>alert(1)</script>' } }))
rec('GET /ai/history', await call('GET', '/ai/history', { token: A }))
rec('POST /risk/assess', await call('POST', '/risk/assess', { token: A, body: { nodeId: 'Node-A', features: { queryFreq: 12, dataGranularity: 'minute', exposedFields: 6 } } }))
rec('GET /risk/history', await call('GET', '/risk/history?nodeId=Node-A', { token: A }))
rec('POST /auth/logout', await call('POST', '/auth/logout', { token: T.admin }))
T.admin = (await call('POST', '/auth/login', { body: { username: 'admin', password: 'admin123' } })).json.data.token

// ---- B1 RBAC：每个账号 × 写接口（各自新建前置数据，避免状态冲突）----
const users = ['admin', 'grid', 'vpp', 'subject', 'regulator', 'edge']
async function code(method, path, user, body) { const r = await call(method, path, { token: T[user], body }); return r.json.code }
for (const u of users) {
  const R = {}
  R['POST /assets'] = await code('POST', '/assets', u, { name: `rbac-${u}`, dataType: 'pv', payload: { v: 1 } })
  R['POST /fl/tasks'] = await code('POST', '/fl/tasks', u, { name: `rbac-${u}`, nodeIds: ['Node-A'], rounds: 1 })
  R['POST /fl/tasks/{}/start'] = await code('POST', `/fl/tasks/${flId}/start`, u)
  R['POST /fl/tasks/{}/cancel'] = await code('POST', `/fl/tasks/${flId}/cancel`, u)
  R['POST /fl/models/{}/publish'] = await code('POST', `/fl/models/v11/publish`, u)
  R['POST /dispatch/tasks'] = await code('POST', '/dispatch/tasks', u, { name: `rbac-${u}` })
  R['POST /dispatch/tasks/{}/issue'] = await code('POST', `/dispatch/tasks/${dpId}/issue`, u, { signature: 'sig:0123456789abcdef' })
  R['POST /users'] = await code('POST', '/users', u, { username: `rb_${u}`, password: 'x12345678' })
  R['PUT /users/{}'] = await code('PUT', '/users/2', u, { realName: 'x' })
  R['DELETE /users/{}'] = await code('DELETE', '/users/999', u)
  R['POST /roles'] = await code('POST', '/roles', u, { code: `rb_${u}`, name: 'x' })
  R['PUT /roles/{}'] = await code('PUT', '/roles/audit_role', u, { name: 'y' })
  R['POST /permissions/applications/{}/approve'] = await code('POST', '/permissions/applications/999/approve', u, {})
  R['POST /permissions/applications/{}/reject'] = await code('POST', '/permissions/applications/999/reject', u, {})
  R['POST /permissions/grants/{}/revoke'] = await code('POST', '/permissions/grants/999/revoke', u)
  R['POST /evidence/demo/tamper'] = await code('POST', '/evidence/demo/tamper', u, { evidenceId: 'ev-000001', newValue: {} })
  R['GET /users'] = await code('GET', '/users', u)
  R['GET /audit/logs/export'] = (await call('GET', '/audit/logs/export', { token: T[u], raw: true })).status
  R['GET /evidence'] = await code('GET', '/evidence?size=1', u)
  R['GET /fl/models'] = await code('GET', '/fl/models', u)
  R['GET /dispatch/tasks'] = await code('GET', '/dispatch/tasks', u)
  R['GET /assets'] = await code('GET', '/assets?size=1', u)
  out.rbac[u] = R
}

// ---- B1 仅自有：subject / edge 的资产与存证可见范围 ----
for (const u of ['subject', 'edge']) {
  const me = db.users.find(x => x.username === u)
  const assets = (await call('GET', '/assets?size=200', { token: T[u] })).json.data
  const grants = db.grants.filter(g => g.did === me.did && g.status === 'active' && g.resourceType === 'asset').map(g => g.resourceId)
  const foreign = assets.items.filter(a => a.sourceDid !== me.did && a.ownerDid !== me.did && !grants.includes(String(a.id)) && !grants.includes('*'))
  const other = db.assets.find(a => a.sourceDid !== me.did && !grants.includes(String(a.id)))
  const detail = await call('GET', `/assets/${other.id}`, { token: T[u] })
  const lineage = await call('GET', `/assets/${other.id}/lineage`, { token: T[u] })
  const evs = await call('GET', '/evidence?size=200', { token: T[u] })
  const myDids = new Set([me.did, ...db.dids.filter(d => d.controllerDid === me.did).map(d => d.did)])
  const myAssets = new Set(db.assets.filter(a => myDids.has(a.sourceDid) || a.ownerDid === me.did).map(a => String(a.id)))
  const foreignEv = evs.json.code === 0 ? evs.json.data.items.filter(e => !myDids.has(e.actorDid) && !myDids.has(e.refId) && !myAssets.has(String(e.refId))) : []
  const otherEv = await call('GET', `/evidence/ev-000001`, { token: T[u] })
  out.own[u] = { assetTotal: assets.total, foreignAssets: foreign.length, foreignDetailCode: detail.json.code, foreignLineageCode: lineage.json.code, evidenceCode: evs.json.code, evidenceTotal: evs.json.code === 0 ? evs.json.data.total : null, foreignEvidence: foreignEv.length, foreignEvidenceDetailCode: otherEv.json.code }
}

// ---- B1 token：无 / 伪造 / 过期 / 已登出 ----
const biz = ['/nodes', '/assets', '/evidence/chain/status', '/audit/logs', '/fl/tasks', '/dispatch/tasks', '/did', '/keys', '/roles', '/ai/history', '/risk/history', '/permissions/matrix']
out.tokens.noToken = {}
for (const p of biz) out.tokens.noToken[p] = (await call('GET', p)).json.code
const b64 = s => Buffer.from(s).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
const forged = `${b64(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))}.${b64(JSON.stringify({ sub: 1, username: 'admin', roles: ['sys_admin'], did: db.users[0].did, iat: 1, exp: Math.floor(Date.now() / 1000) + 9999 }))}.forgedsignature0000000000000000000000000000`
out.tokens.forged = (await call('GET', '/auth/me', { token: forged })).json.code
out.tokens.forgedWrite = (await call('POST', '/evidence/demo/tamper', { token: forged, body: { evidenceId: 'ev-000001' } })).json.code
out.tokens.garbage = (await call('GET', '/nodes', { token: 'not.a.jwt' })).json.code
const expired = issueToken(db.users[0], -10)
out.tokens.expired = (await call('GET', '/auth/me', { token: expired })).json.code
const tmp = (await call('POST', '/auth/login', { body: { username: 'grid', password: 'grid123' } })).json.data.token
await call('POST', '/auth/logout', { token: tmp })
out.tokens.afterLogout = (await call('GET', '/auth/me', { token: tmp })).json.code
out.tokens.tamperByVpp = (await call('POST', '/evidence/demo/tamper', { token: T.vpp, body: { evidenceId: 'ev-000001' } })).json.code
out.tokens.wrongPassword = (await call('POST', '/auth/login', { body: { username: 'admin', password: 'nope' } })).json.code

server.close()
const fs = await import('node:fs')
fs.writeFileSync(process.env.AUDIT_OUT, JSON.stringify(out))
process.exit(0)
"""


def run_mock_probe(rep: Report):
    probe = FRONTEND / ".contract-audit-probe.tmp.mjs"
    out = Path(tempfile.mkstemp(prefix="contract-probe-", suffix=".json")[1])
    probe.write_text(PROBE_JS, encoding="utf8")
    try:
        r = subprocess.run(["node", str(probe)], cwd=FRONTEND, capture_output=True, text=True, timeout=300, env={**os.environ, "AUDIT_OUT": str(out)})
        if r.returncode != 0:
            rep.block(f"[A2] mock 探针执行失败：{r.stderr[-1500:]}")
            return None
        return json.loads(out.read_text(encoding="utf8"))
    except Exception as e:  # noqa: BLE001
        rep.block(f"[A2] mock 探针输出无法解析：{e}")
        return None
    finally:
        probe.unlink(missing_ok=True)
        out.unlink(missing_ok=True)


def walk_values(obj, path="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_values(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            yield from walk_values(v, f"{path}[{i}]")
    else:
        yield path, obj


ENUM_BY_KEY = {
    "subjectType": "subjectType", "dataType": "dataType", "level": "level", "riskLevel": "riskLevel", "category": "category",
    "didStatus": "didStatus", "resourceType": "resourceType", "source": "source", "explanationSource": "source", "narrativeSource": "source",
    "algorithm": "algorithm",
}


def check_enums(key: str, data, rep: Report):
    for path, v in walk_values(data):
        leaf = path.rsplit(".", 1)[-1].split("[")[0]
        if leaf in ENUM_BY_KEY and isinstance(v, str):
            # algorithm 枚举只约束密钥；resourceType 枚举只约束权限接口（审计日志的 resource_type 在 DB-SCHEMA 中为自由文本）
            if leaf == "algorithm" and "/keys" not in key:
                continue
            if leaf == "resourceType" and "/permissions" not in key:
                continue
            # level 在 risk 场景是 riskLevel 枚举，在资产场景是 L1~L4
            allowed = ENUMS[ENUM_BY_KEY[leaf]]
            if leaf == "level":
                allowed = ENUMS["level"] | ENUMS["riskLevel"]
            if v not in allowed:
                rep.block(f"[A2-enum] {key} {path} = {v!r} 不在枚举 {sorted(allowed)}")
        if leaf == "roles" and isinstance(v, str) and v not in ENUMS["roles"] and not v.startswith("audit_"):
            rep.block(f"[A2-enum] {key} {path} 角色 {v!r} 越界")
        if leaf == "status" and isinstance(v, str) and "health" not in key:
            allowed = ENUMS["didStatus"] | ENUMS["appStatus"] | ENUMS["taskStatus"] | ENUMS["nodeStatus"] | {"open", "acked", "issued", "draft", "published", "disabled", "active"}
            if v not in allowed:
                rep.block(f"[A2-enum] {key} {path} status={v!r} 不在任何契约枚举内")
        if leaf == "action" and isinstance(v, str) and "dispatch" in key and "strategy" in path and v not in ENUMS["dqnAction"]:
            rep.block(f"[A2-enum] {key} {path} action={v!r} 不在 charge|idle|discharge")
        if TIME_KEYS.search(leaf) and isinstance(v, str) and v and leaf not in ("ts",) and not ISO_RE.match(v):
            rep.block(f"[A2-time] {key} {path} = {v!r} 不是 ISO8601+时区")
        if leaf == "ts" and isinstance(v, str) and v and not ISO_RE.match(v):
            rep.block(f"[A2-time] {key} {path} = {v!r} 不是 ISO8601+时区")
        if leaf == "traceId" and isinstance(v, str) and v and not re.match(r"^tr-\d{8}-[0-9a-f]{8}$", v):
            rep.block(f"[A2-fmt] {key} {path} traceId={v!r} 格式错误")


def audit_mock(endpoints, ex2, probe, rep: Report):
    res = probe["endpoints"]
    stats = {"checked": 0, "missing": 0, "mismatch": 0, "extra": 0}
    for ep in endpoints:
        key = f"{ep['method']} {ep['norm']}"
        r = res.get(key)
        if r is None:
            rep.warn(f"[A2] 探针未覆盖 {key}")
            continue
        stats["checked"] += 1
        if ep["stream"]:
            if r["status"] != 200 or "csv" not in (r.get("contentType") or "") or r["text"].lstrip("﻿").startswith("{"):
                rep.block(f"[A2] {key} 应为 CSV 文件流，实际 status={r['status']} type={r.get('contentType')}")
            continue
        body = r["json"]
        for k in ("code", "message", "data", "traceId"):
            if k not in body:
                rep.block(f"[A2-wrap] {key} 响应缺少包装字段 {k}")
        if body.get("code") != 0:
            rep.block(f"[A2] {key} admin 调用失败：code={body.get('code')} {body.get('message')}")
            continue
        if body.get("traceId") != "tr-20260821-0000abcd":
            rep.block(f"[A2-trace] {key} 未沿用 X-Trace-Id")
        data = body["data"]
        if ep["paged"]:
            if not (isinstance(data, dict) and isinstance(data.get("items"), list) and all(isinstance(data.get(k), int) for k in ("total", "page", "size"))):
                rep.block(f"[A2-page] {key} 分页结构应为 {{items,total,page,size}}，实际 keys={list(data) if isinstance(data, dict) else type(data)}")
        ex = ex2.get(key)
        if ex and ex.get("response") is not None:
            missing, extra, mism = diff_shape(ex["response"], shape(data))
            for m in missing:
                rep.block(f"[A2-missing] {key} 缺字段 {m}")
            for m in mism:
                rep.block(f"[A2-type] {key} {m}")
            if extra:
                rep.note(f"[A2-extra] {key} 多出字段（向后兼容，允许）：{', '.join(extra)}")
            stats["missing"] += len(missing); stats["mismatch"] += len(mism); stats["extra"] += len(extra)
        elif ex and ex.get("response_error"):
            rep.warn(f"[A2] 契约示例解析失败 {key}: {ex['response_error']}")
        check_enums(key, data, rep)
    rep.sections["A2"] = stats


# ---------------------------------------------------------------- B1 RBAC 断言
# DB-SCHEMA 矩阵 + 契约说明推导出的「期望允许集合」；未列出的账号期望 1003
EXPECT_ALLOWED = {
    "POST /assets": {"admin", "vpp", "subject", "edge"},                 # asset:write
    "POST /fl/tasks": {"admin", "grid"},                                  # algo:execute
    "POST /fl/tasks/{}/start": {"admin", "grid"},
    "POST /fl/tasks/{}/cancel": {"admin", "grid"},
    "POST /fl/models/{}/publish": {"admin", "grid"},
    "POST /dispatch/tasks": {"admin", "grid", "vpp", "regulator", "edge"},   # dispatch:read（支撑旧页面流程）
    "POST /dispatch/tasks/{}/issue": {"admin", "grid"},                   # dispatch:issue
    "POST /users": {"admin"}, "PUT /users/{}": {"admin"}, "DELETE /users/{}": {"admin"},
    "POST /roles": {"admin"}, "PUT /roles/{}": {"admin"},
    "POST /permissions/applications/{}/approve": {"admin"},
    "POST /permissions/applications/{}/reject": {"admin"},
    "POST /permissions/grants/{}/revoke": {"admin"},
    "POST /evidence/demo/tamper": {"admin"},                              # 仅 sys_admin
    "GET /users": {"admin"},
    "GET /audit/logs/export": {"admin", "grid", "regulator"},             # asset:export
    "GET /evidence": {"admin", "grid", "vpp", "subject", "regulator"},    # evidence:read（edge 无）
    "GET /fl/models": {"admin", "grid", "vpp", "regulator", "edge"},      # model:read（subject 无）
    "GET /dispatch/tasks": {"admin", "grid", "vpp", "regulator", "edge"}, # dispatch:read（subject 无）
    "GET /assets": {"admin", "grid", "vpp", "subject", "regulator", "edge"},
}


def audit_rbac(probe, rep: Report):
    rbac = probe["rbac"]
    violations = 0
    for ep, allowed in EXPECT_ALLOWED.items():
        for u, R in rbac.items():
            c = R.get(ep)
            if ep == "GET /audit/logs/export":
                ok = c == 200
            else:
                ok = c != 1003 and c != 1002
            if u in allowed and not ok:
                rep.block(f"[B1-rbac] {u} 应被允许 {ep}，实际 code={c}")
                violations += 1
            if u not in allowed and (c != 1003 if ep != "GET /audit/logs/export" else c != 403):
                rep.block(f"[B1-rbac] {u} 应被拒绝(1003) {ep}，实际 code={c}")
                violations += 1
    own = probe["own"]
    for u, o in own.items():
        if o["foreignAssets"]:
            rep.block(f"[B1-own] {u} 资产列表含 {o['foreignAssets']} 条非自有/未授权资产"); violations += 1
        if o["foreignDetailCode"] == 0:
            rep.block(f"[B1-own] {u} 可读取非自有资产详情"); violations += 1
        if o["foreignLineageCode"] == 0:
            rep.block(f"[B1-own] {u} 可读取非自有资产溯源"); violations += 1
        if u == "subject":
            if o["evidenceCode"] != 0:
                rep.block(f"[B1-own] subject 应能读自有存证，实际 code={o['evidenceCode']}"); violations += 1
            if o["foreignEvidence"]:
                rep.block(f"[B1-own] subject 存证列表含 {o['foreignEvidence']} 条非自有存证"); violations += 1
            if o["foreignEvidenceDetailCode"] == 0:
                rep.block("[B1-own] subject 可读取非自有存证详情"); violations += 1
        if u == "edge" and o["evidenceCode"] != 1003:
            rep.block(f"[B1-own] edge_node 无 evidence:read，应 1003，实际 {o['evidenceCode']}"); violations += 1
    t = probe["tokens"]
    for p, c in t["noToken"].items():
        if c != 1002:
            rep.block(f"[B1-token] 未带 token 访问 {p} 应 1002，实际 {c}"); violations += 1
    for name, exp in (("forged", 1002), ("forgedWrite", 1002), ("garbage", 1002), ("expired", 1002), ("afterLogout", 1002), ("tamperByVpp", 1003)):
        if t[name] != exp:
            rep.block(f"[B1-token] {name} 应 {exp}，实际 {t[name]}"); violations += 1
    if t["wrongPassword"] not in (1001, 1002):
        rep.block(f"[B1-token] 错误密码应 1001/1002，实际 {t['wrongPassword']}"); violations += 1
    rep.sections["B1"] = {"violations": violations, "users": len(rbac), "endpoints": len(EXPECT_ALLOWED)}


# ---------------------------------------------------------------- A4 algo 真实请求
def http_json(method, url, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", "X-Trace-Id": "tr-20260821-0000abcd"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}"), dict(e.headers)


def start_algo(port: int):
    env = {**os.environ, "FL_ROUND_DELAY": "0", "DEEPSEEK_API_KEY": "", "ALGO_PORT": str(port)}
    log = tempfile.NamedTemporaryFile(prefix="algo-audit-", suffix=".log", delete=False)
    p = subprocess.Popen([str(PY), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ALGO_DIR, stdout=log, stderr=subprocess.STDOUT, env=env)
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/algo/v1/health", timeout=1)
            return p
        except Exception:  # noqa: BLE001
            if p.poll() is not None:
                break
            time.sleep(0.5)
    p.kill()
    raise RuntimeError(f"algo 服务未就绪，日志 {log.name}")


def audit_algo(ex3, probe, port: int, rep: Report):
    base = f"http://127.0.0.1:{port}/algo/v1"
    try:
        st, health, hdr = http_json("GET", base + "/health")
    except Exception as e:  # noqa: BLE001
        rep.warn(f"[A4] algo 服务不可达（{e}），跳过第三部分核对")
        return
    got = {}
    got["GET /health"] = health
    if "X-Trace-Id" not in {k.title(): v for k, v in hdr.items()} and "x-trace-id" not in {k.lower() for k in hdr}:
        rep.block("[A4] algo 响应未回写 X-Trace-Id")
    _, tr, _ = http_json("POST", base + "/fl/train", {"jobId": "fl-audit-1", "rounds": 3, "nodes": [{"id": "Node-A", "samples": 480}, {"id": "Node-B", "samples": 320}], "dp": {"enabled": True, "epsilon": 1.0, "delta": 1e-5}, "topk": {"enabled": True, "ratio": 0.1}})
    got["POST /fl/train"] = tr
    job = None
    for _ in range(200):
        _, job, _ = http_json("GET", base + "/fl/jobs/fl-audit-1")
        if job.get("status") in ("success", "failed"):
            break
        time.sleep(0.1)
    got["GET /fl/jobs/{}"] = job
    http_json("POST", base + "/fl/train", {"jobId": "fl-audit-2", "rounds": 50, "nodes": [{"id": "Node-A"}]})
    _, cancel, _ = http_json("POST", base + "/fl/jobs/fl-audit-2/cancel")
    got["POST /fl/jobs/{}/cancel"] = cancel
    _, dqn, _ = http_json("POST", base + "/dqn/dispatch", {"taskId": "dp-audit", "timeWindow": "2026-08-17T15:00~16:00+08:00", "nodes": [{"id": "Node-A", "pv": 45.3, "load": 120, "soc": 65, "storage": -12.0, "price": 0.62}, {"id": "Node-C", "pv": 28.7, "load": 150, "soc": 18, "storage": -25.3, "price": 0.62}]})
    got["POST /dqn/dispatch"] = dqn
    _, ds, _ = http_json("POST", base + "/deepseek/analyze", {"scene": "dispatch", "context": {"taskId": "dp-audit"}, "question": "为什么选择节点C放电？"})
    got["POST /deepseek/analyze"] = ds
    _, cl, _ = http_json("POST", base + "/classify", {"records": [{"dataType": "pv", "fields": ["power", "voltage", "gps"], "freq": "minute", "volume": 1440}]})
    got["POST /classify"] = cl
    _, rk, _ = http_json("POST", base + "/risk/assess", {"nodeId": "Node-A", "features": {"queryFreq": 12, "dataGranularity": "minute", "exposedFields": 6, "epsilonRemaining": 0.58}})
    got["POST /risk/assess"] = rk
    st_err, err, _ = http_json("POST", base + "/classify", {"records": []})
    if st_err != 400 or "error" not in err:
        rep.block(f"[A4] algo 参数错误应 400 + {{error}}，实际 {st_err} {err}")

    stats = {"checked": 0, "missing": 0, "mismatch": 0}
    for key, data in got.items():
        ex = ex3.get(key)
        if not ex or ex.get("response") is None:
            rep.warn(f"[A4] 契约第三部分无 {key} 示例")
            continue
        stats["checked"] += 1
        missing, extra, mism = diff_shape(ex["response"], shape(data))
        for m in missing:
            rep.block(f"[A4-missing] algo {key} 缺字段 {m}"); stats["missing"] += 1
        for m in mism:
            rep.block(f"[A4-type] algo {key} {m}"); stats["mismatch"] += 1
        if extra:
            rep.note(f"[A4-extra] algo {key} 多出字段：{', '.join(extra)}")
        check_enums("algo " + key, data, rep)
    rep.sections["A4"] = stats

    # ---- mock ⇄ algo 形态一致性（前端在 mock 与真后端之间切换不应出错）----
    if probe:
        me = probe["endpoints"]
        pairs = [
            ("FL rounds[]", me["GET /fl/tasks/{}"]["json"]["data"]["rounds"][0], job["rounds"][0], {"evidenceId", "at"}),
            ("DQN actions[]", me["POST /dispatch/tasks/{}/run"]["json"]["data"]["strategy"]["actions"][0], dqn["actions"][0], set()),
            ("DQN qTable[]", me["POST /dispatch/tasks/{}/run"]["json"]["data"]["qTable"][0], dqn["qTable"][0], set()),
            ("DQN constraintsChecked", me["POST /dispatch/tasks/{}/run"]["json"]["data"]["constraintsChecked"], dqn["constraintsChecked"], set()),
            ("AI analyze", {k: v for k, v in me["POST /ai/analyze"]["json"]["data"].items() if k in ("answer", "reasoning", "source", "latencyMs")}, ds, set()),
            ("classify", me["POST /assets/classify"]["json"]["data"], cl, {"algorithm"}),
            ("classify results[]", me["POST /assets/classify"]["json"]["data"]["results"][0], cl["results"][0], set()),
            ("risk", {k: v for k, v in me["POST /risk/assess"]["json"]["data"].items() if k not in ("evidenceId", "traceId")}, rk, set()),
            ("risk factors[]", me["POST /risk/assess"]["json"]["data"]["factors"][0], rk["factors"][0], set()),
        ]
        n = 0
        for name, mock_v, algo_v, allow_extra in pairs:
            # 以 algo 真实返回为形状基准（契约允许的扩展字段在 allow_extra 内）
            missing, extra, mism = diff_shape(shape(algo_v), shape(mock_v))
            if name == "DQN constraintsChecked":
                # violations 可能一侧为空数组，只比字段名
                missing = [m for m in missing if not m.startswith("violations[]")]
                extra = [m for m in extra if not m.startswith("violations[]")]
                mv = mock_v.get("violations") or []
                av = algo_v.get("violations") or []
                if mv and av:
                    m2, e2, t2 = diff_shape(shape(av[0]), shape(mv[0]), "violations[]")
                    missing += m2; mism += t2
            for m in missing:
                rep.block(f"[A4-mock≠algo] {name}: mock 缺 algo 字段 {m}"); n += 1
            for m in mism:
                rep.block(f"[A4-mock≠algo] {name}: {m}"); n += 1
            ex_extra = [e for e in extra if e.split("[]")[0].split(".")[0] not in allow_extra]
            if ex_extra:
                rep.note(f"[A4-mock+] {name}: mock 多出 {', '.join(ex_extra)}")
        # DQN violations 的字段名（mock 用 constraint 对齐 algo）
        rep.sections["A4-mock-vs-algo"] = {"mismatch": n}
        if dqn.get("constraintsChecked", {}).get("violations"):
            v0 = dqn["constraintsChecked"]["violations"][0]
            rep.note(f"[A4] algo violations[] 字段：{sorted(v0)}")
    return got


# ---------------------------------------------------------------- A3 views 字段粗扫
JS_BUILTIN = set("""
length push pop shift unshift slice splice map filter reduce find findIndex some every forEach includes indexOf join sort reverse concat flat flatMap keys values entries
toFixed toString toLowerCase toUpperCase trim split replace replaceAll startsWith endsWith padStart padEnd charAt at match test exec
then catch finally resolve reject all race now getTime toISOString toLocaleString toLocaleDateString toLocaleTimeString getHours getMinutes getFullYear getMonth getDate getDay
value key name id type label title style class disabled loading visible size width height color icon text data row column index prop
log error warn info debug assign freeze parse stringify random floor ceil round max min abs sqrt pow PI sign
target currentTarget preventDefault stopPropagation clipboard writeText createElement appendChild removeChild body document window location href
$el $refs $emit $router $route params query path meta fullPath default env VITE_USE_MOCK VITE_API_BASE MODE DEV PROD
add delete has get set clear Map Set Number String Boolean Object Array Date Math JSON Promise
""".split())


def contract_fields(ex2, ex3):
    fields = set()

    def walk(s):
        if isinstance(s, dict):
            for k, v in s.items():
                fields.add(k); walk(v)
        elif isinstance(s, list):
            for v in s:
                walk(v)
    for ex in (*ex2.values(), *ex3.values()):
        walk(ex.get("response")); walk(ex.get("request"))
    # 契约通用约定 / WS payload / 表格字段
    fields |= {"code", "message", "data", "traceId", "items", "total", "page", "size", "token", "permissions", "payload", "ts", "type",
               "nodeId", "status", "metrics", "taskId", "round", "totalRounds", "loss", "acc", "compressionRatio", "epsilonSpent", "stage", "detail",
               "alertId", "ruleCode", "riskLevel", "actorDid", "level", "module", "content", "evidenceId", "category", "blockHeight"}
    return fields


# mock 文档化的扩展字段（MSG-frontend-infra-to-all-001 §1）与页面本地约定，允许页面引用
MOCK_EXTRA_FIELDS = set("""
location didDocument assetsCount tampered blockHash tamperedIds qTable constraintsChecked reasoning riskLevel actorName detail resourceType resourceId
stepCount evidenceCount totals logs highRisk denied alerts byModule range from to at nodeContributions keyId versions rotations ruleName createdAt updatedAt
ownerDid description keyVersion controllerDid metadata orgName subjectName subjectType found payloadHash prevHash txId timestamp refId hash
nodeIds roundCount lastLoss startedAt finishedAt createdBy anomaly simulatePoison issueEvidenceId commandId signerDid targets ack ackedAt issuedAt ranAt objective timeWindow
applicantName reviewerDid reviewComment reviewedAt expireAt grantId granteeName grantedBy grantedAt revokedAt revokedBy applicationId scope builtin grants
socMin socMax maxPowerKw violations constraint attempted applied rule immediateReward anomalies weight localLoss
certificateId issuedAt issuer evidence integrity signature checkedAt intact localHash chainHash tamperedAt
totalLogs generatedAt periodNew chainIntact brokenAt lastHash height totalRecords byCategory
accepted sessionToken nodeId interval
ackedBy acked allAcked actualPowerKw responseDelaySec
publishedAt publishedBy version rounds draft
questions answer latencyMs source scene question context history
cluster factors sensitivity granularity volume score reason index clusterCenters results algorithm
riskScore suggestion desc features queryFreq dataGranularity exposedFields epsilonRemaining
loggedOut deleted realName username roles did user expiresIn
privateKey publicKey chainTxId authStatus dataType sourceDid
""".split())


def scan_views(fields: set[str], rep: Report):
    """粗扫：views/components 中 `.xxx` 属性访问，排除 JS 内建、本地声明、契约字段、mock 扩展字段"""
    suspects = {}
    allowed = fields | MOCK_EXTRA_FIELDS | JS_BUILTIN
    for f in sorted([*VIEWS_DIR.glob("*.vue"), *COMPONENTS_DIR.rglob("*.vue")]):
        src = f.read_text(encoding="utf8")
        # 本地声明：const/let/var/function/ref 名、对象字面量键、defineProps 键、模板 v-for 变量、import 名
        local = set(re.findall(r"\b(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)", src))
        local |= set(re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*:", src, re.M))        # 对象字面量键 / 样式
        local |= set(re.findall(r"[{,]\s*([A-Za-z_$][\w$]*)\s*:", src))             # 行内对象键
        local |= set(re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", src))                 # 函数调用名
        local |= set(re.findall(r"import\s*\{([^}]*)\}", src) and re.findall(r"[\w$]+", " ".join(re.findall(r"import\s*\{([^}]*)\}", src))) or [])
        local |= set(re.findall(r"\.([A-Za-z_$][\w$]*)\s*=[^=]", src))               # 被赋值的属性（页面自建状态）
        # 脚本与模板里的属性访问（排除 CSS）
        body = re.sub(r"<style[\s\S]*?</style>", "", src)
        body = re.sub(r"/\*[\s\S]*?\*/", "", body)          # 块注释
        body = re.sub(r"(^|\s)//[^\n]*", "", body)           # 行注释
        body = re.sub(r"<!--[\s\S]*?-->", "", body)          # 模板注释
        for m in re.finditer(r"(?<![\w$])([A-Za-z_$][\w$]*(?:\?\.|\.)(?:[A-Za-z_$][\w$]*(?:\?\.|\.))*)([A-Za-z_$][\w$]*)", body):
            chain, leaf = m.group(1), m.group(2)
            root = re.split(r"\?\.|\.", chain)[0]
            if leaf in allowed or leaf in local:
                continue
            if root in ("this", "props", "emit", "router", "route", "ElMessage", "ElMessageBox", "echarts", "console", "Math", "JSON", "Object", "Array", "Date", "Number", "String", "Promise", "navigator", "window", "document", "import", "wsClient", "WS_TYPES", "WS_STATUS", "store", "userStore", "logStore", "perspectiveStore", "dispatchStore", "form", "filters", "state", "ui", "dialog", "chart", "el"):
                continue
            if len(leaf) <= 1 or leaf.isupper() or leaf in ("vue", "js", "md", "json", "css", "dataIndex", "seriesName", "seriesIndex", "axisValue", "marker", "componentType"):
                continue
            suspects.setdefault(f.relative_to(FRONTEND / "src").as_posix(), set()).add(f"{chain}{leaf}")
    rep.sections["A3"] = {k: sorted(v) for k, v in suspects.items()}
    return suspects


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--algo-port", type=int, default=int(os.environ.get("ALGO_AUDIT_PORT", "8197")))
    ap.add_argument("--start-algo", action="store_true")
    ap.add_argument("--skip-mock", action="store_true")
    ap.add_argument("--skip-views", action="store_true")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    rep = Report()
    endpoints, ex2, ex3 = parse_contract()
    rep.sections["contract"] = {"endpoints": len(endpoints), "examples_part2": len([k for k, v in ex2.items() if v.get("response") is not None]), "examples_part3": len([k for k, v in ex3.items() if v.get("response") is not None])}
    for k, v in {**ex2, **ex3}.items():
        if v.get("response_error"):
            rep.warn(f"[parse] {k} 示例解析失败：{v['response_error']}")

    # A1
    api = scan_api_js()
    handlers = scan_handlers()
    hset = {f"{m} {n}" for m, _, n, _ in handlers}
    cset = {f"{e['method']} {e['norm']}" for e in endpoints}
    a1 = {"api_missing": sorted(cset - set(api)), "api_extra": sorted(set(api) - cset), "handler_missing": sorted(cset - hset), "handler_extra": sorted(hset - cset)}
    for k in a1["api_missing"]:
        rep.block(f"[A1-api] api/*.js 缺少契约接口 {k}")
    for k in a1["api_extra"]:
        rep.block(f"[A1-api] api/*.js 调用了契约外路径 {k}（{api[k]}）")
    for k in a1["handler_missing"]:
        rep.block(f"[A1-mock] mocks/handlers 缺少 {k}")
    for k in a1["handler_extra"]:
        rep.warn(f"[A1-mock] mocks/handlers 多出契约外 handler {k}")
    check_handler_order(handlers, rep)
    rep.sections["A1"] = {**a1, "api_calls": len(api), "handlers": len(handlers)}

    # A2 + B1
    probe = None
    if not args.skip_mock:
        probe = run_mock_probe(rep)
        if probe:
            audit_mock(endpoints, ex2, probe, rep)
            audit_rbac(probe, rep)

    # A4
    proc = None
    try:
        if args.start_algo:
            proc = start_algo(args.algo_port)
        audit_algo(ex3, probe, args.algo_port, rep)
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # A3
    if not args.skip_views:
        scan_views(contract_fields(ex2, ex3), rep)

    # ---- 输出 ----
    print("=" * 72)
    print("契约一致性审计 · 摘要")
    print("=" * 72)
    for k, v in rep.sections.items():
        if k == "A3":
            print(f"A3 views 可疑字段引用（粗扫，需人工判断）：{sum(len(x) for x in v.values())} 处 / {len(v)} 文件")
            for f, items in v.items():
                print(f"   {f}: {', '.join(items[:12])}{' …' if len(items) > 12 else ''}")
        else:
            print(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    print("-" * 72)
    print(f"阻断级不一致：{len(rep.blocking)}")
    for b in rep.blocking:
        print("  ✗ " + b)
    print(f"警告：{len(rep.warnings)}")
    for w in rep.warnings:
        print("  ! " + w)
    print(f"提示（允许的扩展字段等）：{len(rep.info)}")
    for i in rep.info:
        print("  · " + i)
    if args.json:
        args.json.write_text(json.dumps({"blocking": rep.blocking, "warnings": rep.warnings, "info": rep.info, "sections": rep.sections}, ensure_ascii=False, indent=2), encoding="utf8")
    sys.exit(1 if rep.blocking else 0)


if __name__ == "__main__":
    main()
