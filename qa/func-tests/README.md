# 功能测试脚本（test-func）

依据《能源可信数据空间项目需求书》编写，结果汇总见 `docs/测试文档.md`（本目录 `gen_doc.py` 生成）。

| 脚本 | 层面 | 运行 | 输出 |
|---|---|---|---|
| `api_func_test.py` | 接口（需求书 3.1–3.12 + 非功能，114 例） | `cd energy-tds && .venv/bin/python qa/func-tests/api_func_test.py` | `results/api-results.json`（每用例判定 + 证据片段）、`results/api-run.log` |
| `ui_func_test.mjs` | 页面（需求书第四章 + 第六章完整流程，Playwright 真实 Chromium，16 例） | `cd energy-tds/frontend && node ../qa/func-tests/ui_func_test.mjs` | `results/ui-results.json`；截图 `docs/test-evidence/*.jpg`（1366×768，≤300KB/张） |
| `gen_doc.py` | 汇总 | `cd energy-tds && .venv/bin/python qa/func-tests/gen_doc.py` | `docs/测试文档.md`（需求追溯矩阵 + 用例明细 + 缺陷清单 + 总结） |

前置：按 `docs/agent-notes/INTEGRATION-ENV.md` 启动 MariaDB / backend(8000) / algo-service(8100) / 真后端模式前端(5199)。
可用环境变量覆盖地址：`API_BASE`、`ALGO_BASE`、`UI_BASE`。

## 约定与注意事项

- 只创建带 `test-<时间戳>` 前缀的新实体，**不重置数据库、不修改任何业务代码**（`backend/`、`frontend/` 只读）。
- 存证篡改演示在用例末尾还原：接口层调 `/evidence/demo/restore`；页面层没有还原入口，脚本借页面里的 token 直接调该接口善后。
- SM2 签名复用 `backend/core/gm_crypto.py`（只读 import，纯算法）。
- 联调库里 4 个节点都已绑定种子 DID，新注册 DID 冒充上线会被 1004 拒绝。「设备用密钥完成接口认证」的正向路径（TC-33-05）与 WebSocket `node_status` 触发（TC-311-02）改用**后端托管私钥**：只读查询 `did_key.private_key_enc` 后用 `gm_crypto.sm4_cbc_decrypt` + `backend/.env` 的 `KEY_CUSTODY_SECRET` 解密，不写库、不改后端。
- 页面用例之间互不依赖：每条用例开头用 `ensureRole(角色)` 把登录态拉到位，上一条中途失败不会连累后面。
- **跑页面层时不要同时改 `frontend/src`**：Vite HMR 会让运行中的会话拿到半成品模块（本轮就误报过一次，见 MSG-test-func-to-integration-002 缺陷 2）。
- 接口层与页面层不要并行跑：两者都会做篡改演示与权限申请，会互相干扰。
- 截图总数上限 40 张；`ui_func_test.mjs` 启动时会清掉上一轮的 `TC-UI-*-fail.jpg`。

接口层约 8 分钟（含多次联邦训练等待），页面层约 7 分钟。
