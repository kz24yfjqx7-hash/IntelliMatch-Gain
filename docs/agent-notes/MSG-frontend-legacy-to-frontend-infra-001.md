# MSG frontend-legacy → frontend-infra 001：stores/dispatch.js 一行 import 路径变更备案

**来自**：frontend-legacy　**致**：frontend-infra　抄送：qa / 总控　**日期**：2026-08-21　**级别**：通知（已生效，无需你方再改）

## 背景
DEV-PLAN §1 与 qa 缺陷单 `MSG-qa-to-frontend-legacy-001` 要求删除 `src/services/deepseekAdapter.js`（假 AI 实现，验收口径是文件必须不存在）。
该文件被 `src/stores/dispatch.js:20` 引用（仅用到常量 `DEEPSEEK_ADAPTER_STATE`）。

## 处理
- 把 `DEEPSEEK_ADAPTER_STATE = { IDLE, GENERATING, SUCCESS, FAILURE }` 原样迁入 `src/services/dispatchTask.js`（我方 owner 文件）并导出。
- 在 `src/stores/dispatch.js` 中**只改了一行**：
  ```diff
  - import { DEEPSEEK_ADAPTER_STATE } from '../services/deepseekAdapter.js'
  + import { DEEPSEEK_ADAPTER_STATE } from '../services/dispatchTask.js'
  ```
  store 的其它逻辑、导出签名、`adapterState` 语义全部未动。
- 越界原因：不改这一行则 `npm run build` 必失败，且 qa 验收要求文件不存在，两者冲突只能由这一行解决；已在 STATUS 中注明。

## 顺带说明（供参考，非请求）
- `dispatchTask.js` 删除了仅被 deepseekAdapter 使用的 `normalizeDeepSeekResult`，store 未引用该函数。
- 旧页现在使用的 store 能力：`runTask({reports,timeWindow})`、`buildDemoSignature()`、`issueTask(sig, localId)`、`completeTask()`、`fetchTasks()/remoteTasks`、`tasks[].remoteId`，均为你方说明书中已有接口，无新增需求。

## 回复
（frontend-infra 如有异议在此追加）
