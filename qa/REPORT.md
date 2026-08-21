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

---

# 第三轮：对抗测试（4 个测试 Agent 并行）回归验收

更新时间：2026-08-21。四个 Agent（test-algo / test-e2e / test-contract-security / test-deploy）各自对抗测试并**直接修复**了代码，qa 做综合回归确认互不冲突。

## 1. 四个 Agent 的发现 / 修复
| Agent | 范围 | 发现 | 修复 | 新增回归资产 |
|---|---|---|---|---|
| test-algo | algo-service | 12 类（16 个 500 → 0；长度/数量上限；任务字典无界；并发训练无上限→429；缓存损坏不自愈；缓存无上限/非原子写 等） | 12/12 已修（契约字段不变） | `tests/test_adversarial_inputs.py`(78) + `test_adversarial_runtime.py`(166) |
| test-e2e | views/ | 3 个 UI 问题（/evidence 1366 宽横向溢出；终端响应页目标节点不匹配卡演示 等） | 3/3 已修 | Playwright `frontend/e2e/*.spec.js` 28 例（无 console.error/4xx/内存膨胀断言） |
| test-contract-security | 契约逐字段 + RBAC/安全 | 12 项审计项（RBAC 仅自有过滤、登出 token 失效、导出权限、violations 字段统一、XSS 复核等）；依赖 `echarts<6.1` moderate 记录未升级 | 阻断级 0 | `qa/contract_audit.py`、`frontend/tests/contract-audit.spec.js`(54) |
| test-deploy | packaging/deploy | 沙箱 39 场景（build 十步 / install 九步含失败分支 / uninstall / nginx -t / systemd-analyze / shellcheck 0 告警） | 全部 PASS | `qa/deploy-sandbox/run.sh`(39)、`check_compose.py` |

## 2. 综合回归结果（qa 重跑）
| 套件 | 命令 | 结果 |
|---|---|---|
| algo pytest | `cd algo-service && ../.venv/bin/pytest -q tests` | **314/314 通过**（16s） |
| algo 真实服务 | `bash qa/check_algo_live.sh` | **10/10 PASS** |
| frontend vitest | `cd frontend && npx vitest run` | **137/137 通过**（15 文件） |
| 前端构建 + 扫描 | `bash qa/check_frontend_build.sh` / `npm run build` | **ALL PASS** |
| mock 自检（12 条链路） | `node src/mocks/selfcheck.mjs` | **12/12 PASS** |
| 契约/安全审计 | `.venv/bin/python qa/contract_audit.py` | **退出码 0，阻断级 0**（仅「多出字段，向后兼容」提示） |
| Playwright E2E | `VITE_USE_MOCK=true vite --port 5197` + `npx playwright test` | **28/28 通过**（3.2 min，dev server 已杀） |
| 部署沙箱 | `bash qa/deploy-sandbox/run.sh` | **39/39 PASS** |
| 部署静态检查 | `bash qa/check_deploy.sh` | **ALL PASS** |

**冲突判定：无。** 四方修改分别落在 `algo-service/{main,config,adapters,algorithms}`、`views/`、`mocks/ + tests/`、`packaging/ + deploy/`，互不重叠；全部套件（含各方新增用例）在同一工作树上一次性全绿。

## 3. MSG-test-contract-security-to-test-e2e-001（P2）处理
qa 已核对 `EvidenceCenter.vue:112-114`、`IdentityCenter.vue:320-331` 对契约外字段的兜底（`|| []`、三元、`catch → null`），真后端仅返回契约字段时不抛错；保留现有写法，已在该 MSG 末尾追加「## 验证」并关闭。

## 4. 剩余问题（均非阻断）
- `echarts <6.1.0` moderate（GHSA-fgmj-fm8m-jvvx）：未升级（6.x 为 breaking；平台不渲染用户可控富文本 tooltip），建议后续评估。
- P2 记录：compose 的 `FL_ROUND_DELAY` 等 algo 内部可选变量（`FL_JOB_TTL`/`FL_MAX_JOBS`/`FL_MAX_RUNNING` 亦同，均带默认值）；`views/_Placeholder.vue` 无引用可删；`IdentityCenter` 展示 `privateKey`（契约允许「仅本次返回」，未写 localStorage）。
- 仍未覆盖：真实甲方 backend、`docker build`/`compose up` 实打（无 docker 权限）、真机（x86 断网 + 树莓派 4B）。
