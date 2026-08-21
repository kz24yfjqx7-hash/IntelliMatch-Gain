# 接口契约 v1.0（冻结）

能源可信数据空间平台。本文件定义所有跨人员边界的接口。任何一方不得单方面修改。

---

## 第一部分：通用约定

### 1.1 统一响应包装

**所有** `/api/v1/**` 接口（包括错误）返回以下结构，HTTP 状态码始终与 `code` 语义一致：

```json
{
  "code": 0,
  "message": "ok",
  "data": { },
  "traceId": "tr-20260817-8f3a2b1c"
}
```

| code | HTTP | 含义 |
|---|---|---|
| 0 | 200 | 成功 |
| 1001 | 400 | 参数错误 |
| 1002 | 401 | 未登录 / Token 失效 |
| 1003 | 403 | 无权限（RBAC 拒绝） |
| 1004 | 403 | DID 校验失败 / 签名无效 |
| 1005 | 404 | 资源不存在 |
| 1006 | 409 | 状态冲突（如重复注册、任务已在运行） |
| 1007 | 429 | 触发风控限流 |
| 2001 | 502 | 算法服务不可用 |
| 2002 | 502 | 链服务不可用 |
| 5000 | 500 | 服务器内部错误 |

失败示例：

```json
{"code":1003,"message":"角色 energy_subject 无 dispatch:issue 权限","data":null,"traceId":"tr-..."}
```

### 1.2 traceId

- 由 backend 在每个请求入口生成，格式 `tr-YYYYMMDD-<8位hex>`。
- 客户端可通过请求头 `X-Trace-Id` 指定，backend 沿用。
- backend 调用 algo-service 时**必须**透传 `X-Trace-Id` 请求头。
- 该请求产生的所有审计日志、存证记录共用同一 traceId，这是"任务级全流程追踪"的主键。

### 1.3 认证

- 登录后所有请求携带 `Authorization: Bearer <token>`。
- token 为 JWT（HS256），payload 含 `sub`(userId)、`username`、`roles`(数组)、`did`、`exp`。
- 有效期 8 小时。密钥来自环境变量 `JWT_SECRET`。
- WebSocket 通过 query 参数传递：`ws://host/ws?token=<token>`。

### 1.4 分页

请求参数：`page`（从 1 开始，默认 1）、`size`（默认 20，最大 200）。

分页响应的 `data` 固定为：

```json
{"items":[], "total":0, "page":1, "size":20}
```

### 1.5 时间格式

所有时间字段为 ISO 8601 带时区字符串：`2026-08-17T14:23:05+08:00`。

### 1.6 枚举值（全局统一，前后端必须一致）

```
角色 roleCode:        sys_admin | grid_dispatcher | vpp_operator | energy_subject | regulator | edge_node
DID 主体类型:          user | device | org | edge
DID 状态:             active | frozen | revoked
数据类型 dataType:     pv | wind | storage | load | dispatch
敏感等级 level:        L1 | L2 | L3 | L4      （L1公开 / L2内部 / L3敏感 / L4核心）
资源类型 resourceType: asset | model | dispatch | evidence | algo
操作 action:          read | write | execute | issue | export
权限申请状态:          pending | approved | rejected | expired
存证类别 category:     data | identity | permission | audit | algo
风险等级 riskLevel:    low | medium | high | critical
任务状态 taskStatus:   created | running | success | failed | cancelled
节点状态 nodeStatus:   online | warning | offline
```

---

## 第二部分：前端 ⇄ 后端（`http://backend:8000/api/v1`）

> 全部由**甲**实现，**乙**调用。

### 2.1 认证与用户（3.1）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | 登录 |
| POST | `/auth/logout` | 登出 |
| GET | `/auth/me` | 当前用户信息 |
| GET | `/users` | 用户列表（分页，需 sys_admin） |
| POST | `/users` | 新建用户 |
| PUT | `/users/{id}` | 修改用户 |
| DELETE | `/users/{id}` | 删除用户 |

```
POST /auth/login
请求  {"username":"admin","password":"admin123"}
响应  data: {
        "token":"eyJ...",
        "expiresIn":28800,
        "user":{"id":1,"username":"admin","realName":"系统管理员",
                "roles":["sys_admin"],"did":"did:vpp:user:0x8f3a...","orgName":"平台运营方"}
      }
```

```
GET /auth/me
响应  data: {"id":1,"username":"admin","realName":"系统管理员","roles":["sys_admin"],
             "did":"did:vpp:user:0x8f3a...","permissions":["asset:read","dispatch:issue", ...]}
```

`permissions` 是扁平字符串数组，格式 `<resourceType>:<action>`。**前端按钮级权限控制直接用它做判断。**

### 2.2 DID 身份管理（3.2）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/did/register` | 签发 DID |
| GET | `/did` | DID 列表（分页；筛选 `subjectType`、`status`、`keyword`） |
| GET | `/did/{did}` | DID 文档 |
| POST | `/did/{did}/status` | 冻结 / 解冻 / 注销 |
| POST | `/did/{did}/rotate-key` | 密钥轮换 |
| POST | `/did/verify` | 验签 |
| POST | `/did/resolve` | 批量解析 |

DID 标识格式：`did:vpp:<subjectType>:<SM3(publicKey) 前 32 位 hex>`
例：`did:vpp:device:0x8f3a2b1c9d4e5f60718293a4b5c6d7e8`

```
POST /did/register
请求  {"subjectType":"device","subjectName":"光伏逆变器-A01",
       "orgName":"XX园区","metadata":{"model":"VPP-2000","location":"A区"}}
响应  data: {
        "did":"did:vpp:device:0x8f3a...",
        "didDocument":{
          "@context":"https://w3id.org/did/v1",
          "id":"did:vpp:device:0x8f3a...",
          "controller":"did:vpp:org:0x1122...",
          "verificationMethod":[{"id":"...#key-1","type":"SM2VerificationKey2023",
                                 "publicKeyHex":"04a1b2..."}],
          "created":"2026-08-17T14:23:05+08:00"
        },
        "publicKey":"04a1b2...",
        "privateKey":"7f8e9d...",     // 仅本次返回，不入库明文
        "chainTxId":"blk-000123-0",
        "evidenceId":"ev-000456"
      }
```

```
POST /did/verify
请求  {"did":"did:vpp:device:0x8f3a...","message":"<原文>","signature":"<SM2签名hex>"}
响应  data: {"valid":true,"subjectType":"device","status":"active","reason":null}
```

```
POST /did/{did}/status
请求  {"action":"freeze","reason":"设备离线超 24 小时"}      // freeze | unfreeze | revoke
响应  data: {"did":"...","status":"frozen","evidenceId":"ev-..."}
```

### 2.3 密钥管理（3.3）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/keys` | 密钥列表（分页；筛选 `did`、`status`） |
| POST | `/keys` | 生成并绑定密钥 |
| POST | `/keys/{id}/freeze` | 冻结 |
| POST | `/keys/{id}/revoke` | 注销 |
| GET | `/keys/{id}/history` | 轮换历史 |

```
GET /keys?did=did:vpp:device:0x8f3a...
响应  data: {"items":[{"id":12,"did":"...","algorithm":"SM2","publicKey":"04a1b2...",
                       "status":"active","version":2,"boundAt":"...","expireAt":"..."}],
             "total":1,"page":1,"size":20}
```

`algorithm` 取值：`SM2`（默认）| `ECC` | `RSA`。

### 2.4 能源数据资产（3.4）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/assets` | 数据登记 |
| GET | `/assets` | 资产列表（分页；筛选 `dataType`、`level`、`sourceDid`、`keyword`） |
| GET | `/assets/{id}` | 资产详情 |
| GET | `/assets/{id}/lineage` | 数据溯源链 |
| POST | `/assets/classify` | 自动分类分级（代理算法服务） |
| GET | `/assets/stats` | 分级统计（供前端图表） |

```
POST /assets
请求  {"name":"节点A光伏出力-20260817","dataType":"pv","sourceDid":"did:vpp:device:0x8f3a...",
       "level":"L2","payload":{"pvOutput":45.3,"ts":"2026-08-17T14:00:00+08:00"},
       "description":"分钟级采集"}
响应  data: {"id":1001,"hash":"sm3:9a8b7c...","level":"L2","chainTxId":"blk-000124-0",
             "evidenceId":"ev-000457","authStatus":"unauthorized","createdAt":"..."}
```

原始 `payload` 存 MySQL，**链上只存 SM3 摘要**。

```
GET /assets/{id}/lineage
响应  data: {"assetId":1001,"traceId":"tr-...",
             "chain":[
               {"stage":"register","at":"...","actorDid":"...","evidenceId":"ev-457","hash":"sm3:9a8b..."},
               {"stage":"authorize","at":"...","actorDid":"...","evidenceId":"ev-460"},
               {"stage":"access","at":"...","actorDid":"...","evidenceId":"ev-472"},
               {"stage":"compute","at":"...","actorDid":"...","evidenceId":"ev-488"}
             ]}
```

```
GET /assets/stats
响应  data: {"byLevel":[{"level":"L1","count":12}, ...],
             "byType":[{"dataType":"pv","count":30}, ...],
             "total":85,"authorized":40,"onChain":85}
```

### 2.5 权限控制中心（3.5）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/roles` | 角色列表 |
| POST | `/roles` | 新建自定义角色 |
| PUT | `/roles/{code}` | 修改角色权限 |
| GET | `/permissions/matrix` | 资源-操作权限矩阵 |
| POST | `/permissions/apply` | 提交权限申请 |
| GET | `/permissions/applications` | 申请列表（分页；筛选 `status`、`applicantDid`） |
| POST | `/permissions/applications/{id}/approve` | 审批通过 |
| POST | `/permissions/applications/{id}/reject` | 驳回 |
| GET | `/permissions/grants` | 已授权列表（筛选 `did`） |
| POST | `/permissions/grants/{id}/revoke` | 回收授权 |
| POST | `/permissions/check` | 权限校验（内部 + 前端预判） |

```
POST /permissions/apply
请求  {"resourceType":"asset","resourceId":"1001","action":"read",
       "reason":"联合建模需要读取节点A光伏数据","expireAt":"2026-09-17T00:00:00+08:00"}
响应  data: {"id":55,"status":"pending","applicantDid":"...","createdAt":"...","evidenceId":"ev-..."}
```

```
POST /permissions/check
请求  {"did":"did:vpp:user:0x...","resourceType":"dispatch","resourceId":"task-9","action":"issue"}
响应  data: {"allowed":false,"reason":"角色 vpp_operator 不具备 dispatch:issue 权限",
             "matchedRule":null,"level":"L3"}
```

```
GET /permissions/matrix
响应  data: {"resources":["asset","model","dispatch","evidence","algo"],
             "actions":["read","write","execute","issue","export"],
             "roles":[{"code":"sys_admin","name":"系统管理员",
                       "grants":{"asset":["read","write","export"],"dispatch":["read","issue"], ...}}]}
```

### 2.6 区块链可信存证（3.6）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/evidence` | 写入存证 |
| GET | `/evidence` | 存证检索（分页；筛选 `category`、`did`、`dataType`、`from`、`to`） |
| GET | `/evidence/{id}` | 存证详情 |
| POST | `/evidence/verify` | 完整性校验 |
| GET | `/evidence/chain/status` | 链状态 |
| GET | `/evidence/trace/{traceId}` | 按 traceId 查完整业务链路 |
| POST | `/evidence/demo/tamper` | **演示专用**：篡改一条存证 |
| GET | `/evidence/{id}/certificate` | 导出存证凭证（JSON） |

```
POST /evidence
请求  {"category":"data","refId":"1001","payload":{...},"actorDid":"did:vpp:..."}
响应  data: {"evidenceId":"ev-000457","hash":"sm3:9a8b7c...","blockHeight":124,
             "txId":"blk-000124-0","prevHash":"sm3:1122...","timestamp":"..."}
```

```
POST /evidence/verify
请求  {"evidenceId":"ev-000457","payload":{...}}     // payload 可省略，省略时用库内数据重算
响应  data: {"intact":false,"localHash":"sm3:aaaa...","chainHash":"sm3:9a8b...",
             "tamperedAt":"2026-08-17T15:02:00+08:00",
             "message":"本地数据与链上摘要不一致，数据已被篡改"}
```

```
GET /evidence/chain/status
响应  data: {"height":128,"lastHash":"sm3:ffee...","intact":true,"brokenAt":null,
             "totalRecords":128,"byCategory":{"data":60,"identity":22,"permission":18,"audit":25,"algo":3}}
```

```
POST /evidence/demo/tamper
请求  {"evidenceId":"ev-000457","newValue":{"pvOutput":999.9}}
响应  data: {"evidenceId":"ev-000457","tampered":true,
             "hint":"请调用 /evidence/verify 或 /evidence/chain/status 查看校验结果"}
说明  仅 sys_admin 可调用，用于答辩现场演示防篡改能力。
```

### 2.7 安全审计中心（3.7）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/audit/logs` | 日志检索（分页；筛选 `traceId`、`actorDid`、`action`、`riskLevel`、`from`、`to`、`keyword`） |
| GET | `/audit/trace/{traceId}` | 任务级全链路追踪 |
| GET | `/audit/alerts` | 告警列表（筛选 `status`：`open`/`acked`） |
| POST | `/audit/alerts/{id}/ack` | 确认告警 |
| GET | `/audit/report` | 审计报告（`period`=`day`/`week`/`month`，`date`=YYYY-MM-DD） |
| GET | `/audit/stats` | 审计看板统计 |
| GET | `/audit/logs/export` | 导出 CSV（返回文件流，非 JSON 包装） |

```
GET /audit/logs?riskLevel=high&page=1&size=20
响应  data: {"items":[{
        "id":9001,"traceId":"tr-20260817-8f3a2b1c","actorDid":"did:vpp:user:0x...",
        "actorName":"张三","action":"dispatch:issue","resourceType":"dispatch","resourceId":"task-9",
        "result":"denied","riskLevel":"high","module":"permission",
        "detail":"越权尝试下发调度指令","ip":"192.168.1.42",
        "at":"2026-08-17T14:23:05+08:00","evidenceId":"ev-000501","hash":"sm3:..."
      }],"total":3,"page":1,"size":20}
```

```
GET /audit/trace/{traceId}
响应  data: {"traceId":"tr-...","summary":{"startAt":"...","endAt":"...","durationMs":4210,
                                          "actorDid":"...","result":"success","riskLevel":"low"},
             "steps":[{"seq":1,"module":"auth","action":"login","at":"...","result":"success"},
                      {"seq":2,"module":"did","action":"did:verify","at":"...","result":"success"},
                      {"seq":3,"module":"permission","action":"permission:check","at":"...","result":"allowed"},
                      {"seq":4,"module":"algo","action":"fl:train","at":"...","result":"success"},
                      {"seq":5,"module":"evidence","action":"evidence:write","at":"...","evidenceId":"ev-..."}]}
```

五类风险规则（甲实现，规则码固定，前端按码展示）：

| ruleCode | 名称 | 触发条件 |
|---|---|---|
| `R01_UNAUTHORIZED` | 越权访问 | 权限校验拒绝累计 ≥ 3 次 / 5 分钟 |
| `R02_ABNORMAL_DID` | 异常 DID 登录 | 已冻结/注销 DID 尝试接入，或异地 IP |
| `R03_PERM_CHURN` | 高频权限变更 | 同一主体权限变更 ≥ 5 次 / 10 分钟 |
| `R04_BULK_EXPORT` | 批量数据导出 | 单次导出 ≥ 1000 条或 10 分钟内导出 ≥ 3 次 |
| `R05_SUSPICIOUS_GRAD` | 可疑梯度上传 | 算法服务上报梯度异常 / 隐私预算超限 |

```
GET /audit/stats
响应  data: {"todayLogs":420,"highRiskLogs":6,"openAlerts":2,"onChainLogs":420,
             "byModule":[{"module":"did","count":80}, ...],
             "byRisk":[{"riskLevel":"low","count":390},{"riskLevel":"high","count":6}, ...],
             "trend":[{"date":"2026-08-11","total":380,"high":2}, ...]}
```

```
GET /audit/report?period=day&date=2026-08-17
响应  data: {"period":"day","date":"2026-08-17",
             "identityOps":{"register":5,"freeze":1,"revoke":0,"rotate":2},
             "permissionOps":{"applied":8,"approved":6,"rejected":2,"revoked":1},
             "evidence":{"total":128,"byCategory":{...}},
             "riskEvents":[{"ruleCode":"R01_UNAUTHORIZED","count":3,"level":"high"}],
             "narrative":"今日平台共记录 420 条审计日志……",   // 由 DeepSeek 生成，失败时为规则化文本
             "narrativeSource":"live"                          // live | cache | rule
      }
```

### 2.8 节点与拓扑（支撑现有页面）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/nodes` | 节点列表（含实时指标） |
| GET | `/nodes/{id}` | 节点详情 |
| GET | `/nodes/{id}/metrics` | 历史指标（`from`、`to`、`interval`） |
| POST | `/nodes/{id}/online` | 设备上线（DID 签名校验） |

```
GET /nodes
响应  data: {"items":[{
        "id":"Node-A","name":"虚拟电厂节点A","status":"online","model":"VPP-2000",
        "did":"did:vpp:edge:0x...","didStatus":"active",
        "metrics":{"pvOutput":45.3,"storageOutput":-12.0,"load":120,"soc":65},
        "lastSeenAt":"..."
      }],"total":4,"page":1,"size":20}
```

字段与现有 `raspi/public/data/mock.json` 完全对齐，前端旧页面可平滑迁移。

```
POST /nodes/{id}/online
请求  {"did":"did:vpp:edge:0x...","nonce":"abc123","signature":"<SM2签名hex>"}
响应  data: {"accepted":true,"nodeId":"Node-A","sessionToken":"...","evidenceId":"ev-..."}
失败  code 1004，无合法 DID 的节点一律拒绝接入。
```

### 2.9 联邦学习（3.8，后端代理算法服务）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/fl/tasks` | 创建训练任务 |
| GET | `/fl/tasks` | 任务列表（分页） |
| GET | `/fl/tasks/{id}` | 任务详情 |
| POST | `/fl/tasks/{id}/start` | 启动训练 |
| POST | `/fl/tasks/{id}/cancel` | 取消 |
| GET | `/fl/tasks/{id}/rounds` | 每轮指标 |
| GET | `/fl/models` | 模型版本列表 |
| POST | `/fl/models/{version}/publish` | 发布模型 |

```
POST /fl/tasks
请求  {"name":"负荷预测联合建模-第3轮","nodeIds":["Node-A","Node-B","Node-C","Node-D"],
       "rounds":10,"dp":{"enabled":true,"epsilon":1.0,"delta":1e-5},
       "topk":{"enabled":true,"ratio":0.1}}
响应  data: {"id":"fl-000012","status":"created","createdAt":"...","traceId":"tr-..."}
```

```
GET /fl/tasks/{id}
响应  data: {"id":"fl-000012","name":"...","status":"running","currentRound":4,"totalRounds":10,
             "nodes":[{"nodeId":"Node-A","did":"...","joined":true,"samples":480}],
             "dp":{"enabled":true,"epsilon":1.0,"epsilonSpent":0.42},
             "topk":{"enabled":true,"ratio":0.1,"compressionRatio":90.0},
             "rounds":[{"round":1,"loss":0.412,"acc":0.783,"compressionRatio":90.0,
                        "epsilonSpent":0.10,"gradientHash":"sm3:...","evidenceId":"ev-..."}],
             "modelVersion":null,"traceId":"tr-..."}
```

**乙的前端画收敛曲线直接用 `rounds` 数组。** 训练进行中通过 WebSocket `fl_progress` 增量推送。

### 2.10 智能调度（3.9）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/dispatch/tasks` | 创建调度任务 |
| GET | `/dispatch/tasks` | 任务列表 |
| GET | `/dispatch/tasks/{id}` | 任务详情 |
| POST | `/dispatch/tasks/{id}/run` | 运行 DQN 生成策略 |
| POST | `/dispatch/tasks/{id}/issue` | 下发指令（需 `dispatch:issue` 权限 + DID 签名） |
| POST | `/dispatch/tasks/{id}/ack` | 边缘节点回执 |

```
POST /dispatch/tasks/{id}/run
响应  data: {"id":"dp-000009","status":"success",
             "strategy":{"actions":[{"nodeId":"Node-C","action":"discharge","powerKw":24.0,
                                     "qValue":8.42,"reason":"负荷最高且SOC充足"}],
                         "totalReward":15.7,"timeWindow":"2026-08-17T15:00~16:00+08:00"},
             "explanation":"节点C当前负荷150kW为全网最高……",   // DeepSeek 生成
             "explanationSource":"live",
             "evidenceId":"ev-...","traceId":"tr-..."}
```

```
POST /dispatch/tasks/{id}/issue
请求  {"signature":"<签发者SM2签名hex>"}
响应  data: {"issued":true,"commandId":"cmd-000021","signerDid":"did:vpp:user:0x...",
             "evidenceId":"ev-...","targets":["Node-C"]}
失败  无 dispatch:issue 权限 → code 1003；签名无效 → code 1004。两种失败都记 high 风险审计日志。
```

### 2.11 AI 智能分析（3.10）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/ai/analyze` | 智能分析 / 问答 |
| GET | `/ai/history` | 分析历史 |

```
POST /ai/analyze
请求  {"scene":"dispatch","context":{"taskId":"dp-000009"},"question":"为什么选择节点C放电？"}
      scene 取值：dispatch（调度解释）| risk（风险分析）| data（数据分析）| qa（智能问答）| audit（审计解读）
响应  data: {"answer":"节点C当前负荷150kW……","reasoning":["总负荷450kW高于总光伏145kW", "..."],
             "source":"live",          // live=真实API  cache=离线兜底缓存  rule=规则化文本
             "latencyMs":1240,"evidenceId":"ev-...","traceId":"tr-..."}
```

**离线兜底是硬性要求**：真实 API 失败或超时（> 8s）必须自动回落到缓存，`source` 标记为 `cache`，绝不返回错误。

### 2.12 风险评估（支撑现有页面）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/risk/assess` | 动态隐私风险评估 |
| GET | `/risk/history` | 历史评分 |

```
POST /risk/assess
请求  {"nodeId":"Node-A","features":{"queryFreq":12,"dataGranularity":"minute","exposedFields":6}}
响应  data: {"nodeId":"Node-A","riskScore":72.4,"level":"high",
             "factors":[{"name":"查询频率","weight":0.35,"score":85,"desc":"5分钟内12次查询"},
                        {"name":"数据粒度","weight":0.30,"score":70}],
             "suggestion":"建议将差分隐私 ε 由 1.0 降至 0.5","evidenceId":"ev-..."}
```

### 2.13 WebSocket

连接：`ws://<host>/ws?token=<jwt>`。鉴权失败立即关闭（code 4001）。

服务端推送统一格式：

```json
{"type":"fl_progress","ts":"2026-08-17T14:23:05+08:00","traceId":"tr-...","payload":{}}
```

| type | payload | 触发时机 |
|---|---|---|
| `node_status` | `{nodeId, status, metrics}` | 节点状态/指标变化，5 秒一次 |
| `fl_progress` | `{taskId, round, totalRounds, loss, acc, compressionRatio, epsilonSpent}` | 每完成一轮 |
| `dispatch_progress` | `{taskId, stage, detail}` stage: `aggregating`/`computing`/`explaining`/`issued`/`acked` | 调度阶段流转 |
| `audit_alert` | `{alertId, ruleCode, riskLevel, message, actorDid}` | 风控规则命中 |
| `log` | `{level, module, content, traceId}` | 需要在前端底部日志栏滚动的事件 |
| `evidence_written` | `{evidenceId, category, blockHeight}` | 新存证上链 |

客户端可发送 `{"type":"ping"}`，服务端回 `{"type":"pong"}`。心跳间隔 30 秒。

---

## 第三部分：后端 ⇄ 算法服务（`http://algo-service:8100/algo/v1`）

> 全部由**乙**实现，**甲**调用。无 JWT 鉴权（仅容器内网可达），但**必须**透传 `X-Trace-Id` 请求头。
> 响应**不使用**统一包装，直接返回业务 JSON；失败返回 HTTP 4xx/5xx + `{"error":"..."}`。

### 3.1 健康检查

```
GET /algo/v1/health
响应  {"status":"ok","version":"1.0.0","models":{"dqn":"loaded","deepseek":"live|cache"}}
```

### 3.2 联邦学习

```
POST /algo/v1/fl/train
请求  {"jobId":"fl-000012","rounds":10,
       "nodes":[{"id":"Node-A","samples":480},{"id":"Node-B","samples":320}],
       "dp":{"enabled":true,"epsilon":1.0,"delta":1e-5},
       "topk":{"enabled":true,"ratio":0.1}}
响应  {"jobId":"fl-000012","status":"running"}
说明  异步执行，立即返回。甲通过下面的轮询接口获取进度。

GET /algo/v1/fl/jobs/{jobId}
响应  {"jobId":"fl-000012","status":"running","currentRound":4,"totalRounds":10,
       "rounds":[{"round":1,"loss":0.412,"acc":0.783,"compressionRatio":90.0,
                  "epsilonSpent":0.10,"gradientHash":"<SM3或SHA256 hex>",
                  "nodeContributions":[{"nodeId":"Node-A","weight":0.4,"localLoss":0.43}]}],
       "modelVersion":"v12","anomaly":null}
说明  anomaly 非 null 时（如 {"type":"gradient_poisoning","nodeId":"Node-C","detail":"..."}），
      甲必须生成 R05_SUSPICIOUS_GRAD 高危审计日志。

POST /algo/v1/fl/jobs/{jobId}/cancel
响应  {"jobId":"...","status":"cancelled"}
```

**乙必须实现真实的 FedAvg**：各节点用本地数据训练小模型，按样本量加权平均。DP 加真高斯噪声并累计 ε。Top-k 做真稀疏化并输出真实压缩率。禁止硬编码假曲线。

### 3.3 DQN 调度

```
POST /algo/v1/dqn/dispatch
请求  {"taskId":"dp-000009","timeWindow":"2026-08-17T15:00~16:00+08:00",
       "nodes":[{"id":"Node-A","pv":45.3,"load":120,"soc":65,"storage":-12.0,"price":0.62}]}
响应  {"taskId":"dp-000009",
       "actions":[{"nodeId":"Node-C","action":"discharge","powerKw":24.0,"qValue":8.42,
                   "reason":"负荷最高且SOC充足"}],
       "totalReward":15.7,
       "qTable":[{"nodeId":"Node-C","charge":3.1,"idle":5.2,"discharge":8.42}],
       "constraintsChecked":{"socMin":20,"socMax":95,"maxPowerKw":30,"violations":[]}}
说明  action 取值：charge | idle | discharge
```

**乙必须实现真实 DQN**：自建储能调度环境，离线训练并保存 checkpoint 到 `algo-service/models/dqn.npz`，服务启动时加载推理。

### 3.4 DeepSeek 分析

```
POST /algo/v1/deepseek/analyze
请求  {"scene":"dispatch","context":{...},"question":"..."}
响应  {"answer":"...","reasoning":["...","..."],"source":"live","latencyMs":1240}
说明  source 取值 live | cache | rule。
      乙负责：真实 API 调用 → 超时 8s 或失败 → 命中本地缓存 → 再失败 → 规则化模板文本。
      永远返回 200，永远有 answer。缓存文件 algo-service/cache/deepseek_cache.json。
```

### 3.5 数据分类分级

```
POST /algo/v1/classify
请求  {"records":[{"dataType":"pv","fields":["power","voltage","gps"],"freq":"minute","volume":1440}]}
响应  {"results":[{"index":0,"level":"L3","score":0.72,
                   "reason":"包含地理位置字段且采集粒度为分钟级",
                   "cluster":2,"factors":{"sensitivity":0.8,"granularity":0.7,"volume":0.5}}],
       "clusterCenters":[[0.2,0.3],[0.5,0.6],[0.8,0.7]]}
说明  用 k-means + 规则加权实现，替代原 raspi 前端 Pyodide 方案。
```

### 3.6 隐私风险评估

```
POST /algo/v1/risk/assess
请求  {"nodeId":"Node-A","features":{"queryFreq":12,"dataGranularity":"minute","exposedFields":6,
                                     "epsilonRemaining":0.58}}
响应  {"nodeId":"Node-A","riskScore":72.4,"level":"high",
       "factors":[{"name":"查询频率","weight":0.35,"score":85,"desc":"5分钟内12次查询"}],
       "suggestion":"建议将差分隐私 ε 由 1.0 降至 0.5"}
```

---

## 第四部分：环境变量（`.env`，两方共用）

```bash
# --- 数据库（甲使用）---
MYSQL_ROOT_PASSWORD=root123
MYSQL_DATABASE=energy_tds
MYSQL_USER=energy
MYSQL_PASSWORD=energy123
MYSQL_HOST=mysql
MYSQL_PORT=3306

# --- Redis（甲使用）---
REDIS_HOST=redis
REDIS_PORT=6379

# --- 后端（甲）---
BACKEND_PORT=8000
JWT_SECRET=energy-tds-demo-secret-2026
JWT_EXPIRE_SECONDS=28800
ALGO_SERVICE_URL=http://algo-service:8100

# --- 算法服务（乙）---
ALGO_PORT=8100
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_TIMEOUT=8
DEEPSEEK_OFFLINE_FALLBACK=true

# --- 前端（乙）---
FRONTEND_PORT=80
VITE_API_BASE=/api/v1
VITE_WS_BASE=/ws
VITE_USE_MOCK=false
```

---

## 第五部分：联调前的独立开发桩

合并前双方各自需要一个"假的对方"，**桩代码放在各自目录内，互不影响**：

| 谁 | 需要的桩 | 位置 | 要求 |
|---|---|---|---|
| 甲 | 假算法服务 | `backend/tests/fake_algo.py` | FastAPI 单文件，实现第三部分全部接口，返回固定假数据 |
| 乙 | 假后端 | `frontend/src/mocks/` | MSW handlers，实现第二部分全部接口；`VITE_USE_MOCK=true` 时启用 |

乙的 MSW 桩同时充当**答辩离线兜底**：后端挂掉时前端仍能演示。

---

## 第六部分：验收自检清单

合并后跑通以下链路才算完成：

1. `docker compose up -d` 一条命令起全部服务，无需联网
2. 用 `admin/admin123` 登录 → 拿到 token
3. 注册一个设备 DID → 列表可见 → DID 文档可查 → 验签通过
4. 用该 DID 登记一条 pv 数据资产 → 自动分级 → 生成 SM3 摘要 → 上链
5. 用 `subject/subject123` 申请该资产读权限 → `admin` 审批通过 → 权限生效
6. 用 `vpp/vpp123` 尝试 `POST /dispatch/tasks/{id}/issue` → 返回 1003 → 审计出现 high 风险日志 → 前端弹出 R01 告警
7. 创建 FL 任务并启动 → 前端收敛曲线随 WebSocket 实时增长 → 每轮梯度哈希上链
8. 运行 DQN 调度 → 生成策略 → DeepSeek 解释（断网时 `source=cache`）→ `admin` 签名下发成功
9. 调用 `/evidence/demo/tamper` 篡改一条存证 → `/evidence/verify` 返回 `intact:false` → 链状态显示断裂点
10. 用第 6 步的 traceId 查 `/audit/trace/{traceId}` → 返回完整步骤链
11. 生成日报 → 含 DeepSeek 自然语言解读
12. 全程断网，除 DeepSeek 降级为 cache 外，其余功能完全正常
