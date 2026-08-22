"""把 results/*.json 汇成 Markdown 用例表与汇总表（供 docs/测试文档-接口与安全.md）。运行：python3 qa/api-tests/gen_doc_tables.py > /tmp/tables.md"""
import glob
import json
import os
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_NAME = OrderedDict([("AUTH", "2.1 认证"), ("USER", "2.1 用户管理"), ("DID", "2.2 DID 身份"), ("KEY", "2.3 密钥管理"), ("ASSET", "2.4 数据资产"),
                        ("PERM", "2.5 权限中心"), ("EV", "2.6 可信存证"), ("AUD", "2.7 安全审计"), ("NODE", "2.8 节点"), ("FL", "2.9 联邦学习"),
                        ("DP", "2.10 智能调度"), ("AI", "2.11 AI 分析"), ("RISK", "2.12 风险评估"), ("WS", "2.13 WebSocket"),
                        ("DEG", "算法链路降级 / 2001"), ("SEC", "安全专项"), ("DB", "数据库专项"), ("PERF", "性能抽样")])
cases = []
by_id = OrderedDict()
# run_<节>.json 是对单节的重跑，按文件修改时间晚者覆盖同编号用例
files = ["run_all.json", "ws.json", "perf.json", "degrade.json"] + sorted(
    [os.path.basename(x) for x in glob.glob(os.path.join(HERE, "results", "run_*_*.json")) + glob.glob(os.path.join(HERE, "results", "run_audit.json"))],
    key=lambda n: os.path.getmtime(os.path.join(HERE, "results", n)))
for f in files:
    p = os.path.join(HERE, "results", f)
    if os.path.exists(p):
        for c in json.load(open(p, encoding="utf-8")):
            by_id[c["id"]] = c
cases = list(by_id.values())
cases = [c for c in cases if not c["id"].endswith("-ERR")]


def esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


print("<!-- 由 qa/api-tests/gen_doc_tables.py 生成 -->")
for mod, name in MOD_NAME.items():
    cs = [c for c in cases if c["module"] == mod]
    if not cs:
        continue
    print(f"\n### {name}（{len(cs)} 条）\n")
    print("| 编号 | 依据 | 步骤 / 请求 | 预期 | 实际 | 判定 | 证据（响应片段） |")
    print("|---|---|---|---|---|---|---|")
    for c in cs:
        print(f"| {c['id']} | {esc(c['basis'])} | {esc(c['step'])} | {esc(c['expect'])} | {esc(c['actual'])} | **{c['verdict']}** | `{esc(c['evidence'][:160])}` |")
print("\n### 汇总\n")
print("| 模块 | 用例数 | 通过 | 失败 | 阻塞 |")
print("|---|---|---|---|---|")
tot = [0, 0, 0, 0]
for mod, name in MOD_NAME.items():
    cs = [c for c in cases if c["module"] == mod]
    if not cs:
        continue
    p = sum(c["verdict"] == "通过" for c in cs); f = sum(c["verdict"] == "失败" for c in cs); b = len(cs) - p - f
    tot = [tot[0] + len(cs), tot[1] + p, tot[2] + f, tot[3] + b]
    print(f"| {name} | {len(cs)} | {p} | {f} | {b} |")
print(f"| **合计** | **{tot[0]}** | **{tot[1]}** | **{tot[2]}** | **{tot[3]}** |")
