# qa/api-tests · 接口 / 数据库 / 安全测试脚本（test-api）

对合并后的真实系统（backend 8000 + algo-service 8100 + MariaDB + Redis）做接口级、数据库级与安全测试。
**不改任何业务代码**；密码学校验只读复用 `backend/core/gm_crypto.py`（SM2/SM3）。
所有测试实体带 `test-` 前缀，不重置数据库，不杀任何进程。

## 运行

```bash
cd <项目根>
# 1. 主脚本：2.1~2.12 全部 HTTP 接口 + 安全专项 + 数据库核对（约 3 分钟，含 FL 跑 3 轮）
backend/.venv/bin/python qa/api-tests/run_all.py            # 全量
backend/.venv/bin/python qa/api-tests/run_all.py auth did   # 只跑指定节（复用上次 results/ctx.json 里的实体）
# 2. WebSocket：鉴权、六类消息、FL/调度触发推送（约 1 分钟）
backend/.venv/bin/python qa/api-tests/ws_test.py
# 3. 性能抽样：登录 / 列表 / 存证校验 ×50 的 P95，20 并发登录
backend/.venv/bin/python qa/api-tests/perf.py
# 4. 降级链路：在 5301 起 algo 副本（DeepSeek 不可达）、在 5300 起 backend 副本（算法服务不可达），跑完自动结束
SCRATCH=/tmp backend/.venv/bin/python qa/api-tests/degrade_test.py
```

环境变量：`API_BASE`（默认 `http://127.0.0.1:8000/api/v1`）、`WS_URL`、`ALGO_BASE`。DB 连接用 `energy/energy123@127.0.0.1:3306/energy_tds`。

## 输出

`results/*.json`：每条用例 `{id, module, basis, step, expect, actual, verdict, evidence}`；
`results/ctx.json`：本次创建的实体 id（DID、资产、任务等）。
`gen_doc_tables.py` 把 results 汇成 Markdown 表，供 `docs/测试文档-接口与安全.md` 引用。

## 注意

- 暴力登录用例只对新建的 `test-brute-*` 账号连错 6 次，不会锁演示账号。
- `sec_evidence` 开头会检查链完整性，若其他 Agent 的 tamper 演示没有还原，会调用 `/evidence/demo/restore` 还原后再测（只调演示接口，不改库）。
- `API-SEC-20` 会直接 SQL 改一条 **本脚本自己写入** 的存证快照并立即改回，用于验证"绕过应用层改库会被抓"。
- `API-NODE-07` 会对 Node-D 绑定的 edge DID 做一次密钥轮换（`custody=true`，托管不变）以取得私钥做真实 SM2 签名上线。
