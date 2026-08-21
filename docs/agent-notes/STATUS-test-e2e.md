# STATUS-test-e2e

更新：2026-08-21　　工具：Playwright 1.x + Chrome Headless Shell 151（`npx playwright install chromium` 成功，无需系统依赖）

## 结果

- **E2E 用例 28 个，全部通过**（`frontend/e2e/*.spec.js`，单 worker 串行 ≈3.2 min）。
- 每个用例统一断言：无 `console.error`、无未捕获异常、无 4xx 资源请求（仅 401 密码错误 / 403 越权与验签失败两个被测场景白名单）、关键文本可见。
- `npm run build` 零错误；`npx vitest run` 137/137 全绿（e2e 目录不在 vitest include 内）。
- 截图 20 张 / 1.5MB：`frontend/e2e/screenshots/*.jpg`（1366×768 为主，3 张 1920×1080）。
- dev server（5197）已杀掉。

运行方式见 `frontend/e2e/README.md`；`package.json` 新增 `test:e2e` / `test:e2e:headed`，devDependencies 新增 `@playwright/test`。

### 覆盖对照

| 剧本步 / 验收条 | 用例 |
|---|---|
| 步1 / 验收2 登录 → 驾驶舱、六环节计数、4 节点、WS 刷新 | 01-auth（6 账号）、02-home |
| 步2 / 验收3 注册 DID → 文档 → 验签 | 03-identity |
| 步3 / 验收4 自动分级 → 登记上链 → 溯源 | 04-assets |
| 步4 / 验收5 subject 申请 → admin 审批 → 生效 | 05-permission |
| 步5 / 验收6 vpp 越权 → 1003 → R01 铃铛 → 审计 high → traceId | 06-dispatch-deny |
| 步6 / 验收7 FL 启动 → 曲线 ≥3 轮 → 预算环 / 压缩率 / 哈希 | 07-privacy-fl |
| 步7 / 验收8 DQN → 策略表 → AI 解释徽章 → 签名下发 → 终端 ack（+1004） | 08-dispatch |
| 步8 / 验收9 篡改 → verify 标红 → brokenAt | 09-evidence |
| 步9 / 验收10、11 traceId 时间轴、告警 ack、日报 narrative、导出 CSV | 10-audit |
| 视角切换、退出后受保护路由被拦、按钮级 v-permission | 01-auth、02-home |
| 1366×768 / 1920×1080 无横向滚动、区域不重叠、ECharts 尺寸非 0、切路由 20 次无泄漏 | 11-layout-memory |

## 发现并修复的问题（均在 views/ 内）

| # | 现象 | 截图 | 修复 | 复验 |
|---|---|---|---|---|
| 1 | `/evidence` 在 1366×768 下 `.content-area` 出现横向滚动（scrollWidth 1262 > 1146）：`.evidence-grid { grid-template-columns: 3fr 2fr }` 覆盖了父级 `minmax(0,1fr)`，存证表格 min-width 把列撑开，右侧「业务链路追踪」面板被挤出可视区 | `evidence-chain-broken.jpg`（修复后） | `frontend/src/views/EvidenceCenter.vue` 样式 `.evidence-grid` → `minmax(0, 3fr) minmax(0, 2fr)` | 09-evidence、11-layout 两分辨率均无溢出 |
| 2 | 篡改演示后页面**看不到任何标红**：mock 选中的是 `data` 类存证（如 ev-000099，高度 99），既不在「最近 16 块」链条（#121~136）里，也不在检索表第 1 页；只有校验弹窗与统计卡变化，与剧本「该条目整行标红」不符 | `evidence-chain-broken.jpg` | `EvidenceCenter.vue`：`loadBlocks()` 把 `chain.tamperedIds` 中不在窗口内的块用 `getEvidence` 取回**钉到链条最前**并加 `···` 省略标记，有钉块时滚动到起点；`submitTamper()` 成功后按该存证的 `did/refId` 过滤检索表并选中该行（`tampered-row` 整行标红）；模板改为 `<template v-for>` 支持 gap 节点，新增 `.block-gap` 样式 | 09-evidence：`.block.tampered` 在视口内、`.el-table__row.tampered-row` 可见、`.block.affected` > 0 |
| 3 | 「终端响应」页：签名下发的指令目标是 Node-C，而边端默认当前节点 Node-A，页面只显示「当前节点暂无已下发调度指令」，演示第 7 步会卡住 | `terminal-ack.jpg`（切换后） | `frontend/src/views/TerminalResponse.vue`：新增 `otherNodeTask` computed，空态下显示「最新指令目标为 Node-X → 切换到该节点查看」按钮，一键 `perspectiveStore.setCurrentNode` | 08-dispatch：切换后自动验签通过 → 回执 acked → 回执存证 ev-xxx 显示 |

## 测试过程中确认的非问题（记录以免误判）

- `edge` 账号侧栏无「区块链存证」「能源数据资产」菜单：`centerMenus` 按 `evidence:read` / `asset:read` 过滤，edge_node 角色无此权限，符合矩阵。
- `subject` 访问 `/cloud/aggregate` 被守卫拦回首页并 toast，符合 `meta.permission: dispatch:read`。
- 越权下发 / 无效签名返回 HTTP 403（code 1003 / 1004），密码错误 401：浏览器会打印 `Failed to load resource` 的 console.error，这是被测行为，其余场景零 console.error。
- 所有订阅 `wsClient.on` 的 7 个页面组件都在 `onBeforeUnmount` 注销；切路由 20 次后 WS 推送期间 console 输出不增长，JS 堆（强制 GC 后）未超过首次采样的 2.5 倍。
- ECharts：11 条路由、两种分辨率下所有 `[_echarts_instance_]` 容器宽高均 > 0。
- headless 环境缺中文字体，截图中文呈方块，属于机器字体问题，不是页面问题。

## 无法自动化 / 未覆盖

- 步 10「拔网线」：mock 模式本身不依赖外网，E2E 仅验证了 AI 解释徽章存在（mock 恒 `cache`）；真实断网降级需联调后端验证。
- DeepSeek `source=live` 分支、真实后端 SM2 验签、`docker compose` 一键启动（验收 1、12）属于部署 / 后端范围。
- 双浏览器实时联动（vpp 越权时 admin 另一浏览器铃铛 +1）：MSW 数据库是页内内存，跨标签不共享，E2E 在同一页面用 SPA 内切换账号验证了同等链路；真实后端 WS 广播需联调验证。
- 权限中心「有效授权」统计分页近似值（STATUS-frontend-pages 已知）未做精确断言。

## 给 test-contract-security 的消息

无。本轮未发现 `src/{api,mocks,stores,router,directives}` 内的 bug，未新建 MSG 文件。

## 文件清单

- 新增：`frontend/playwright.config.js`、`frontend/e2e/helpers.js`、`frontend/e2e/01-auth.spec.js` … `11-layout-memory.spec.js`、`frontend/e2e/README.md`、`frontend/e2e/screenshots/*.jpg`
- 修改：`frontend/package.json`（devDependency `@playwright/test`、脚本 `test:e2e*`）、`frontend/src/views/EvidenceCenter.vue`、`frontend/src/views/TerminalResponse.vue`
