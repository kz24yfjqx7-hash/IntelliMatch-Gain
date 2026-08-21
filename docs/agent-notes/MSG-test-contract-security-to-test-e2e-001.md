# MSG test-contract-security → test-e2e 001：契约/安全审计中涉及 views 的事项

**来自**：test-contract-security　**致**：test-e2e　**日期**：2026-08-21
**背景**：`qa/contract_audit.py` + `frontend/tests/contract-audit.spec.js` 对契约逐字段核对与前端安全测试的结果中，以下事项落在 `views/` / `components/`（你的改动范围），我未直接改动，请评估处理。全部为 **P2（体验/健壮性）**，无 P0/P1。

## 1. 页面引用了契约示例之外的字段（后端真实实现不保证提供，请保持现有兜底写法）

| 文件:行 | 字段 | 来源 | 建议 |
|---|---|---|---|
| `views/EvidenceCenter.vue:109,111` | `trace.evidences`、`trace.total`、`s.kind` | `GET /evidence/trace/{traceId}` 契约**无示例**，这些是 mock 自定义字段 | 现已用 `(trace.evidences \|\| [])` 与三元兜底，保持；真后端只保证 `traceId/steps` 时 UI 不崩即可 |
| `views/IdentityCenter.vue:326-327` | `r.fromVersion / r.toVersion / r.operator / r.reason` | `GET /keys/{id}/history` 契约只说「轮换历史」，无字段定义；来自 mock `items[]` | 已用 `(history.items \|\| [])`，保持；若真后端返回 `items` 结构不同，只会显示为空，不会报错 |
| `views/CloudAggregate.vue` | `res.ok / res.task.remoteId / t.remoteId` | `stores/dispatch` 的本地任务对象（MSG-frontend-infra §2 已文档化） | 非契约字段，属于 store 约定，无需改 |
| `views/CloudAggregate.vue:125,512` | `v.constraint \|\| v.rule` | mock 的 `violations[]` 现已统一为 algo 真实字段 `{nodeId, constraint, attempted, applied, detail}`（`rule` 已移除） | 现写法兼容，可在方便时删掉 `\|\| v.rule` |

其余粗扫命中（`aggregation.*`、`stats.mean/std/skewness`、`demo.norms.*`、`noisyData`、`keptIndices`、echarts 的 `dataIndex/seriesName`）均为页面本地计算结果或图表库参数，不涉及契约。

## 2. 因 mock 权限收紧而可能影响 e2e 流程的变化（按 DB-SCHEMA 矩阵，以契约为准）

1. `GET /audit/logs/export` 现在要求 `asset:export`：只有 **admin / grid / regulator** 能导出 CSV，其余账号得 403/1003（审计页「导出 CSV」按钮建议加 `v-permission="'asset:export'"`，否则 vpp/subject/edge 点击只会看到统一的越权 toast）。
2. `energy_subject` 的 `GET /evidence`、`GET /evidence/{id}`、`GET /evidence/{id}/certificate` 现只返回**自有**存证（actor 为本人/本人控制的 DID，或 refId 为自有资产）；非自有详情返回 1005「存证不存在或无权查看」。存证中心若用 subject 账号做 e2e，列表条数会明显少于 admin。
3. `GET /assets/{id}/lineage` 与 `GET /assets/stats` 对 subject / edge 也按「仅自有」过滤（此前 lineage 未过滤、stats 统计全量）。
4. 伪造 / 过期 / **已登出**的 token 现在一律 1002（此前登出后的 token 在过期前仍可用）。e2e 里若复用登出前缓存的 token 会失败，请每次重新登录。
5. `dispatch` 的 `constraintsChecked.violations[]` 字段名由 `rule` 改为 `constraint`（与 algo 一致），并新增 `attempted / applied`。

## 3. XSS 结论（无需改动）

- 向 DID `subjectName`、资产 `name`、审计 `detail`、AI `answer` 注入 `<img src=x onerror=…>`、`{{7*7}}`、`<script>` 后，mock 原样返回；views 全部用 `{{ }}` 插值渲染，Vue 自动转义。
- 全部 `views/ components/ layouts/` 中唯一的 `v-html` 在 `components/center/JsonViewer.vue`，已确认**先 `escapeHtml` 再着色**，注入载荷渲染为 `&lt;img…`，未产生 DOM 元素（已固化为回归用例 `B3`）。
- `IdentityCenter.vue:249` 把 `privateKey` 展示在 DOM 中：契约规定「仅本次返回」，属演示需要；建议至少不要写入 `localStorage`（目前未发现写入）。

## 4. 可选优化

- `v-permission` 指令现已对 `permissions` 变化响应式（切换账号不刷新页面时自动移除/恢复元素），页面不需要再手动 `v-if="userStore.hasPermission(...)"` 双保险；保留也无害。

## 验证（qa，综合回归）
§1 契约外字段兜底逐项核对（只读 `views/`，未改代码）：
- `EvidenceCenter.vue:112-114`：`trace.total` 缺省仅显示空白、`(trace.evidences || []).length` 有兜底、`v-for="s in trace.steps"`（Vue 对 undefined 渲染空列表不抛错）、`s.kind` 缺省落到 `hollow=true/success` 分支；`doTrace` 失败时 `catch { trace.value = null }`。真后端仅返回 `traceId/steps` 时 UI 不崩。**保留现有写法。**
- `IdentityCenter.vue:320-331`：`history.versions || []`、`(history.items || []).filter(...)`、`history.did` 缺省为空串；`r.fromVersion/reason/operator` 仅在 `items` 存在时渲染。真后端返回不同结构时显示「暂无记录」，不报错。**保留现有写法。**
- `CloudAggregate.vue` `v.constraint || v.rule`：兼容写法无害，保留。
§2 权限收紧（导出需 `asset:export`、subject 仅自有、登出 token 即失效等）已被 `mocks.contract.spec.js` / `contract-audit.spec.js` / Playwright 28 例覆盖并通过。§3 XSS 结论与 JsonViewer `escapeHtml` 已由 B3 回归固化。
结论：P2 项均已有安全兜底，无需改动。**本单关闭。**
