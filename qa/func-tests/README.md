# 功能测试脚本（test-func）

依据《能源可信数据空间项目需求书》编写，结果汇总见 `docs/测试文档.md`。

| 脚本 | 层面 | 运行 | 输出 |
|---|---|---|---|
| `api_func_test.py` | 接口（需求书 3.1–3.12 + 非功能） | `cd energy-tds && .venv/bin/python qa/func-tests/api_func_test.py` | `results/api-results.json`（每用例判定 + 证据片段） |
| `ui_func_test.mjs` | 页面（需求书第四章 + 第六章完整流程，Playwright 真实 Chromium） | `cd energy-tds/frontend && node ../qa/func-tests/ui_func_test.mjs` | `results/ui-results.json`；截图 `docs/test-evidence/*.jpg`（1366×768） |

前置：按 `docs/agent-notes/INTEGRATION-ENV.md` 启动 MariaDB / backend(8000) / algo-service(8100) / 真后端模式前端(5199)。
可用环境变量覆盖地址：`API_BASE`、`ALGO_BASE`、`UI_BASE`。

约束：脚本只创建带 `test-` 前缀的新实体，不重置数据库、不修改业务代码；
存证篡改演示在用例末尾调用 `/evidence/demo/restore` 还原。SM2 签名复用 `backend/core/gm_crypto.py`（只读 import）。
接口层全量约 8 分钟（含多次联邦训练等待），页面层约 6 分钟。
