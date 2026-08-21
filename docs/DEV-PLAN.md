# 能源可信数据空间平台 · 乙方开发总纲（多 Agent 协作版）

> 本文件是所有子 Agent 的共同依据。**先读 `contract/API-CONTRACT.md`，再读本文件，再动手。**
> `contract/` 为冻结区（已设为只读），任何 Agent 不得修改；`backend/` 是甲方目录，**不得创建**。

## 0. 工作区

```
energy-tds/                      ← 乙方全部成果（本目录）
├── docker-compose.yml           owner: deploy
├── .env.example                 owner: deploy
├── contract/                    只读（甲乙共同契约）
├── algo-service/                owner: algo      （algo-service/tests/ 归 qa）
├── frontend/                    owner: frontend-*（frontend/Dockerfile、frontend/nginx.conf 归 deploy；frontend/tests/ 归 qa）
├── deploy/                      owner: deploy
├── packaging/                   owner: deploy
├── qa/                          owner: qa（集成/契约一致性测试、检查脚本、测试报告）
├── docs/
│   ├── DEV-PLAN.md              本文件
│   ├── agent-notes/             Agent 间通信目录（见 §6）
│   └── legacy/                  原始需求文档的文本版，只读参考
└── .venv/                       Python 虚拟环境（已装 numpy fastapi uvicorn httpx pytest）
```

- Python：统一用 `/home/stu/yanxulong/Tzb/energy-tds/.venv/bin/python`（3.12），新增依赖请同时写进 `algo-service/requirements.txt` 并通知 qa。
- Node：`frontend/node_modules` 已安装（vue3/vite5/pinia/echarts/element-plus/axios/msw2/vitest/jsdom）。**不要删除重装**；需要新包时 `npm i <pkg>` 并在 agent-notes 里说明。
- 所有代码注释用中文。源码中**不得出现任何外网 URL**（唯一例外：`contract` 规定的 `https://w3id.org/did/v1` 字符串常量和 `.env.example` 里的 `DEEPSEEK_BASE_URL`）。

## 1. Agent 分工（互不重叠）

| Agent | 负责 | 不碰 |
|---|---|---|
| **algo** | `algo-service/` 全部源码、脚本、数据、模型、Dockerfile、requirements.txt | tests/ |
| **frontend-infra** | `frontend/src/{api,mocks,stores,directives,router,utils}`、`main.js`、`App.vue`、`components/`、`views/Login.vue`、`vite.config.js` | 其它 views |
| **frontend-pages**（第二波） | `views/{Identity,Assets,Permission,Evidence}Center.vue` 四个新页 + 首页流程图组件 | api/mocks/stores |
| **frontend-legacy**（第二波） | 7 个旧页改造、`services/scientificCompute.js` 重写、删 Pyodide、删 `aiReportGenerator.js`/`deepseekAdapter.js` 假实现 | api/mocks/stores |
| **deploy** | `docker-compose.yml`、`.env.example`、`deploy/`、`packaging/`、`frontend/Dockerfile`、`frontend/nginx.conf`、`frontend/.dockerignore`、`algo-service/.dockerignore` | 业务代码 |
| **qa（测试工程师）** | `algo-service/tests/`、`frontend/tests/`、`qa/`、缺陷单、验收报告 | 业务代码（只报不改；可改测试代码） |

冲突规则：**只在自己 owner 的路径下写文件**。发现别人目录有问题 → 写缺陷单（§6），不要直接改。

## 2. 算法服务（algo）要点

实现 `contract/API-CONTRACT.md` 第三部分全部接口，路径前缀 `/algo/v1`，**响应不包装**，错误 `{"error": "..."}`。

- `main.py`：FastAPI，端口 `ALGO_PORT`(默认 8100)，每个请求读取 `X-Trace-Id` 并回写到响应头；`GET /algo/v1/health`。
- `algorithms/fedavg.py`：4 节点 Non-IID 本地数据（`scripts/gen_datasets.py` → `data/node_datasets.npz`），NumPy MLP（8-16-1，负荷预测回归；acc 定义为相对误差 <10% 的样本占比，写进注释），按样本量加权平均。任务在后台线程异步跑，`GET /fl/jobs/{id}` 轮询，每轮 `gradientHash` = SHA256（contract 允许 SM3 或 SHA256）。每轮之间 `sleep` 0.8~1.2s 让前端看到曲线增长。
- `algorithms/dp.py`：L2 裁剪 + 高斯噪声 `N(0,(C·σ)²)`；σ 由 ε/δ/T 反推（简化矩会计 `ε = q·sqrt(T·log(1/δ))/σ`），每轮累计 `epsilonSpent`，超预算 → `anomaly={"type":"privacy_budget_exhausted",...}`。
- `algorithms/topk.py`：按 |g| 取前 k 比例，其余置零，残差缓存，`compressionRatio=(1-k/n)*100`。
- 梯度投毒检测：节点梯度与均值余弦相似度 < 阈值 → `anomaly={"type":"gradient_poisoning","nodeId":...}`；`POST /fl/train` 请求体若带 `"simulatePoison": "Node-C"`（可选扩展字段，甲不传则无影响）则该节点梯度翻转放大，用于演示。
- `algorithms/dqn.py` + `scripts/train_dqn.py`：环境状态 `[pv, load, soc, price, hour]` 归一化，动作 `[charge, idle, discharge]`，奖励 = 削峰收益 − 越限惩罚 − 电价成本；两层 MLP Q 网络 + 经验回放 + 目标网络，NumPy 手写反向传播；训练产物 `models/dqn.npz`。推理：对每个节点算 Q 值，约束校验 SOC 20~95%、单节点 ≤30kW，越限动作改为次优动作并记入 `constraintsChecked.violations`。`powerKw` 由 Q 值差与 SOC 余量计算得到。
- `algorithms/classifier.py`：特征 `[sensitivity, granularity, volume]` → k-means(k=3, 固定随机种子, NumPy) + 规则加权 → L1~L4，返回 `clusterCenters`。
- `algorithms/risk.py`：因子 查询频率/数据粒度/暴露字段/剩余预算 加权评分，`level` 按 contract 枚举 `low|medium|high|critical`，给出 ε 建议。
- `adapters/deepseek.py`：`live`(httpx，超时 `DEEPSEEK_TIMEOUT`，`DEEPSEEK_API_KEY` 为空直接跳过) → `cache`（`cache/deepseek_cache.json`，key=`sha256(scene + canonical_json(context) + question)`，另有按 scene 的通用兜底 key）→ `rule`（中文模板填空）。永远 200。`scripts/warm_cache.py` 预热。
- 场景 `audit` 的 `context` 会带审计报告统计数据，规则模板要能生成一段通顺的日报叙述（甲的 `/audit/report` 靠它）。
- `Dockerfile`：`python:3.11-slim`，先 `pip install -r requirements.txt`（numpy/fastapi/uvicorn/httpx 均有 aarch64 wheel），再拷源码；镜像内必须含 `models/dqn.npz` 与 `data/node_datasets.npz`（构建前由 build.sh 保证存在，Dockerfile 里也兜底：若不存在则运行生成脚本）。

## 3. 前端基础设施（frontend-infra）要点 —— 其它前端 Agent 按此调用

### 3.1 `src/api/request.js`
- axios 实例 `baseURL = import.meta.env.VITE_API_BASE || '/api/v1'`，请求头注入 `Authorization: Bearer <token>`（从 `stores/user` 取）与 `X-Trace-Id`（前端生成 `tr-YYYYMMDD-<8hex>`，便于联调追踪）。
- 响应拦截：`code===0` → **resolve `data` 字段**；`code 1002` → 清登录态跳 `/login`；`code 1003/1004` → `ElMessage.error` 越权/验签失败提示并 reject；其它非 0 → toast + reject。**reject 的 error 对象上附带 `error.code`、`error.message`、`error.traceId`。**
- 导出：`export default request` 以及 `export function download(url, params)`（CSV 文件流，返回 Blob）。

### 3.2 API 模块导出签名（**固定，页面 Agent 直接按这些名字调用**）

```js
// auth.js
login({username,password})  logout()  getMe()
listUsers(params)  createUser(data)  updateUser(id,data)  deleteUser(id)
// did.js
registerDid(data)  listDids(params)  getDidDocument(did)  changeDidStatus(did,{action,reason})
rotateDidKey(did)  verifyDid({did,message,signature})  resolveDids(dids)
// key.js
listKeys(params)  createKey(data)  freezeKey(id)  revokeKey(id)  keyHistory(id)
// asset.js
registerAsset(data)  listAssets(params)  getAsset(id)  getAssetLineage(id)
classifyAssets({records})  getAssetStats()
// permission.js
listRoles()  createRole(data)  updateRole(code,data)  getPermissionMatrix()
applyPermission(data)  listApplications(params)  approveApplication(id,data)  rejectApplication(id,data)
listGrants(params)  revokeGrant(id)  checkPermission(data)
// evidence.js
writeEvidence(data)  listEvidence(params)  getEvidence(id)  verifyEvidence({evidenceId,payload})
getChainStatus()  traceEvidence(traceId)  tamperEvidence({evidenceId,newValue})  getCertificate(id)
// audit.js
listAuditLogs(params)  getAuditTrace(traceId)  listAlerts(params)  ackAlert(id)
getAuditReport({period,date})  getAuditStats()  exportAuditLogs(params)   // 返回 Blob
// node.js
listNodes(params)  getNode(id)  getNodeMetrics(id,params)  nodeOnline(id,data)
// fl.js
createFlTask(data)  listFlTasks(params)  getFlTask(id)  startFlTask(id)  cancelFlTask(id)
getFlRounds(id)  listFlModels(params)  publishFlModel(version)
// dispatch.js
createDispatchTask(data)  listDispatchTasks(params)  getDispatchTask(id)
runDispatchTask(id)  issueDispatchTask(id,{signature})  ackDispatchTask(id,data)
// ai.js
aiAnalyze({scene,context,question})  aiHistory(params)
// risk.js
assessRisk({nodeId,features})  riskHistory(params)
```

全部返回 `Promise<data>`（已解包）。

### 3.3 `src/api/ws.js`
- `createWsClient()` 单例：`connect(token)`、`disconnect()`、`on(type, handler)` → 返回取消函数、`off`、`send(obj)`；30s 心跳 `{"type":"ping"}`；指数退避重连（1s→30s）；`VITE_USE_MOCK=true` 时内部走 `mocks/wsMock.js` 的模拟推送（定时 `node_status`，FL/调度启动后推 `fl_progress`/`dispatch_progress`，越权时推 `audit_alert`，每条写操作推 `log`/`evidence_written`）。
- 导出 `export const wsClient = createWsClient()` 与 `export const WS_TYPES = {...}`。

### 3.4 stores
- `stores/user.js`：`token user roles permissions isLoggedIn`；`login(form)`、`logout()`、`fetchMe()`、`hasPermission(perm)`、`hasRole(role)`；token 存 `localStorage('energy-tds-token')`。
- `stores/logs.js`：保留原 `addLog` 等签名；新增 `attachWs()` 把 `log`/`audit_alert`/`evidence_written` 消息写入日志流；`alerts` 列表与 `unackedAlertCount`。
- `stores/perspective.js`：`nodes` 改为 `ref([])`，新增 `async fetchNodes()`，保持 `currentNodeData/currentNodeInfo/getNodeById/buildEdgeReport/cloudMenus/edgeMenus/globalMenus/currentMenus/togglePerspective/setCurrentNode` 不变；node 对象结构 `{id,name,status,model,did,didStatus,metrics:{pvOutput,storageOutput,load,soc},lastSeenAt, data:{...metrics,model}}`（**`data` 为兼容旧页面的别名**）。`cloudMenus` 增加四个中心：`/identity /assets /permission /evidence`（放 `centerMenus`，两种视角都显示）。
- `stores/dispatch.js`：改为调后端 `dispatch.js`；保留 `tasks activeTask latestTask latestDispatchableTask` 与 `initializeTask/dispatchTask/completeTask/getLatestTaskForNode` 的语义，但实现换成 `createDispatchTask → runDispatchTask → issueDispatchTask → ackDispatchTask`；新增 `runTask()`、`issueTask(signature)`、`ackTask()`、`fetchTasks()`。

### 3.5 路由与权限
- 路由：`/login`（无布局）、其余在 `AppLayout` 下：原 7 条 + `/identity /assets /permission /evidence`。`meta.requiresAuth=true`、`meta.permission='asset:read'` 等。全局守卫：未登录跳 `/login?redirect=`；无权限跳首页并 toast。
- 指令 `v-permission="'dispatch:issue'"`：无权限时 `el.remove()`（或 `disabled`，传 `{ value, modifiers: {disable} }`）。
- `AppHeader` 增加：当前用户名/角色、告警铃铛（未确认数）、退出按钮；标题改「能源可信数据空间平台」。

### 3.6 MSW（`src/mocks/`）
- `browser.js`（`setupWorker`）、`handlers/*.js` 按模块拆、`db.js`（内存数据库 + 种子：6 账号、4 节点含 DID、80 条资产、存证链、审计日志、30 天指标、角色矩阵照 `contract/DB-SCHEMA.md`）、`chain.js`（本地哈希链：SHA-256 `block_hash = H(prev + payloadHash + ts)`，`tamper` 真的改 payload 并让 `verify`/`chain/status` 检测到断裂点）、`wsMock.js`。
- 统一包装 `{code,message,data,traceId}`；权限矩阵真的生效：`vpp` 调 `dispatch/.../issue` 返回 `1003` 并推 `audit_alert`(R01) + 写 high 审计日志。
- FL 训练在 mock 里也要**真算**（用 JS 复刻简化版 FedAvg：小型 MLP + 高斯噪声 + Top-k），保证兜底演示曲线也随参数变。
- `main.js`：`if (import.meta.env.VITE_USE_MOCK === 'true') await (await import('./mocks/browser')).worker.start({onUnhandledRequest:'bypass'})`。`public/mockServiceWorker.js` 由 `npx msw init public/ --save` 生成。
- 测试桩：`src/mocks/node.js` 导出 `setupServer(...handlers)`（供 vitest 用）。

## 4. 页面要求摘录（frontend-pages / frontend-legacy）

见 `docs/legacy/提示词-乙-算法前端交付.md` §2.2、§2.3 与 `contract/API-CONTRACT.md` 第二部分。视觉风格沿用 `styles/global.css` 的深色科技风（`--color-primary #00B4D8`），新页面用 Element Plus 表格/表单/对话框 + ECharts。每个关键操作都 `logStore.addLog(...)`。

- 首页 `/cloud/topology` 顶部新增 `components/TrustFlowBanner.vue`：六环节横向流程图（身份接入→数据登记→权限授权→隐私计算→智能调度→存证审计），计数分别来自 `listDids({size:1}).total`、`getAssetStats().total`、`listGrants().total`、`listFlTasks().total`、`listDispatchTasks().total`、`getChainStatus().height`，点击跳转。
- 存证中心必须有「篡改演示」按钮 → 调 `tamperEvidence` → 自动调 `verifyEvidence` + `getChainStatus`，标红断裂点。
- 审计中心：traceId 输入 → `getAuditTrace` 时间轴（`el-timeline`）；告警列表 + ack；`getAuditReport` 展示 `narrative` 与 `narrativeSource` 徽章；导出 CSV。
- 旧页 `scientificCompute.js` 改为薄客户端：保留导出对象 `scientificCompute`，方法 `load()` 恒 resolve(true)、`kMeansClustering(features,k)` → 调 `classifyAssets`、`calculateStatistics/normalizeZScore` 改为纯 JS 本地实现、`applyDifferentialPrivacy/topkGradientSparsity/calculateGradientNorm` 改为纯 JS 本地实现（演示用，真正训练走 FL API）。

## 5. 部署（deploy）要点

见 `docs/legacy/安装包规范.md` 与 `提示词-乙` 工作三/四。关键：
- compose 五服务、`./backend/sql:/docker-entrypoint-initdb.d:ro` 挂载（目录不存在也要能 `docker compose config` 通过，安装包里由 `sql/` 提供）、healthcheck、内存上限、环境变量名严格照 contract 第四部分。
- `frontend/Dockerfile` 多阶段：`FROM --platform=$BUILDPLATFORM node:20-alpine AS builder` → `nginx:alpine`；nginx `/api/` → `backend:8000`、`/ws` → `backend:8000` 带 Upgrade 头、SPA `try_files`。
- `packaging/build.sh --arch amd64|arm64|all` 十步、`install.sh` 九步（含 armv7l 拒绝、随机密钥、端口占用列进程、180s 健康轮询）、`uninstall.sh --purge`、systemd 单元、kiosk 单元、mysql 两份 cnf。脚本要过 `bash -n` 与 shellcheck（若可用）。
- 由于本机没有 `backend/`，build.sh 里对 backend 的步骤要在缺目录时**明确报错退出**（规范要求三目录齐全），但提供 `--skip-backend` 开发选项以便乙方独立试打包。

## 6. Agent 间通信协议

目录 `docs/agent-notes/`：
- `STATUS-<agent>.md`：**只由该 agent 自己写**，记录：已完成 / 进行中 / 阻塞 / 对外暴露的接口或约定变化。每完成一个里程碑就更新。
- `MSG-<from>-to-<to>-<序号>.md`：跨 agent 请求/通知（例如 qa 给 algo 的缺陷单、algo 通知 frontend 响应字段的细节）。接收方处理后在文件末尾追加 `## 回复` 段落。开工前与每个里程碑后都 `ls docs/agent-notes/` 看看有没有给自己的消息。
- 缺陷单格式：标题、严重级别（P0 阻塞 / P1 功能错误 / P2 体验）、复现步骤、期望 vs 实际、涉及文件:行号。
- 如果环境提供了 `SendMessage` 工具也可以直接发，但**文件是权威记录**，发消息的同时必须落文件。
- 主 Agent（总控）会在每一波结束后汇总各方 STATUS 与 MSG，并把需要跨方修改的事项转发。

## 7. 完成定义（DoD）

- algo：`pytest algo-service/tests` 全绿；`uvicorn main:app` 起得来；`curl /algo/v1/health` 返回 `{"status":"ok",...}`；DQN checkpoint 存在且推理 <200ms；FL 10 轮 loss 单调趋势下降（允许抖动）。
- frontend：`npm run build` 零错误；`VITE_USE_MOCK=true npm run dev` 下 12 条验收链路（contract 第六部分）能在浏览器里走通；`grep -rn "http://\|https://" src/` 仅剩允许项；`npm test` 全绿。
- deploy：`docker compose config` 通过；`docker build` algo-service 与 frontend 镜像成功（amd64）；`bash -n` 全部脚本；安装包目录结构与规范 §三一致。
- qa：`qa/REPORT.md` 验收报告，逐条对照 contract 第六部分与本文件 DoD，附测试命令与结果。
