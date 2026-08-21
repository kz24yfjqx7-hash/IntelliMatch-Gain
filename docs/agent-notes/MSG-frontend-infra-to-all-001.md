# MSG frontend-infra → all 001：前端基础设施使用说明（api / stores / ws / v-permission / mock）

**来自**：frontend-infra　**致**：frontend-pages / frontend-legacy / qa / deploy　**日期**：2026-08-21

`npm run build` 零错误；`node src/mocks/selfcheck.mjs` 12/12 通过；`npm test` 55/56（唯一失败是 `services/scientificCompute.js` 的 Pyodide CDN URL，归 frontend-legacy）。
以下是页面 Agent 的"说明书"。函数名与 DEV-PLAN §3.2 完全一致，**不要自己再封装 axios**。

---

## 1. api 模块

```js
// 按需从汇总入口导入（推荐）
import { login, listNodes, registerDid, tamperEvidence, exportAuditLogs, saveBlob } from '@/api'
// 或分模块
import * as didApi from '@/api/did'
```

- 全部返回 `Promise<data>`（已解包 `{code,message,data,traceId}` 的 `data`）。
- 失败 reject 的 `err` 带 `err.code / err.message / err.traceId`；`request.js` 已统一 `ElMessage.error`，页面**不必再 toast**，只需 `try/catch` 处理 UI 状态。
  - `1002` → 自动清登录态并跳 `/login?redirect=`
  - `1003` 提示「越权操作：…」，`1004` 提示「DID 验签失败：…」
- 每个请求自动带 `Authorization: Bearer` 与 `X-Trace-Id`（`tr-YYYYMMDD-8hex`）。想拿到某次操作的 traceId：大多数写接口的 `data` 里会回 `traceId`（mock 与契约均如此：`createFlTask / runDispatchTask / registerAsset / aiAnalyze …`）。需要自定义 traceId（把多步操作串到一条链路）：
  ```js
  import request from '@/api/request'
  import { genTraceId } from '@/utils/traceId'
  const tid = genTraceId()
  await request.post('/dispatch/tasks/dp-1/issue', { signature }, { headers: { 'X-Trace-Id': tid } })
  ```
- CSV 导出：`const blob = await exportAuditLogs({ riskLevel: 'high' }); saveBlob(blob, 'audit.csv')`。
- 分页接口统一返回 `{items,total,page,size}`；筛选参数在 mock 中**真实生效**。

### 各模块签名（DEV-PLAN §3.2 原样）
```
auth:       login({username,password}) logout() getMe() listUsers(params) createUser(data) updateUser(id,data) deleteUser(id)
did:        registerDid(data) listDids(params) getDidDocument(did) changeDidStatus(did,{action,reason}) rotateDidKey(did) verifyDid({did,message,signature}) resolveDids(dids)
key:        listKeys(params) createKey(data) freezeKey(id) revokeKey(id) keyHistory(id)
asset:      registerAsset(data) listAssets(params) getAsset(id) getAssetLineage(id) classifyAssets({records}) getAssetStats()
permission: listRoles() createRole(data) updateRole(code,data) getPermissionMatrix() applyPermission(data) listApplications(params)
            approveApplication(id,data) rejectApplication(id,data) listGrants(params) revokeGrant(id) checkPermission(data)
evidence:   writeEvidence(data) listEvidence(params) getEvidence(id) verifyEvidence({evidenceId,payload}) getChainStatus()
            traceEvidence(traceId) tamperEvidence({evidenceId,newValue}) getCertificate(id)
audit:      listAuditLogs(params) getAuditTrace(traceId) listAlerts(params) ackAlert(id) getAuditReport({period,date}) getAuditStats() exportAuditLogs(params)→Blob
node:       listNodes(params) getNode(id) getNodeMetrics(id,params) nodeOnline(id,data)
fl:         createFlTask(data) listFlTasks(params) getFlTask(id) startFlTask(id) cancelFlTask(id) getFlRounds(id) listFlModels(params) publishFlModel(version)
dispatch:   createDispatchTask(data) listDispatchTasks(params) getDispatchTask(id) runDispatchTask(id) issueDispatchTask(id,{signature}) ackDispatchTask(id,data)
ai:         aiAnalyze({scene,context,question}) aiHistory(params)
risk:       assessRisk({nodeId,features}) riskHistory(params)
```

### 几个 mock 返回里的补充字段（契约之外、向后兼容，页面可用可不用）
- `listNodes` item 多 `location`；`getNode` 多 `didDocument / assetsCount`。
- `listEvidence` item 多 `tampered:boolean`、`blockHash`；`getChainStatus` 多 `tamperedIds[]`。
- `runDispatchTask` 多 `qTable / constraintsChecked / reasoning[]`。
- `getAuditTrace.steps[]` 多 `riskLevel / actorName / detail / resourceType / resourceId`；`summary` 多 `stepCount / evidenceCount`。
- `getAuditReport` 多 `totals{logs,highRisk,denied,alerts} / byModule[] / range{from,to}`。
- `getFlTask.rounds[]` 多 `evidenceId / at / nodeContributions[]`。
- `keyHistory` 返回 `{keyId,did,items(轮换记录),versions(各版本密钥)}`。
- `listAlerts` item 有 `id / ruleCode / ruleName / riskLevel / message / actorDid / status(open|acked) / traceId / createdAt / at`。

---

## 2. stores

```js
import { useUserStore } from '@/stores/user'
import { useLogStore } from '@/stores/logs'
import { usePerspectiveStore } from '@/stores/perspective'
import { useDispatchStore } from '@/stores/dispatch'
```

### user（新）
`token user roles permissions isLoggedIn loading primaryRole roleLabel displayName did`
`login(form) logout() fetchMe() restore() hasPermission(perm|perm[]) hasRole(role|role[]) homePath()`
- `permissions` 是 `/auth/me` 的扁平数组（`asset:read` …），`sys_admin` 额外有 `user:manage`（用户管理按钮用它）。

### logs（兼容 + 新增）
- 旧：`logs recentLogs addLog(content, level, source, options) addTaskLog(...) getLogsByLevel/Source/TaskId clearLogs`
- 新：`alerts openAlerts unackedAlertCount attachWs() detachWs() fetchAlerts({status}) ackAlert(id) pushAlert(alert)`
- `attachWs()` 已在 `AppLayout` 挂载时调用，WS 的 `log / audit_alert / evidence_written` 自动进底部日志栏；页面无需重复调用。
- `addLog` 的 `options` 新增 `traceId`，日志栏会显示。

### perspective（兼容 + 新增）
- `nodes` 现在是 `ref([])`（模板里照旧 `perspectiveStore.nodes`，脚本里若要原生数组请用 `perspectiveStore.nodes`（pinia 已解包）；**不要再当常量数组 import**）。
- node 结构：`{id,name,status,model,did,didStatus,metrics:{pvOutput,storageOutput,load,soc},lastSeenAt,data:{...metrics,model}}`；`data` 是旧页面别名，继续可用。
- 新：`fetchNodes()`（AppLayout 已调用一次并订阅 WS `node_status` 自动刷新）、`nodesLoaded nodesLoading applyNodeStatus(payload) centerMenus`。
- `centerMenus` = 四个中心（`/identity /assets /permission /evidence`，含 `permission` 字段），侧栏已渲染。

### dispatch（实现换成后端，签名兼容）
- 旧接口保留：`tasks activeTask latestTask latestDispatchableTask latestTaskTimeline initializeTask generateTaskWithDeepSeek dispatchTask markTaskExecuting completeTask failTask getTaskById getLatestTaskForNode getLatestReceivedTaskForNode resetTasks`
  - `generateTaskWithDeepSeek(reports, options)` 现在 = `createDispatchTask → runDispatchTask`，返回 `{ok, task}`；`task.aiResult.recommendation` 结构不变，另附 `task.remoteId / strategy / qTable / explanation / explanationSource / evidenceId / traceId`。`options.adapterOptions.mode==='failure'` 仍本地模拟失败（旧页「模拟失败」按钮）。
  - `dispatchTask(taskId)` 仍**同步**返回 `{ok, task}` 并把本地状态置 DISPATCHED，同时后台调 `issueDispatchTask`；若后端拒绝（1003/1004）任务会被标为 FAILED 并写日志。**新页面请改用 `await issueTask(signature)`** 以拿到真实结果。
  - `completeTask()` 同步完成并后台 `ackDispatchTask`。
- 新：`runTask(options)`、`issueTask(signature, taskId?)`（抛出带 `code` 的 error）、`ackTask(taskId?, data?)`、`fetchTasks()`→`remoteTasks`、`buildDemoSignature(taskId?)`（`sig:sha256(...)` 演示签名）。
- 演示签名规则（mock）：任意长度 ≥8 且不以 `invalid/bad` 开头的字符串视为有效；传 `'invalid'` 可演示 1004。

---

## 3. WebSocket

```js
import { wsClient, WS_TYPES } from '@/api/ws'
import { onMounted, onBeforeUnmount } from 'vue'

let off
onMounted(() => {
  off = wsClient.on(WS_TYPES.FL_PROGRESS, (payload, msg) => {
    // payload: {taskId, round, totalRounds, loss, acc, compressionRatio, epsilonSpent}；msg.traceId / msg.ts
  })
})
onBeforeUnmount(() => off && off())
```
- `WS_TYPES`: `NODE_STATUS FL_PROGRESS DISPATCH_PROGRESS AUDIT_ALERT LOG EVIDENCE_WRITTEN`；`wsClient.on('*', msg => …)` 订阅全部。
- `wsClient.status`（Vue ref）：`idle|connecting|connected|reconnecting|closed|mock`，日志栏已有状态灯。
- 连接由 `userStore.login()/restore()` 与 `AppLayout` 自动建立，页面**不要**自己 `connect()`。
- mock 模式：每 5s 推 `node_status`（4 节点指标随机游走，同时改 `/nodes` 返回值）；FL 每轮推 `fl_progress`+`evidence_written`；调度 run/issue/ack 推 `dispatch_progress`（`aggregating→computing→explaining→issued→acked`）；越权推 `audit_alert`；每个写操作推 `log`。

---

## 4. 权限指令与路由

```html
<el-button v-permission="'dispatch:issue'" @click="issue">签名下发</el-button>       <!-- 无权限移除 -->
<el-button v-permission.disable="'algo:execute'">启动训练</el-button>                 <!-- 无权限置灰 -->
<el-button v-permission="['asset:write','asset:export']">…</el-button>               <!-- 任一满足 -->
```
脚本里判断：`userStore.hasPermission('evidence:read')`。

路由（均在 `AppLayout` 下）：`/cloud/topology /cloud/aggregate(dispatch:read) /edge/* /audit /identity /assets(asset:read) /permission /evidence(evidence:read)`。四个中心当前是占位页 `views/{Identity,Assets,Permission,Evidence}Center.vue`，frontend-pages 直接整体覆盖即可，路由不用动。`meta.perspective` 为 `cloud|edge` 的路由会自动切换侧栏视角；中心页为 `center` 不切换。

---

## 5. 演示账号（mock，明文比对）

| 账号 | 密码 | 角色 | 关键权限 |
|---|---|---|---|
| admin | admin123 | sys_admin | 全部 + user:manage（审批/用户/篡改演示） |
| grid | grid123 | grid_dispatcher | algo:execute、dispatch:issue |
| vpp | vpp123 | vpp_operator | asset:write；**无** dispatch:issue（演示 1003 + R01） |
| subject | subject123 | energy_subject | 仅自有资产；访问 /users → 1003 |
| regulator | reg123 | regulator | 只读 + export |
| edge | edge123 | edge_node | 登录后落地 /edge/classification |

mock 种子：17 个 DID（含 1 冻结）、80 条资产（1001~1080）、4 节点 30 天小时级指标、8 条权限申请（2 pending）、5 条授权、约 80 条审计日志、3 条告警（2 open）、2 个已完成 FL 任务 + 2 模型、2 个已完成调度任务、链高度 ≈138。
`db.DEMO_TRACE`（一条完整 login→verify→check→fl:train→evidence 的 traceId）可通过 `listAuditLogs({action:'fl:train'})` 拿到用于审计页首屏演示。

---

## 6. 其它

- `utils/format.js`：`toIso8 nowIso fmtTime fmtDateTime fmtDate fmtNumber fmtPercent fmtThousands shortHash shortDid fmtDuration` 与 `RISK_LABELS LEVEL_LABELS ROLE_LABELS DATA_TYPE_LABELS`。
- `utils/sha256.js`：`sha256Hex(str) sha256Json(obj) canonicalJson(obj)`（同步）。
- `utils/traceId.js`：`genTraceId() isTraceId() dateStamp()`。
- 环境：`.env.development` 默认 `VITE_USE_MOCK=true`；联调后端时改 false（vite 代理 `/api`→`localhost:8000`，`/ws`→ws）。注意本机 5173 端口已被其它 nginx 占用，本地起 dev 请 `npx vite --port 5199`。
- mock 的 `hash` 字段格式为 `sm3:<sha256hex>`（注释已说明用 SHA-256 代替 SM3）；algo 服务的 `gradientHash` 是无前缀 hex，页面显示哈希时请兼容两种（用 `shortHash` 即可）。
