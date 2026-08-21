# STATUS-frontend-infra

更新：2026-08-21

## 已完成
- `src/utils/`：`traceId.js`（tr-YYYYMMDD-8hex）、`format.js`（ISO+08:00 与显示格式化、枚举中文）、`sha256.js`（同步纯 JS SHA-256 + canonical JSON）。
- `src/api/`：`request.js`（Bearer / X-Trace-Id 注入、解包、1002 跳登录、1003/1004 提示、`download()`/`saveBlob()`）+ 13 个模块（签名与 DEV-PLAN §3.2 逐字一致）+ `ws.js`（心跳/退避重连/mock 通道、`status` ref）+ `index.js`。
- `src/stores/`：`user.js`（新）；`logs.js`/`perspective.js`/`dispatch.js` 改造，旧字段/方法全部保留，7 个旧页未改即可编译运行。
- `src/router/index.js`：`/login` + `AppLayout` 子路由（原 7 条 + 4 中心），`requiresAuth`/`permission` 守卫，会话恢复。
- `src/layouts/AppLayout.vue`、`App.vue` 仅 `<router-view/>`、`main.js`（MSW 启动、指令注册、zh-cn locale）。
- `src/directives/permission.js`（`v-permission` / `.disable` 修饰符 / 数组任一）。
- `components/AppHeader.vue`（新标题、离线徽标、用户名+角色标签、告警铃铛下拉可 ack、退出）、`AppSidebar.vue`（「可信数据空间」分组四中心、DID 状态点）、`AppLogBar.vue`（WS 状态灯、traceId）。
- `views/Login.vue`（深色科技风、6 账号一键填充、按角色跳转、Mock 徽标）；`views/_Placeholder.vue` + 四个中心占位页。
- `src/mocks/`：`db.js`（6 用户/6 角色矩阵/17 DID/密钥与轮换/4 节点/80 资产/30 天小时级指标/8 申请 5 授权/≈80 审计日志/3 告警/2 FL 任务+2 模型/2 调度任务/AI 与风险历史）、`chain.js`（LocalHashChain：tamper/verify/status 真算断裂点）、`helpers.js`（包装、token、权限校验+审计+R01、分页、存证、告警）、`handlers/*.js` 12 模块全部契约接口、`wsMock.js`、`algo/fedavgLite.js`（MLP+DP+Top-k 真算）、`browser.js`、`node.js`、`selfcheck.mjs`。
- `vite.config.js`（5173、/api 与 /ws 代理、manualChunks）、`vitest.config.js`、`.env.development`、`.env.example`、`README.md`、`index.html` 标题。

## 自测结果
- `npm run build`：零错误（13s）。
- `VITE_USE_MOCK=true npx vite --port 5199`：`/ /login /src/main.js /mockServiceWorker.js /src/mocks/browser.js` 等均 200，已杀掉。（本机 5173 被其它 nginx 占用，dev 请换端口。）
- `npm test`（qa 套件）：55/56；唯一失败为 `services/scientificCompute.js` Pyodide CDN URL（frontend-legacy 范围）。
- `node src/mocks/selfcheck.mjs`：12/12

| # | 链路 | 结果 | 说明 |
|---|---|---|---|
| 1 | 服务可用（mock 桩代替 compose） | PASS | 未登录 1002 |
| 2 | admin 登录 / 6 账号 / subject 访问 /users→1003 | PASS | 权限 9 项 |
| 3 | 注册设备 DID → 列表 → 文档 → 验签 → 密钥轮换 | PASS | valid=true，v2 |
| 4 | 登记 pv 资产 → 自动分级 → sm3 摘要 → 上链 → 溯源 → stats → 分页 | PASS | 资产总数 81 |
| 5 | subject 申请 → vpp 审批 1003 → admin 审批 → check allowed | PASS | |
| 6 | vpp issue → 403/1003 → high 审计 → R01 告警 → WS audit_alert → ack | PASS | |
| 7 | FL 创建/启动 → 5 轮真算 → 每轮存证 → fl_progress ×5 → loss 下降 → 模型 | PASS | 压缩率 90.1%，ε 累计至 1.0 |
| 8 | 调度 run → 策略/qTable/cache 解释 → 坏签名 1004 → admin issue → edge ack → 5 阶段 WS | PASS | |
| 9 | tamper（非 admin 1003）→ verify intact:false → chain brokenAt → 凭证 | PASS | |
| 10 | /audit/trace 越权 traceId 与调度 traceId；不存在 1005 | PASS | |
| 11 | 日报 narrative/narrativeSource=cache；stats；CSV 导出 | PASS | |
| 12 | 节点/指标/上线验签/风险评估/node_status/AI qa/logout | PASS | |

## 对外约定 / 变化
- 说明书：`MSG-frontend-infra-to-all-001.md`。
- mock `hash` 为 `sm3:<sha256hex>`；演示签名规则：长度 ≥8 且不以 `invalid|bad` 开头即有效。
- R01 在 `dispatch:issue` 越权时**首次即告警**（演示需要），其它权限拒绝按 5 分钟 ≥3 次。
- `dispatchStore.dispatchTask()` 保持同步语义，后台 issue；新页请用 `issueTask()`。

## 已知问题 / 待办
- `services/scientificCompute.js` 含 Pyodide CDN URL（frontend-legacy 负责删除）。
- 四个中心页为占位，等 frontend-pages 覆盖。
- mock 的 FL acc（相对误差 <10% 占比）在 5 轮内约 0.3~0.5，与 algo 说明一致；loss 单调下降。

## 阻塞
无
