# STATUS · docs-test-rbac（分角色 RBAC 测试文档补齐）

- 所有者：docs-test-rbac 子 Agent
- 日期：2026-08-23
- 范围：只改 `docs/测试文档.md`、`docs/测试文档-接口与安全.md`，并新建本文件。未跑测试、未改代码、未改 `docs/系统操作手册.md`。
- 地面真相来源：`docs/系统操作手册.md` §3.3 各身份的功能界定（三张权威矩阵，已用 6 演示账号实测核对代码）；另交叉核对了后端 `sys_role_permission` 种子（`backend/sql/02_seed.sql`）、`frontend/src/router/index.js`、`frontend/src/stores/perspective.js`、`frontend/src/stores/user.js`、各 view 的 `v-permission`。

## 一、两份文档改了什么

### A. docs/测试文档.md（功能测试）
- **§4 新增 4.1「分角色登录界面核对表（6 账号）」**：6 个演示账号逐行核对 默认落地页 / 侧栏可见菜单集合 / 看不到的菜单 / 被隐藏的关键按钮 / 直连受限页被拦回首页的表现，判定列「通过·与设计一致」，证据引用手册 §3.3 三矩阵 + TC-UI-12（真实 Chromium）+ TC-31-12 + `GET /auth/me`。
- **§4 新增 4.2「6 角色 × 页面/操作 覆盖对账」**：逐角色标注边界由哪层用例覆盖，明确区分「页面层自动化实测（TC-UI-12）」「接口层自动化实测（TC-31-12 等）」「权限点核对（/auth/me）」「依据 §3.3 守卫矩阵核对」，形成 6 角色全覆盖且不谎称跑了没跑的用例。
- **§1 追溯矩阵**：3.1 RBAC 行、3.5 权限控制行 各补一处指向 §4.1/§4.2 的引用。

### B. docs/测试文档-接口与安全.md（接口/安全）
- **§6 新增 6.1「接口级 RBAC 矩阵（6 角色 × 关键写/读接口）」**：6 角色 × `POST /assets`、`POST /dispatch/tasks/{id}/issue`、`POST /fl/tasks`、`POST /users`、`GET /audit/*`、`GET /evidence` 的期望 code（0 放行 / 1003 拒绝），与后端 `@require_permission` / `require_roles` 逐格一致；标注 scope=own 行为（subject 存证/资产列表按属主收窄，B-002 已修复；scope=all 四角色不受影响）。
- 同节强调「前端 `v-permission` 裁剪 + 后端 `@require_permission` 双保险」安全结论，及越权 1003 写 high 审计 + 上链 + 5 分钟累计 3 次触发 R01 告警。

## 二、6 角色 × 页面/操作 覆盖对账（自动化实测 vs 权限点核对/人工）

| 角色 | 页面层自动化（TC-UI-12，真实 Chromium） | 接口层自动化（TC-31-12 等） | 权限点核对 /auth/me | 依据 §3.3 守卫矩阵核对（非自动化） |
|---|---|---|---|---|
| admin | —（未针对全开场景单独跑） | ✔ GET /users→0、/audit/logs→0 | ✔ 权限全集 | 「无受限页」组合 |
| grid | — | ✔ GET /audit/logs→1003 | ✔ 无 asset:write/user:manage | /audit 直连拦回 |
| vpp | ✔ 无签名下发/篡改/新建用户/审批、privacy 创建FL 禁用 | ✔ API-FL-04/05、API-DP-06 | ✔ | — |
| subject | ✔ 直连 /cloud/aggregate 被守卫拦回 | ✔ own：API-ASSET-11~13、API-EV-17/17b | ✔ | 其余受限页拦回 |
| regulator | ✔ 打开 /audit | ✔ API-ASSET-15、API-AUD-19 | ✔ 无写权限 | 无受限页组合 |
| edge | ✔ 侧栏无存证 + 边端视角 | ✔ GET /evidence→1003、/fl/models→0 | ✔ | /evidence、/audit 直连拦回 |

结论：6 角色界面与可用功能边界全部覆盖、判定「与设计一致」。页面级自动化实测集中在受限角色 vpp/subject/edge/regulator；admin/grid 的界面全开/无审计边界以 /auth/me 权限点核对 + 接口层 TC-31-12 佐证；「无受限页可被拦回」的组合按 §3.3 守卫矩阵核对，未逐一自动化——文中已如实标注，未写成自动化通过。

## 三、核对中发现的「文档↔代码不一致」（仅报告，未改任何代码/手册）

1. **手册 §3.3「关键操作按钮矩阵」的「资产导出 = asset:export」按钮在前端不存在**。全量 grep `frontend/src/` 无任何 `v-permission="'asset:export'"` 的导出按钮：`AssetsCenter.vue` 唯一 v-permission 按钮是「登记资产」(asset:write)；其 export 只出现在「申请权限」表单的 action 单选项（read/write/execute/export），非导出动作按钮。`asset:export` 仅命中 mock 数据/指令注释。→ §3.3 把「资产导出」列为按钮不准确；`asset:export` 目前只是**后端种子矩阵中的权限点**（admin/grid/regulator 有），无对应前端 UI。本次测试文档按 §3.3 权威口径保留该权限点表述，未将其标为「按钮已自动化验证」。
2. **「发布模型」前后端门控口径不一致**：前端 `CloudAggregate.vue:80` 用 `v-permission` = `algo:execute`，后端 `POST /fl/models/{version}/publish` 用 `@require_permission("model","read")`（`backend/modules/algo/router.py`:57-60）。后果：vpp_operator/regulator/edge_node 前端无「发布模型」按钮，但后端接口对持 `model:read` 的角色放行。建议甲方确认预期门控口径。为避免断言错误代码，接口级 RBAC 矩阵**未纳入** `/fl/models/publish`。
3. `stores/perspective.js` 的菜单数组是 2/4/4（云端2、边端4、可信空间4），与 §3.3 一致；但「可信数据空间 4 项两视角固定」是**视角**层面的固定，实际仍受 `AppSidebar.vue`:100-102 的权限过滤——edge_node 无 evidence 权限故只显示 3 项、energy_subject 云/边菜单也被 dispatch:read/model:read 过滤。这与 §3.3 页面可见性矩阵（edge evidence ✗、subject own 等）**一致**，非缺陷，仅口径提醒；本次 §4.1 表已按最终可见性（edge 无存证）如实呈现。

以下断言经代码核对**完全一致**：后端种子矩阵六角色（`02_seed.sql`）、前端路由 meta.permission/roles（含 /audit 为角色级门控）、后端各 router 的 require_roles 硬限制、`stores/user.js` canReadAudit 仅 sys_admin/regulator、v-permission 中 用户管理=user:manage / 登记资产=asset:write / 签名下发=dispatch:issue / 创建FL=algo:execute。

## 四、格式自检
- 两处新增表格列数一致（§4.1 为 7 列、§4.2 与接口矩阵各自列数统一）；代码块闭合；未新增/破坏锚点；中文，语气与既有文档一致。
