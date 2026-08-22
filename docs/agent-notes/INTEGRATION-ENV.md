# 联调环境说明（2026-08-22，甲方 backend 已合并）

所有子 Agent 先读本文件。**铁律：`backend/` 是甲方目录，任何文件不得修改（.venv 除外）；`contract/` 只读。**
发现后端与契约/需求不符的问题，写进 `docs/agent-notes/BACKEND-ISSUES.md`（追加，带编号、复现步骤、期望/实际、契约章节），不要改后端。

## 正在运行的服务（全部 127.0.0.1）
| 服务 | 地址 | 说明 |
|---|---|---|
| MariaDB 11.4（替代 MySQL 8，用户态运行） | 3306，库 energy_tds，用户 energy/energy123，root 无密码走 socket | 已导入 backend/sql/01_schema.sql + 02_seed.sql（26 表、6 用户、80 资产、154 存证、2880 指标） |
| Redis（机器上已有，非本项目） | 6379，backend 用 REDIS_DB=9 | |
| backend（甲方 FastAPI） | http://127.0.0.1:8000/api/v1 ，WS ws://127.0.0.1:8000/ws ，Swagger /docs | 启动：`cd backend && .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000`，日志 scratchpad/backend.log，配置来自 backend/.env |
| algo-service（乙） | http://127.0.0.1:8100/algo/v1 | `cd algo-service && set -a && . ../.env && set +a && ../.venv/bin/uvicorn main:app --port 8100` |
| frontend（Vite，**真后端模式** VITE_USE_MOCK=false） | http://127.0.0.1:5199 ，/api 与 /ws 代理到 8000 | `cd frontend && VITE_USE_MOCK=false npx vite --host 127.0.0.1 --port 5199 --strictPort` |

MariaDB 客户端：`/tmp/claude-1002/-home-stu-yanxulong-Tzb/65f0c8ee-142f-469a-945b-159bbb6cf897/scratchpad/mysql/mdb/bin/mariadb -S /tmp/claude-1002/-home-stu-yanxulong-Tzb/65f0c8ee-142f-469a-945b-159bbb6cf897/scratchpad/mysql/mysql.sock -uroot energy_tds`
重置数据库：DROP DATABASE 后重导两个 sql（约 1 秒）。**不要随意重置，其他 Agent 也在用；必须重置时先在 STATUS 里声明。**

演示账号：admin/admin123 grid/grid123 vpp/vpp123 subject/subject123 regulator/reg123 edge/edge123
Playwright：在 frontend/ 下 `node xxx.mjs`，`import { chromium } from '@playwright/test'`（Chrome headless shell 已装）。
不要杀别人的进程；不要占用 5199/8000/8100 之外的端口启动第二套服务（需要时用 5300+ 端口并在 STATUS 里声明）。
需求文档：docs/legacy/能源可信数据空间项目需求书.docx.txt、docs/legacy/虚拟电厂云边端隐私智能调度平台后端开发.docx.txt；契约：../Zhi_softwire/contract/API-CONTRACT.md。
通信：docs/agent-notes/STATUS-<agent>.md 记录进度；给别人提需求写 MSG-<from>-to-<to>-NNN.md，被点名者处理后在文件内追加 `## 回复`。
