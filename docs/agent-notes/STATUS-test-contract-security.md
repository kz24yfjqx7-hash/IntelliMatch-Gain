# STATUS · test-contract-security（契约逐字段一致性审计 + 前端安全测试）

**更新**：2026-08-21　**结论**：`npx vitest run` 15 文件 / 137 用例全绿（含 qa tests、`views/__smoke__`、`views/__smoke_legacy__`）；`npm run build` 通过；`node src/mocks/selfcheck.mjs` 12/12；`qa/contract_audit.py` 阻断级 0。

## 交付物

| 文件 | 说明 |
|---|---|
| `qa/contract_audit.py` | 可重复运行的审计脚本（`.venv/bin/python qa/contract_audit.py [--algo-port N | --start-algo] [--json out]`）。A1 路径三方对照 + handler 注册顺序；A2 在 Node 内起 MSW 真实请求 73 个接口并与契约示例逐字段 diff（缺字段/类型/枚举/ISO8601+08:00/分页/traceId 沿用）；A3 views 字段粗扫；A4 起/连 algo 打 8 个接口对照第三部分，并核对 mock 与 algo 返回形态；B1 6 账号 × 22 个接口的 RBAC 矩阵、仅自有、无/伪造/过期/已登出 token、tamper 权限。退出码非 0 表示有阻断项。 |
| `frontend/tests/contract-audit.spec.js` | 54 个 vitest 永久回归用例（A1/A2/A4/B1/B2/B3/B4），与脚本同源。 |
| `docs/agent-notes/MSG-test-contract-security-to-test-e2e-001.md` | 转交 test-e2e 的 P2 事项（页面引用契约外字段、权限收紧对 e2e 的影响、XSS 结论）。 |

## 审计摘要（修复前 → 修复后）

| 项 | 修复前 | 修复后 |
|---|---|---|
| A1 契约 73 接口 ⇄ api/*.js ⇄ handlers | 缺失 0 / 多余 0 / 顺序错误 0 | 同 |
| A2 契约示例字段 ⇄ mock 实际返回 | 缺字段 0、类型不符 0；**时间格式错误 2**（`rotate-key`/`POST /keys` 的 `expireAt` 为 UTC `Z`）；**id 冲突 1**（运行时签发 DID 后 `POST /keys` 与既有密钥同 id，导致 freeze/revoke 打到别的密钥） | 0 / 0 / 0 / 0（多出字段 59 处均为向后兼容扩展，已记录） |
| A4 algo 8 接口 ⇄ 契约第三部分 | 缺字段 0、类型 0（扩展字段 `anomalies`、`immediateReward` 已在 MSG-algo 文档化） | 同 |
| A4 mock ⇄ algo 形态 | **1 处**：`constraintsChecked.violations[]` mock 用 `rule`，algo 用 `constraint/attempted/applied` | 0 |
| B1 RBAC / 仅自有 / token | **6 处**：伪造 token（合法 payload+错签名）被接受；登出后 token 仍有效；`energy_subject` 存证未按「仅自有」过滤；`lineage`/`stats` 未过滤；`audit/logs/export` 无权限门槛 | 0 违例 |

## 安全问题清单

| # | 级别 | 问题 | 状态 |
|---|---|---|---|
| S1 | P1 | mock `parseToken` 不校验签名，`currentUser` 会按 payload 恢复会话 → 伪造 token 可冒充 sys_admin | **已修** `mocks/helpers.js`：重算签名比对；三段式/exp 类型校验 |
| S2 | P1 | 登出不吊销 token（内存 session 丢失后按 payload 恢复） | **已修** `db.revokedTokens` 黑名单 + `issueToken` 加 `jti` 防同秒同 token |
| S3 | P1 | `energy_subject` 可读全部存证/任意资产溯源 | **已修** `helpers.isOwnOnly/ownDids`，`evidence.js visibleBlocks`，`asset.js visibleAssets` 复用 |
| S4 | P2 | 审计 CSV 导出对所有角色开放（与 R04 规则/矩阵 `asset:export` 不符） | **已修** 需 `asset:export` |
| S5 | P2 | `POST /keys` 与运行时 `createDid` 密钥 id 冲突 | **已修** `db.nextId('key')` 与 `keyId` 共用计数器 |
| S6 | P2 | `v-permission` 仅在组件重渲染时更新，切换账号不刷新页面时不会恢复元素 | **已修** 指令改为 watch `permissions`，移除用注释锚点占位、可恢复；`.disable` 双向切换 |
| S7 | P2 | 退出登录后 `logStore` 的 WS 订阅未清理 | **已修** `AppLayout` `onBeforeUnmount → detachWs()`（`logout()` 本身已 `wsClient.disconnect()`） |
| S8 | 信息 | `request.js` 1002 处理：已在 `/login` 时不再 `replace`，多次 1002 不死循环（用例验证） | 无需改 |
| S9 | 信息 | ws token 仅 query 传递；`api/ stores/ router/ main.js` 无 token 打印（用例验证） | 无需改 |
| S10 | 信息 | XSS：唯一 `v-html`（JsonViewer）先转义；其余插值渲染（用例验证） | 无需改，已通知 test-e2e |
| S11 | 信息 | 敏感信息：源码/`dist/` 无 API key、无 `.env`；演示口令仅在 `mocks/db.js`（`Login.vue` 有快捷登录提示，属演示需要）；`docs/`、`README`、`packaging/` 仅引用环境变量名 | 无需改 |
| S12 | 依赖 | `npm audit --omit=dev`：**1 moderate** — `echarts <6.1.0` XSS（GHSA-fgmj-fm8m-jvvx），修复需升级到 6.x（breaking，本项目用 5.5 API、jsdom 下用桩）；`pip check`：无冲突（fastapi 0.141.1 / uvicorn 0.52.4 / httpx 0.28.1 / numpy 2.5.2） | **记录，未升级**（需 frontend 评估 6.x 迁移；平台不渲染用户可控的富文本 tooltip，风险低） |

## 其它决定 / 备注

- `POST /dispatch/tasks/{id}/run` 仍按 `dispatch:read` 放行（DB-SCHEMA 注记「DQN 调度的 algo:execute 仅 sys_admin/grid_dispatcher」可解读为需 `algo:execute`），**未改**：验收链路 6 要求 vpp 能走到 `issue` 并得 1003，旧页 CloudAggregate 的 run→issue 流程依赖 vpp 可 run。请总控裁定；改动只需在 `mocks/handlers/dispatch.js` run 处把 `dispatch:read` 换成 `algo:execute`。
- `POST /did/register`、`POST /keys*`、`POST /evidence`、`POST /audit/alerts/{id}/ack` 等在矩阵中无对应权限列，mock 仍为「登录即可」。
- 审计日志 `resourceType` 在 DB-SCHEMA 中为自由文本（did/user/role/node…），脚本只对 `/permissions/*` 接口套契约 `resourceType` 枚举。
- 审计脚本 A4 默认连 `--algo-port 8197`；不可达时只告警跳过；`--start-algo` 会自行起 uvicorn 并按 pid 结束。
- 未改动 `views/`、`components/`、`algo-service/`。
