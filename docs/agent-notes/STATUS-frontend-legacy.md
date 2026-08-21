# STATUS-frontend-legacy

更新：2026-08-21

## 已完成
- **services**
  - `services/scientificCompute.js` 重写为薄客户端（删 Pyodide / CDN）：`load()` 恒 true；`kMeansClustering` → `classifyAssets`；统计 / z-score / 拉普拉斯与高斯加噪 / L2 裁剪 / Top-k / 范数 / 预算组合均为纯 JS；新增 `randomGradients()` 供演示。
  - 删除 `services/aiReportGenerator.js`、`services/deepseekAdapter.js`；`DEEPSEEK_ADAPTER_STATE` 迁入 `services/dispatchTask.js`；删除 `normalizeDeepSeekResult`。
- **components/legacy**
  - `SourceBadge.vue`（live / cache / rule 徽章，三个页面复用）、`DataFlowAnimation.vue`（数据不出域五环节 CSS 动画）。
- **7 个旧页**（均接真实 API，保留原布局气质）
  | 页面 | 接入 |
  |---|---|
  | NetworkTopology | 顶部 `<TrustFlowBanner />`；节点取 `perspectiveStore.nodes`（GET /nodes）；卡片/列表显示 DID 短标识 + didStatus 徽章（active 绿 / frozen 黄 / revoked 红）；订阅 `node_status` 刷新图表与时间戳；抽屉展示 did / lastSeenAt / metrics / `getNode` 附加字段，趋势图用 `getNodeMetrics` 最近 12 小时 |
  | DataClassification | 可编辑字段表（dataType / fields / freq / volume / 勾选）→ `classifyAssets`；展示 level / score / reason / factors / cluster，clusterCenters 散点图；「一键登记为数据资产」→ `registerAsset`（sourceDid = 当前节点 did）→ 显示 id / hash / evidenceId 并写日志；改参数后旧结果失效需重跑 |
  | RiskAssessment | 滑块 / 下拉构造 features → `assessRisk`（改参 500ms 防抖自动评估 + 场景预设）；riskScore 仪表盘、level 标签、factors 表 + 权重柱图、suggestion；`riskHistory` 折线；统计 / z-score 本地纯 JS；high/critical 弹拦截横幅 |
  | PrivacyCompute | 创建表单（name / nodeIds / rounds / dp / topk / simulatePoison）→ `createFlTask` → `startFlTask`；订阅 `fl_progress` 增量 + 3s 轮询兜底；隐私预算环形仪表（超限变红）、loss/acc 曲线、Top-k 压缩率曲线、每轮 gradientHash / evidenceId 列表、anomaly 提示、取消、任务列表切换 `getFlTask`；底部纯 JS 单次加噪 / Top-k 演示 |
  | CloudAggregate | ① FL 任务状态卡（`getFlTask`：状态 / 轮次 / 节点表 / 曲线 / modelVersion）+ `listFlModels` + 发布按钮 `v-permission="'algo:execute'"` ② `dispatchStore.runTask`（create → run）→ actions 表 / qTable 柱图 / constraintsChecked.violations / totalReward / explanation + `explanationSource` 徽章；订阅 `dispatch_progress` 五阶段流转；任务列表 `fetchTasks` 可回看 ③ 「签名下发」`v-permission="'dispatch:issue'"` → `issueTask(signature)`；「模拟越权下发」任何角色可见 → 直接 `issueDispatchTask`，捕获 1003/1004 显示拦截横幅 ④ AI 面板 `aiAnalyze({scene:'dispatch', context: DQN 入参出参, question})` 展示 answer / reasoning / source / latencyMs + 历史；「生成分析报告」用真实返回拼 Markdown 下载 |
  | TerminalResponse | 从 `dispatchStore.fetchTasks()` 筛当前节点的 issued/acked 指令；DID 验签步骤条：signerDid → `getDidDocument` 取公钥 → `verifyDid`；「确认执行并回执」功率曲线动画 → `ackDispatchTask(id,{nodeId,actualPowerKw,status,startedAt,completedAt})`，回执存证显示；订阅 `dispatch_progress` |
  | AuditLog | ① `getAuditStats` 卡片 + byModule 饼 / byRisk 柱 / trend 折线 ② `listAuditLogs` 多条件分页检索（riskLevel 着色，evidenceId / hash 列，点行跳追踪）+ `exportAuditLogs` → `saveBlob` ③ `getAuditTrace`：summary 卡 + `el-timeline`，默认填最近 high 日志 traceId 并给出候选 ④ `listAlerts` / ack，R01~R05 中文映射，订阅 `audit_alert` 实时插入 + ElNotification ⑤ `getAuditReport` 各统计 + narrative + narrativeSource 徽章 ⑥ 「前端操作记录」Tab 保留 logStore 视图 |
- **测试**：`src/views/__smoke_legacy__/{_helper.js, 7×*.spec.js}`（mount + MSW + flushPromises，断言无 errorHandler 错误与关键标题）。

## 自测结果
- `npm run build`：零错误。
- `grep -rn "https\?://" src/`：无命中（`w3id.org/did/v1` 在 mock 中，已在白名单）。
- `npx vitest run`：68/68 全绿（qa 61 + 本方 7），`tests/no-external-url.spec.js` 通过。
- `VITE_USE_MOCK=true npx vite --port 5198`：`/` 与 `/cloud/topology` 返回 200，已杀掉。

## 越界说明
- `src/stores/dispatch.js` 改了一行 import（`deepseekAdapter.js` → `dispatchTask.js`），原因与 diff 见 `MSG-frontend-legacy-to-frontend-infra-001.md`。
- 开工时 `components/TrustFlowBanner.vue` 不存在，曾创建 10 行占位；frontend-pages 的正式版本已覆盖（当前为其版本）。

## 已知问题 / 说明
- TerminalResponse 的验签 `signature` 为演示派生值 `sig:sha256(signerDid|commandId)`：契约 issue 响应不回传原始签名字节，真实后端若要求原签名需由甲方在 task 详情中附带。
- 「模拟越权下发」对有权限角色（admin / grid）会真的下发成功（或 1006 已下发）；演示越权请用 vpp 登录。
- AI / 解释来源在 mock 下恒为 `cache`，联调 algo-service 后按实际 live/cache/rule 显示。
- FL 页 WS 断开时依赖 3s 轮询 `getFlTask`，曲线增长略有延迟。

## 阻塞
无
