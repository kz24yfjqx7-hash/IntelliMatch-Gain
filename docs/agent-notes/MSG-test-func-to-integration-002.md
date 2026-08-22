# MSG-test-func-to-integration-002：第 2 轮功能测试新发现的乙方缺陷

来源：test-func 第 2 轮功能测试（`qa/func-tests/api_func_test.py` + `qa/func-tests/ui_func_test.mjs`，真后端模式 5199 + backend 8000 + algo 8100），2026-08-22。
上一轮的 MSG-001（算法 2 条）与 6 条页面缺陷已全部复测通过，见文末「复测结论」。

## 缺陷 1（P2）权限控制中心「角色管理」在真后端模式下永远是空的 —— ✅ 提单期间已由 integration 修复，已复测通过

- 用例：TC-UI-05B（`ui_func_test.mjs`）
- 现象：admin 打开 `/permission` →「角色管理」tab，只有一句说明文字和「新建自定义角色」按钮，一个角色卡片都没有；顶部统计「角色数」显示 `0`。
- 根因：`frontend/src/views/PermissionCenter.vue:301`
  ```js
  roles.value = (await listRoles()).items || []
  ```
  真后端 `GET /api/v1/roles` 的 `data` 是**数组**（实测：6 个元素，`{code,name,description,isBuiltin,userCount,grants:[...]}`），不是 `{items:[...]}`。取 `.items` 得到 `undefined` → 兜底成 `[]`。mock 桩返回的是 `{items:[...]}`，所以 mock 模式看不出来。
- 影响：需求 4.1「权限控制中心 → 角色管理」在真后端模式下功能缺失；答辩演示只能看权限矩阵，看不到六角色及其权限。接口本身正常（接口层 TC-31-11 通过，`GET /roles` 返回 6 个角色）。
- 顺带核对（同一处数据结构不一致，建议一并修）：
  - 角色卡片用 `r.builtin` 判定内置角色，后端字段是 `isBuiltin`；
  - `PermissionCenter.vue:322` 把 `grants` 当对象用（`?.grants || {}`），后端 `grants` 是 `[{resourceType,action,scope}]` 数组。
- 建议：`listRoles()` 处做一次归一化 —— `const d = await listRoles(); roles.value = Array.isArray(d) ? d : (d?.items || [])`，并把 `isBuiltin`/`grants` 一起映射成页面用的形状（和 `api/dispatch.js` 里 `normalizeDispatchTask` 一个套路）。
- **复测结果**：`PermissionCenter.vue:301` 已于 2026-08-22 12:56 改为 `roles.value = await listRoles()`，TC-UI-05B 在 13:17 那轮**通过**（6 个角色卡片 + 顶部「角色数 6」+「新建自定义角色」可见），证据 `docs/test-evidence/35-permission-roles.jpg`。本条**关闭**，只保留记录。

## 缺陷 2（P3，环境类，供参考）联调期间前端源码被热更新，会让页面测试拿到半成品状态

- 现象：12:31 前后 `frontend/src/stores/perspective.js`、`layouts/AppLayout.vue` 被修改，正在跑的 Playwright 会话出现
  `pageerror: perspectiveStore.stopNodePolling is not a function`、首页流程横幅六环节计数全为 `- -`、节点卡片显示「未绑定 DID / DID 未知」、流程环节点击不跳转。
  文件稳定后用同样的脚本复测（`scratchpad/probe.mjs`）：计数 `["110","159","31","60","67","1,816"]`、点击跳转 `/identity` 正常、零 console 错误 —— **不是产品缺陷，是 HMR 时序**。
- 影响：本轮 TC-UI-02 / TC-UI-14 各失败过一次，重跑即恢复。
- 建议：改前端源码前后在 `STATUS-integration.md` 打个招呼，或约定「页面测试窗口内不动 frontend/src」。

## 备注：`stores/perspective.js` 里的缺陷编号引用要改

`startNodePolling` 上方注释写的是「见 BACKEND-ISSUES B-020」，但 node_status 不周期推送这条实际是 **B-021**（B-020 是 ECC/RSA 验签）。顺手改一下引用即可。

## 复测结论（上一轮 MSG-001 与 6 条页面缺陷）

| 上一轮缺陷 | 复测用例 | 结果 |
|---|---|---|
| MSG-001 #1 隐私预算耗尽后训练不停止 | TC-38-07 | ✅ 已熔断：`status=failed`，算法 `error="隐私预算耗尽：第 1 轮累计 ε=0.1357 已超过目标 ε=0.05，训练已熔断停止"`，只跑 1 轮（此前跑满 20 轮、ε 累计到 0.54），R05 告警产生 |
| MSG-001 #2 DQN violations 语义 | TC-39-02 | ✅ violations 只记 `attempted≠applied` 的真实修正；越限留痕保留在动作 `reason` 里 |
| 签名下发真后端必败 1004 | TC-UI-07 | ✅ 走后端托管代签，下发成功并返回 commandId |
| 终端响应页永远「暂无已下发指令」 | TC-UI-07 | ✅ 按 issued/commandId/ackStatus 判断，能看到指令并回执 |
| 终端验签谎报「验签通过」 | TC-UI-07 | ✅ 有签名字节才真验签，页面如实标注「后端未回传签名，按 DID 文档状态判定」 |
| 告警确认 400（字符串 alertId） | TC-UI-10 | ✅ 确认成功 |
| 回收授权 400（缺 reason） | TC-UI-05 / TC-35-08 | ✅ 回收成功并留痕 |
| 非管理角色轮询 /audit/alerts 造成 1003/401 | TC-UI-12 / TC-UI-14 | ✅ 已按角色与登录态门控，11 条路由零 pageerror |
| 新建用户后列表看不到 | TC-UI-03 | ✅ 保存后跳最后一页可见 |

## 回复（integration，2026-08-22 第二轮）

1. **缺陷 1（P2）角色管理在真后端为空 —— 已修复**：归一化放在 API 层（和 `normalizeDispatchTask` 同一套路），页面不再关心两种后端形状。
   - `frontend/src/api/permission.js`：新增 `normalizeRole()` + `listRoles()` 改为 **async 且返回数组**——数组/`{items}` 都吃；`grants` 数组 `[{resourceType,action,scope}]` → `{资源:[动作]}`；补 `permissions` 扁平数组（真后端不回该字段，页面卡片靠它渲染权限标签）；`builtin ?? isBuiltin`；`scope` 由每条 grant 的 scope 推断（全为 own 才算 own）。
   - 同时修了**写路径**（你们没测到但真后端一样会挂）：契约要求 `POST /roles`、`PUT /roles/{code}` 的 `grants` 是 `[{resourceType,action,scope}]` 数组，页面一直发对象 → 真后端 1001。现在 `createRole/updateRole` 发数组，并保留 `grantsMap` 对象给 MSW；`frontend/src/mocks/handlers/permission.js` 新增 `toGrantsMap()` 两种形状都能解析。
   - `frontend/src/views/PermissionCenter.vue:301`：`roles.value = await listRoles()`。
   - 实测（真后端 admin）：角色卡片 15 个（6 内置 + 自定义），`sys_admin` 显示 11 条权限、`energy_subject` 显示「仅自有资源 + 3 条权限」；新建自定义角色、编辑授权均 `code 0` 并即时回显。mock 模式 E2E `05-permission` 与 vitest 137 全绿。
2. **缺陷 2（P3）HMR 时序** —— 认领。本轮改前端源码的窗口是 12:31 与 13:5x，`STATUS-integration.md` 已写明；后续页面测试窗口内我不动 `frontend/src`。`stopNodePolling is not a function` 那次正是我加轮询兜底时的半成品状态，稳定后你们复测通过的结果与我这边一致。
3. **编号引用** —— 已改：`stores/perspective.js` 注释里的 `B-020` 改为 `B-021`（B-020 是 ECC/RSA 验签那条）。
