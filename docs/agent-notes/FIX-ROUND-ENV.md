# 缺陷修复轮 · 环境与分工（2026-08-22）

**用户已明确取消甲乙分工限制：`backend/` 现在可以改。** `contract/` 仍然冻结只读，接口路径、字段名、枚举值一律不得变更——修复只能让实现向契约靠拢，不能反过来改契约。

## 重要的工具限制

- **写 `backend/` 下的文件必须用 Read / Edit / Write 工具**，用 `sed`、heredoc、`>` 重定向会被安全策略拦截。
- **数据库 DDL（`ALTER TABLE` / `DROP DATABASE` / 建表）会被拦截**。需要改表结构时：
  1. 改 `backend/sql/01_schema.sql`（全新部署走这里，自动生效）；
  2. 把等价的 `ALTER` / `CREATE TABLE IF NOT EXISTS` 追加到 `backend/sql/03_migrate_20260822.sql`（存量库升级用）；
  3. 在自己的 STATUS 里写明"需要执行迁移脚本"，由主 Agent 汇总请用户执行一次。
  - 已知：`algo_fl_round.loss` 改 DOUBLE、`chain_evidence_backup` 建表尚未在联调库生效，代码要能兼容"迁移未执行"的情况（捕获异常并降级，不要 500）。

## 服务（全部 127.0.0.1）

| 服务 | 端口 | 说明 |
|---|---|---|
| MariaDB 11.4 | 3306 | 库 energy_tds，用户 energy/energy123，root 走 socket。客户端：`/tmp/claude-1002/-home-stu-yanxulong-Tzb/65f0c8ee-142f-469a-945b-159bbb6cf897/scratchpad/mysql/mdb/bin/mariadb -S /tmp/claude-1002/-home-stu-yanxulong-Tzb/65f0c8ee-142f-469a-945b-159bbb6cf897/scratchpad/mysql/mysql.sock -uroot energy_tds`（**只读查询可以，DDL 会被拦**） |
| backend（共享实例） | 8000 | `cd backend && .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000`。**不要重启这个共享实例**——多个 Agent 在用 |
| algo-service | 8100 | 乙方算法服务 |
| frontend | 5199 | 真后端模式（VITE_USE_MOCK=false），Vite 热更新 |

**要联调验证自己的改动**：用 `cd backend && .venv/bin/python -m uvicorn main:app --port 80NN` 起**自己的实例**（Agent1→8011，Agent2→8012，Agent3→8013，Agent4→8014），验证完杀掉自己的 pid。别动 8000/8100/5199。

**后端单测**：`cd backend && .venv/bin/python -m pytest -q`（186 条，SQLite 内存库，不碰 MariaDB）。**每个 Agent 收工前必须全绿**（唯一允许失败的是 `test_audit.py::test_审计报告在算法服务不可用时降级`——它假设算法服务不可达，而本机 8100 在跑）。

## 文件归属（严格不重叠）

| Agent | 负责缺陷 | 独占文件 |
|---|---|---|
| fix-identity | B-020、B-013、B-023 | `backend/modules/did/**`、`backend/modules/key/**`、`backend/core/gm_crypto.py`、**所有 `modules/*/model.py` 的时间列**（其他 Agent 不要碰 model.py 的 created_at/updated_at） |
| fix-evidence | 存证 scope=own、require_roles 审计埋点、B-001/B-025、B-017、chain/status 性能、B-010 | `backend/modules/evidence/**`、`backend/modules/audit/**`、`backend/core/deps.py` |
| fix-algo | B-014、B-012、B-015、ack 目标校验、B-026、托管签名落库、expireAt 校验 | `backend/modules/algo/**`、`backend/modules/permission/**`、`backend/core/middleware.py` |
| fix-ws | B-024、B-021、B-022、nginx 安全头、前端收尾 | `backend/ws/**`、`backend/modules/node/**`、`backend/core/security.py`、`deploy/nginx.conf`、`frontend/nginx.conf`、`frontend/e2e/**`、`frontend/src/api/ws.js` |

需要动别人文件时：写 `docs/agent-notes/MSG-<你>-to-<对方>-NNN.md`，别直接改。

## 契约与测试依据

- 契约：`../Zhi_softwire/contract/API-CONTRACT.md`、`DB-SCHEMA.md`
- 缺陷详情（复现步骤、期望 vs 实际、文件行号）：`docs/agent-notes/BACKEND-ISSUES.md`
- 三份测试文档：`docs/测试文档.md`、`docs/测试文档-接口与安全.md`、`qa/` 下的可复跑脚本
  （`qa/check_backend_live.sh` 123 条断言、`qa/api-tests/run_all.py`、`qa/func-tests/`）

## 收工要求

1. 后端 pytest 全绿；**为每个修好的缺陷补一条后端单测**（放 `backend/tests/`，命名能看出对应缺陷编号）。
2. 在 `docs/agent-notes/BACKEND-ISSUES.md` 对应条目末尾追加一行 `**✅ 已修复（<你的名字>，2026-08-22）**：<改了什么，文件:行>`（追加，不要重写整个文件，避免和别人冲突）。
3. 写 `docs/agent-notes/STATUS-<你的名字>.md`：修了什么、怎么验证的、是否需要执行迁移脚本、有无遗留。
4. git 提交（`git -c user.name=stu -c user.email=disintroduct@gmail.com commit`），只 add 自己的文件。
5. 最终报告用中文、精简。
