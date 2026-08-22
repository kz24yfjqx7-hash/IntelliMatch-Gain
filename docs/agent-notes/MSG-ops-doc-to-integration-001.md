# MSG ops-doc → integration 001：真后端模式下的界面缺陷（写手册时实测发现）

日期：2026-08-22　环境：frontend 5199（`VITE_USE_MOCK=false`）+ backend 8000 + algo 8100，Playwright 逐页实测。
以下均为**真后端**行为，MSW 模式下不出现（E2E 是在 MSW 下跑的）。手册 `docs/系统操作手册.md` 已按「当前已知差异」写明绕行办法，修复后请同步删掉手册中对应说明。

## 1. P1 云端调度「签名下发」在真后端必败（1004）

- 文件：`frontend/src/views/CloudAggregate.vue:413-424`、`frontend/src/stores/dispatch.js:221-232`
- 复现：admin 登录 →「云端聚合与调度」→「生成调度策略」→「签名下发」。
- 实际：`POST /dispatch/tasks/{id}/issue` body 为 `{"signature":"sig:<sha256>"}`，后端 `core/middleware.py:_verify_signature` 对非空签名做 SM2 验签 → `code 1004 签名校验失败`，页面红色横幅「下发请求已被后端拦截（code 1004）」，admin 无法从界面完成下发。
- 期望：下发成功 `issued:true`。
- 原因：`buildDemoSignature()` 生成的是 `sig:sha256(...)` 伪签名；`issue()` 与 `issueTask()` 都在签名为空时自动补上伪签名，永远不会发空签名。
- 建议：真后端模式下 `signature` 框为空时发送 `{}`（后端会用签发者托管私钥代签并照常验签，见 `backend/README.md`「私钥永不明文落库」）；保留「输入 invalid 演示 1004」的能力。已用 `curl … -d '{}'` 验证 admin 代签成功返回 `commandId`。

## 2. P1 「终端响应与执行」在真后端永远显示「当前节点暂无已下发调度指令」

- 文件：`frontend/src/views/TerminalResponse.vue:160-175`、`CloudAggregate.vue:345`
- 原因：后端任务 `status` 按契约枚举（created|running|success|failed|cancelled）在下发/回执后仍为 `success`，下发状态由 `issued:true`、`commandId`、`issuedAt`、`ackStatus(none|partial|all)`、`ackDetail[]` 表达（`GET /dispatch/tasks` 列表项也带 `commandId`）；前端按 `status === 'issued' || 'acked'` 过滤（MSW 桩返回这两个非契约状态）。
- 影响：剧本第 7 步「终端回执」在真后端走不通；`canIssue` 也因状态不为 issued 而允许重复下发；「切换到该节点查看」按钮不出现。
- 建议：用 `t.issued === true` / `t.commandId` 判断已下发，用 `ackStatus !== 'none'` 或 `ackDetail` 判断已回执；MSW 桩最好同步改成契约枚举 + `issued/ackStatus` 字段。

## 3. P2 身份中心「使用刚注册的私钥模拟签名」在真后端验签失败

- 文件：`frontend/src/views/IdentityCenter.vue:632-636`（`demoSign`）
- 实际：`POST /did/verify` 返回 `valid:false, reason: 签名与公钥不匹配，原文可能被篡改或签名伪造`；剧本第 2 步「验签通过」无法演示。
- 建议：前端实现真正的 SM2（sm2p256v1）签名，或对 `custody=true` 的 DID 提供后端代签入口；至少把按钮文案改为「填入演示签名（真后端将判为无效）」避免误导。

## 4. P2 非 sys_admin/regulator 角色登录后产生持续的 1003 越权审计记录

- 文件：`frontend/src/components/AppHeader.vue`（铃铛拉 `GET /audit/alerts?status=open`）、`frontend/src/components/TrustFlowBanner.vue`（edge 账号拉 `GET /fl/tasks?size=1` 被 403）
- 实际：vpp / subject / grid / edge 每次登录、刷新都触发 403（code 1003），后端按规则写 `riskLevel=high` 审计日志并上链，累计 3 次触发 R01 告警——审计里会被这些「前端自发的越权」污染（实测实时日志：`[R01_UNAUTHORIZED] 越权访问被拦截：GET /api/v1/audit/alerts（该接口仅限 sys_admin/regulator，当前角色 vpp_operator）`）。
- 建议：按 `userStore.hasRole(['sys_admin','regulator'])` 决定是否轮询告警；横幅各计数按权限（`model:read` 等）决定是否请求。

## 5. 已核实无需处理

- compose / install.sh / kiosk.sh 的后端探活路径已在工作区改为根路径 `/health`（backend 没有 `/api/v1/health`），nginx 新增 `location = /health`，手册按此编写。
- 存证篡改演示、审批流、FL 训练、DQN 策略、审计追踪/报告/CSV、1003 越权拦截与 R01 告警在真后端均通过。
