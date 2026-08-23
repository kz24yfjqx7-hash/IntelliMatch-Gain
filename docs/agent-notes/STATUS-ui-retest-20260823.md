# STATUS · ui-retest · 2026-08-23（页面层复跑，后端 8000 带 R03 修复，pid 2934506 未动）

串行跑在真后端 `127.0.0.1:8000` + 前端 5199（`VITE_USE_MOCK=false`）上，UTC 03:56–04:08（北京 11:56–12:08）。
接口层套件此时已跑完，页面层是唯一在打后端的客户端。日志与临时文件：
`/tmp/claude-1002/-home-stu-yanxulong-Tzb/65f0c8ee-142f-469a-945b-159bbb6cf897/scratchpad/ui-retest/`（`ui-run1.log`、`e2e-run1.log`）。
未 kill/重启任何进程；未执行 DDL；未改 backend/、qa/api-tests/。

## 一、复跑结果

| 套件 | 结果 | 标识 | 失败项 |
|---|---|---|---|
| `qa/func-tests/ui_func_test.mjs`（真后端 5199） | **16/16**（第一遍 15/16） | tag `test-08230356`，`qa/func-tests/results/ui-results.json`，截图 `docs/test-evidence/*.jpg` 已全部刷新 | 无。TC-UI-07 证据：`AI 来源=DeepSeek 实时; 任务=dp-000075; 下发=true; 指令=cmd-000075 → Node-A`；TC-UI-09 篡改 ev-002488 已自行还原 |
| Playwright E2E 真后端（`E2E_BASE_URL=http://127.0.0.1:5199`） | **28/28**（第一遍 27/28），3.1 min | `e2e-run1.log` | 无 |
| `09-evidence.spec.js` 单跑 | **1/1**（6.5 s） | | 连同全量里的一次共 2/2，稳定 |
| `cd frontend && npx vitest run` | **137/137** | 改 `EvidenceCenter.vue` 之后跑 | |
| 链状态 | `restore-all` → restoredCount 0（用例已自行还原）；`GET /evidence/chain/status` **intact=true**，height **2550**，brokenAt=null | | |

## 二、两个失败项定性

### 1. TC-UI-07（云端聚合 → 签名下发 → 终端回执）——**用例脆弱**（非前端缺陷、非后端缺陷；在接口层套件先跑过的环境里是稳定复现，不是偶发）

根因（按第一遍后端日志 + 截图 mtime 还原的时间线，UTC）：
- `CloudAggregate.vue` 挂载时默认选中「最近一条已生成策略的任务」（`onMounted` → `viewRemote(latest)`），接口层套件会留下一批 run 完但未下发的任务（当时是 dp-000065，本轮复跑前是 dp-000073），所以页面一打开「签名下发」就已可点。
- 用例点「生成调度策略」后的等待是 *「签名下发按钮可用」*，被旧任务立刻满足：03:08:17.856 用例创建 dp-000066 并 run（耗时 2.7 s，03:08:20.5 才完成），而 03:08:18.504 用例已经把**旧任务 dp-000065** 下发出去了（`POST /dispatch/tasks/dp-000065/issue` 200，trace 是 dp-000065 自己的 `tr-…-0e090dd1`）。截图 17/18/19 的 mtime 03:08:17/18/20 也印证"没等新任务"。
- 用例的"下发成功"判定是 `body.innerText` 匹配 `/已下发|issued|下发成功/`——任务列表里任何历史「已下发」都能匹配，所以证据写成 `下发=true; 提示=空`（ElMessage 3 秒自关，本来就抓不到）。
- 到 `/edge/response`：终端页只展示**当前节点**（默认 Node-A）的指令；Node-A 已有一条接口层留下的 **已回执** 任务 dp-000064（cmd-000064，03:06:06 被 api 套件 ack），`nodeTasks` 非空 → 不出现「切换到该节点查看」按钮（该按钮按设计只在当前节点无指令时出现），`nodeTasks[0]` 就是 dp-000064 → 状态片显示「已回执」；而 dp-000065 的目标不含 Node-A。于是 10 s 轮询 + `issuedChip.waitFor(15000)` 超时，03:08:46 失败——与截图里 cmd-000064「已执行/已回执」完全一致。
- 排除后端：回执接口 `POST /dispatch/tasks/*/ack` 全日志 `cost=` 最大 66 ms；下发 `issue` 20–270 ms；无 5xx。排除"等已消失的提示"：失败点不在 ElMessage 上。

处理（只改用例，`qa/func-tests/ui_func_test.mjs` TC-UI-07）：
- 点「生成调度策略」前先 `page.waitForResponse(POST …/dispatch/tasks/{id}/run)`，取到**本次新建任务 id**，`waitForFunction` 等页面文本出现该 id 后再等「签名下发」可用。
- 下发成功改等稳定信号 `.issue-result`（「✅ 指令 cmd-xxx 已下发至 Node-X …」，常驻 DOM，不是 3 秒自关的 ElMessage），并从中解析 commandId 与目标节点。
- 终端页先在侧栏 `.node-option` 点目标节点（不再依赖只在"无指令"时才出现的切换按钮），再确认 `.command-panel` 含本次 commandId（不含则从指令下拉里选），然后才等「已下发」状态片。其余步骤（验签、回执、回执面板断言）不变。
- 复跑证据：`任务=dp-000075; 指令=cmd-000075 → Node-A`，新任务被正确下发并回执。

### 2. E2E `09-evidence`（`.el-table__row.tampered-row` 15 s 内未出现）——**前端缺陷**（已修，`frontend/src/views/EvidenceCenter.vue`）

根因：
- `submitTamper()` 篡改后确实按 id 取详情把被篡改记录钉到了 `list.items` 表首（昨天加的逻辑本身是对的）。
- 但篡改与校验本身会写审计存证（第一遍 error-context 里表首第一行就是 `ev-002247 · evidence:tamper`），后端通过 WS 推 `evidence_written`，`onEvidenceWritten()` 节流 1.5 s 后调用 `load()` 刷新第一页——`load()` 直接 `list.items = data.items`，把钉上的红行刷掉了。被篡改的 ev-002223 在高度 2223，第一页是 2247…2238，于是 15 s 内再也没有 `.tampered-row`（区块条 `.block.tampered` 有 pinned 逻辑所以仍标红，和截图一致）。
- `isTampered` 的字段判定（`tampered` / `chain.tamperedIds`）与后端一致，`getEvidence` 也没失败；不是字段对不上，是**时序上被 WS 刷新覆盖**。第一遍失败与否取决于 WS 帧到达与 `expect` 的相对时机，所以表现为"偶发"，但机制是确定的。

修法：
- 把"钉表首"下沉到 `load()` 里的新函数 `pinTampered()`：第一页加载后，对 `chain.tamperedIds ∪ localTamperedIds` 中不在本页的 id 按 id 取详情（优先用篡改下拉的缓存）钉到表首，带 `tampered: true, pinned: true`。这样 WS 触发的刷新、手动「刷新」、篡改后的刷新都保持红行。
- `submitTamper()` 改为 `query.page = 1` 后 `load()`，删掉原来只执行一次的钉表逻辑（避免重复钉）。
- vitest 137/137 不回归；E2E 09 全量 + 单跑 2/2 通过；TC-UI-09 也通过。

## 三、改动文件
- `qa/func-tests/ui_func_test.mjs`：TC-UI-07 步骤（见上，只收紧等待信号，没有放宽任何断言；新增断言"下发结果须含 commandId 与目标节点"）。
- `frontend/src/views/EvidenceCenter.vue`：新增 `pinTampered()`，`load()` 末尾调用；`submitTamper()` 简化。
- `docs/agent-notes/STATUS-ui-retest-20260823.md`（本文件）。
- 副产物：`docs/test-evidence/*.jpg` 被套件刷新（`TC-UI-07-fail.jpg` 是第一遍的旧文件，本轮无失败截图生成），`qa/func-tests/results/ui-results.json`。

## 四、后端缺陷
**无新发现。** 页面层两个失败项一个是用例等待信号不稳（TC-UI-07），一个是前端 WS 刷新覆盖钉表（E2E 09）。
