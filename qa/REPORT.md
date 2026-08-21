# 验收报告（qa）— 最终版（第二波回归）

更新时间：2026-08-21

## 1. 各套件最终结果

| 套件 | 命令 | 结果 |
|---|---|---|
| algo-service pytest（5 文件 70 用例） | `cd algo-service && ../.venv/bin/python -m pytest tests -q` | **70/70 通过** |
| algo 真实服务检查 | `bash qa/check_algo_live.sh`（uvicorn + curl 10 项） | **10/10 PASS** |
| frontend vitest（14 文件 83 用例：qa 7 个 spec + frontend-pages `__smoke__` + frontend-legacy `__smoke_legacy__` 7 个） | `cd frontend && npx vitest run` | **83/83 通过** |
| 前端构建 + 外网扫描 | `bash qa/check_frontend_build.sh` | **ALL PASS**（build 55 产物；src/dist 无 http(s)、cdn、jsdelivr、pyodide） |
| 部署静态检查（46 项） | `bash qa/check_deploy.sh` | **ALL PASS**（WARN：shellcheck 未装；`FL_ROUND_DELAY` 为带默认值的契约外可选变量） |

### 第二波专项核查
- 11 个页面（NetworkTopology / CloudAggregate / DataClassification / RiskAssessment / PrivacyCompute / TerminalResponse / AuditLog / IdentityCenter / AssetsCenter / PermissionCenter / EvidenceCenter）+ `TrustFlowBanner` 在 MSW 下以 admin 登录挂载：**无抛错、均有内容**（`frontend/tests/pages.spec.js`）。
- vpp_operator 登录后 CloudAggregate 的「🔏 签名下发」按钮被 `v-permission` 移除，admin 可见：**通过**。
- `v-permission` 实际使用：CloudAggregate（`dispatch:issue`、`algo:execute`）、PermissionCenter（`user:manage` ×5）、AssetsCenter（`asset:write`）、IdentityCenter（`user:manage`）、DataClassification / PrivacyCompute（`.disable`）。EvidenceCenter 的「篡改演示」按钮用 `userStore.hasRole('sys_admin')` 守卫（契约：仅 sys_admin），与指令等效，可接受。
- router 四个中心路由指向 `IdentityCenter/AssetsCenter/PermissionCenter/EvidenceCenter.vue`，无 `_Placeholder` 引用（文件 `_Placeholder.vue` 仍在目录中但未被引用，P2 建议删除）。
- `aiReportGenerator.js`、`deepseekAdapter.js` 已删除，`src/**` 无 import 引用。
- Pyodide / cdn / jsdelivr：src 0 处 URL（仅 `scientificCompute.js` 头注释说明删除原因），dist 0 处。
- frontend-legacy 对 `stores/dispatch.js` 一行 import 的越界修改已备案并通知 frontend-infra，测试全过，无异议。

## 2. 缺陷单汇总

| 单号 | 致 | 级别 | 摘要 | 状态 |
|---|---|---|---|---|
| MSG-qa-to-algo-001 | algo | P1 ×2 + P2 | 10 轮 loss 不降；无投毒误报 `gradient_poisoning`；classify 空 records 200 | 已修复 · 已验证 · 关闭 |
| MSG-qa-to-frontend-infra-001 | frontend-infra | P0 (+P2) | `stores/logs.js` 注释 `*/` 提前闭合 → build 失败；`selfcheck.mjs` localhost URL | 已修复 · 已验证 · 关闭 |
| MSG-qa-to-deploy-001 | deploy | P2 | `backend.env_file: .env` 硬依赖 | 已修复 · 已验证 · 关闭 |
| MSG-qa-to-frontend-legacy-001 | frontend-legacy | P1 | Pyodide CDN URL；假实现文件未删 | 已修复 · 已验证 · 关闭 |

第二波回归**未发现新的 P0/P1**。P2 记录（不另开单）：`docker-compose.yml` 的 `FL_ROUND_DELAY` 契约外可选变量；`views/_Placeholder.vue` 无引用可删。

## 3. 对照 DoD / 契约第六部分
详见 `qa/acceptance-checklist.md`。
- algo：四项 DoD 全部达成。
- frontend：`npm run build` 零错误；`npm test` 83/83；`grep http(s)://` 仅剩 `w3id.org/did/v1`；12 条链路在 MSW 桩上全部自动化通过（登录 / DID / 资产上链 / 申请审批 / vpp 越权 1003 + high 日志 + R01 + WS 告警 / FL 真算曲线 / DQN + 解释 + 签名下发 / tamper→verify→brokenAt / traceId 回查 / 日报 narrative / 断网降级）。
- deploy：`docker compose config` 通过、`bash -n` 全过、install.sh 九步要点齐全。

## 4. 未覆盖项（需总控 / 甲方 / 硬件）
1. **真实 backend**：本仓库无 `backend/`，契约第二部分仅对 MSW 桩验证；合并后需用真实后端重跑 `mocks.contract.spec.js` 同等链路（可把 BASE 指向真实地址改造为集成脚本）。
2. **docker 权限**：`docker build`（algo-service / frontend 两镜像）、`docker compose up`、`packaging/build.sh` 实打未执行。
3. **真机**：干净 x86 断网安装、树莓派 4B（kiosk 自启、内存/IO）、`reboot` 自启、`uninstall.sh` 残留检查。
4. 浏览器人工走查（视觉/交互）；WebSocket 真实连接的心跳与退避重连；shellcheck。
