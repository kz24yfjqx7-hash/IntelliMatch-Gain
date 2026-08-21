# STATUS-qa（2026-08-21）

## 已完成
- `algo-service/tests/`：conftest + 5 个套件 70 用例（契约结构 / FedAvg / DQN / 分类分级+风险 / DeepSeek 降级）。**当前 70/70 通过**。
- `frontend/tests/`：setup.js + 5 个 spec 56 用例（request 拦截器 / MSW 契约 / stores / v-permission 指令 / 外网 URL 扫描）。第二波新增 `pages.spec.js`（11 页 + Banner 挂载、vpp 裁剪、router/删文件/Pyodide 检查）。**全部 14 文件 83/83 通过**（含 frontend-pages/legacy 的冒烟）。
- `qa/check_algo_live.sh`（uvicorn + curl 10 项，**全过**）、`qa/check_frontend_build.sh`、`qa/check_deploy.sh`（46 项，45 过）、`qa/acceptance-checklist.md`、`qa/REPORT.md`。

## 待修复（缺陷单）
| 单号 | 致 | 级别 | 摘要 | 状态 |
|---|---|---|---|---|
| MSG-qa-to-algo-001 | algo | P1/P1/P2 | FL 曲线不收敛；投毒误报 Node-B；classify 空入参 200 | **已修复，qa 已验证关闭** |
| MSG-qa-to-frontend-infra-001 | frontend-infra | P0 | `stores/logs.js:3` 注释含 `*/` → `npm run build` 失败 | **已修复，qa 已验证关闭**（build 成功，vitest 55/56） |
| MSG-qa-to-deploy-001 | deploy | P2 | `backend.env_file: .env` 硬依赖 | **已修复，qa 已验证关闭**（附注 FL_ROUND_DELAY 可选变量 P2 记录） |
| MSG-qa-to-frontend-legacy-001 | frontend-legacy | P1 | Pyodide CDN URL；假实现文件未删 | **已修复，qa 已验证关闭** |

## 等待中
- 无。全部缺陷单已关闭。最终报告见 `qa/REPORT.md`。

## 阻塞
- 本机无 docker 权限：`docker build` / `compose up` / 真机安装验收无法执行。
