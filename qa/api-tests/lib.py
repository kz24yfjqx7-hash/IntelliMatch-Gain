"""公共库：HTTP 客户端、用例记录器、国密工具（复用甲方 backend/core/gm_crypto，只读导入）、DB 访问。"""
import json
import os
import sys
import time
import pymysql

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND_DIR = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND_DIR)
from core import gm_crypto as gm  # noqa: E402  纯算法库，只读复用

import httpx  # noqa: E402

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
ALGO = os.environ.get("ALGO_BASE", "http://127.0.0.1:8100/algo/v1")
WS_URL = os.environ.get("WS_URL", "ws://127.0.0.1:8000/ws")
DB = dict(host="127.0.0.1", port=3306, user="energy", password="energy123", database="energy_tds",
          charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
ACCOUNTS = {"admin": "admin123", "grid": "grid123", "vpp": "vpp123", "subject": "subject123",
            "regulator": "reg123", "edge": "edge123"}
ENVELOPE = {"code", "message", "data", "traceId"}

client = httpx.Client(base_url=BASE, timeout=60)
_tokens: dict[str, str] = {}


def token(user: str) -> str:
    if user not in _tokens:
        r = client.post("/auth/login", json={"username": user, "password": ACCOUNTS[user]})
        _tokens[user] = r.json()["data"]["token"]
    return _tokens[user]


def hdr(user: str | None = None, trace: str | None = None, raw_token: str | None = None) -> dict:
    h = {}
    if raw_token:
        h["Authorization"] = f"Bearer {raw_token}"
    elif user:
        h["Authorization"] = f"Bearer {token(user)}"
    if trace:
        h["X-Trace-Id"] = trace
    return h


def req(method: str, path: str, user: str | None = "admin", json_body=None, params=None, **kw):
    r = client.request(method, path, headers=hdr(user, kw.pop("trace", None), kw.pop("raw_token", None)),
                       json=json_body, params=params, **kw)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return r, body


def snippet(body, limit=220) -> str:
    s = json.dumps(body, ensure_ascii=False, default=str) if not isinstance(body, str) else body
    return s if len(s) <= limit else s[:limit] + "…"


def db_query(sql: str, args=None):
    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


class Recorder:
    """用例记录器：每条用例 编号/依据/步骤/预期/实际/判定/证据。"""

    def __init__(self, out_path: str):
        self.out_path = out_path
        self.cases: list[dict] = []
        self.module = ""
        self.seq = {}

    def case(self, cid: str, basis: str, step: str, expect: str, checks, actual_body=None,
             status_code=None, blocked: str | None = None, module: str | None = None):
        """checks: bool 或 (bool, 说明) 列表；blocked 非空则判定为阻塞并写原因。"""
        if blocked:
            verdict, detail = "阻塞", blocked
        else:
            if not isinstance(checks, (list, tuple)):
                checks = [checks]
            fails = []
            for c in checks:
                ok, why = (c if isinstance(c, tuple) else (c, ""))
                if not ok:
                    fails.append(why or "断言失败")
            verdict = "通过" if not fails else "失败"
            detail = "；".join(fails)
        ev = ""
        if actual_body is not None:
            ev = (f"HTTP {status_code} " if status_code else "") + snippet(actual_body)
        rec = dict(id=cid, module=module or cid.split("-")[1], basis=basis, step=step, expect=expect,
                   actual=detail or ("与预期一致" if verdict == "通过" else ""), verdict=verdict, evidence=ev)
        self.cases.append(rec)
        mark = {"通过": "PASS", "失败": "FAIL", "阻塞": "BLOCK"}[verdict]
        print(f"[{mark}] {cid} {step[:60]} {('-> ' + detail) if detail else ''}", flush=True)
        return verdict == "通过"

    def save(self):
        with open(self.out_path, "w", encoding="utf-8") as f:
            json.dump(self.cases, f, ensure_ascii=False, indent=1, default=str)
        total = len(self.cases)
        p = sum(c["verdict"] == "通过" for c in self.cases)
        fl = sum(c["verdict"] == "失败" for c in self.cases)
        b = total - p - fl
        print(f"\n共 {total} 条：通过 {p} 失败 {fl} 阻塞 {b}  -> {self.out_path}")


def envelope_ok(body) -> tuple[bool, str]:
    if not isinstance(body, dict):
        return False, "响应非 JSON 对象"
    if set(body.keys()) != ENVELOPE:
        return False, f"包裹字段为 {sorted(body.keys())}"
    if not str(body.get("traceId", "")).startswith("tr-"):
        return False, f"traceId 格式 {body.get('traceId')}"
    return True, ""


def expect_code(r, body, http: int, code: int) -> list:
    return [envelope_ok(body), (r.status_code == http, f"HTTP {r.status_code}≠{http}"),
            (isinstance(body, dict) and body.get("code") == code, f"code {body.get('code') if isinstance(body, dict) else body}≠{code}")]


def ok(r, body) -> list:
    return expect_code(r, body, 200, 0)


def has_keys(d, keys) -> tuple[bool, str]:
    if not isinstance(d, dict):
        return False, "data 非对象"
    missing = [k for k in keys if k not in d]
    return (not missing, f"缺字段 {missing}" if missing else "")


def page_ok(d) -> tuple[bool, str]:
    return has_keys(d, ["items", "total", "page", "size"])


def iso_ok(s) -> tuple[bool, str]:
    import re
    good = isinstance(s, str) and re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$", s) is not None
    return good, f"时间格式 {s!r} 非 ISO8601 带时区"


def uniq(prefix="test"):
    return f"{prefix}-{int(time.time() * 1000) % 100000000:08d}"
