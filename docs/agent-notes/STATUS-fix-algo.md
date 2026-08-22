# STATUS · fix-algo（2026-08-22）

负责范围：`backend/modules/algo/**`、`backend/modules/permission/**`、`backend/core/middleware.py`
（另按分工要求代修 `modules/asset/service.py` 的并发 500，未碰它的 scope 过滤与 model.py 时间列）。

## 一、修了什么

| 缺陷 | 结论 | 主要改动 |
|---|---|---|
| **B-014 P1** 并发 500 + traceId 全零 | 已修 | 新增 `core/retry.py`；`core/middleware.py` TraceMiddleware 异常分支保留 traceId；`main.py::_envelope` 优先读 `request.state.trace_id` |
| **B-012** 并发审批 500 | 已修 | `permission/service.py::_claim_pending` 条件更新占位，后到者 1006 |
| **B-015 P2** 全流程追踪只有 1 步 | 已修（产生端） | `core/middleware.py::adopt_trace / audit_step`；algo 各接口沿用任务 traceId，每轮补 `fl:round` 埋点 |
| **B-008** ack 接受非目标节点 | 已修 | `ack_dispatch` 校验目标集合，返回 1001 |
| **B-026** ack 不上链不回 evidenceId | 已修 | `ack_dispatch` 补 `write_evidence`，响应与 WS 均带 `evidenceId` |
| **B-004 / B-016** 托管代签签名不落库、详情不回传 | 已修 | `_verify_signature` 回写签名到请求体；`_dispatch_to_item` 增加 `signature` |
| **B-003** expireAt 允许过去时间 | 已修 | `_parse_expire` 校验，返回 1001 |

### B-014 的根因（值得记一笔）

不是「乐观锁版本号」冲突，而是：

1. `db.add(record); db.flush()` → INSERT 已进事务；
2. `write_evidence()` 里链尾 `SELECT ... FOR UPDATE` 撞死锁（1213）/锁等待（1205），
   **InnoDB 把整个事务回滚掉**，第 1 步的 INSERT 一起没了；
3. 这个异常被存证层按设计吞掉（「存证失败不阻断业务」），SQLAlchemy 的 Session
   却不知道数据库已经回滚，仍把那行当 persistent；
4. `record.evidence_id = ...; db.commit()` 发出的 UPDATE 匹配 0 行 → `StaleDataError` → 500。

所以修法是把「一次完整的写事务」当成可重放单元（`core/retry.py::run_with_retry`），
而不是给表加 version 列。可重试判定：StaleDataError、1213、1205、1614、1062、
以及 SQLite 的 `database is locked`。重试 3 次仍冲突 → 契约 1006，绝不 5000。

traceId 全零则是另一条独立链路：未捕获异常冒泡到 Starlette 最外层的
`ServerErrorMiddleware`（比 TraceMiddleware 更外层），此时 `TraceMiddleware.finally`
已经把 contextvar reset 回默认值 `tr-00000000-00000000`。

## 二、改了哪些文件

- 新增 `backend/core/retry.py`
- 新增 `backend/tests/test_fix_algo.py`（14 条）
- `backend/core/middleware.py`：TraceMiddleware 异常分支、`adopt_trace`、`audit_step`、`_write_back`
- `backend/main.py`：`_envelope` 增加 `request` 参数（4 个异常处理器传入）— 该文件无人认领，仅这一处
- `backend/modules/algo/service.py`、`analysis.py`、`router.py`
- `backend/modules/permission/service.py`
- `backend/modules/asset/service.py`：仅 `create_asset` 的写事务包上重试（未动 scope 过滤 / 时间列）
- 文档：`BACKEND-ISSUES.md` 追加 ✅ 行、`MSG-fix-algo-to-fix-evidence-001.md`

## 三、怎么验证的

### 单测

`cd backend && .venv/bin/python -m pytest -q` 全绿，唯一失败是环境相关的
`test_audit.py::test_审计报告在算法服务不可用时降级`（本机 8100 在跑，与本轮改动无关）。

并发用例是**真线程 + 真会话**：conftest 的内存库用 StaticPool（所有会话共用一条连接），
多线程提交会直接撞 `cannot commit transaction - SQL statements in progress`，
测不出并发语义。因此 `test_fix_algo.py` 里加了 `concurrent_client` fixture——
业务表落在临时**文件版 SQLite**（每线程一条连接、真实锁竞争），
账号/角色/权限矩阵从内存库克隆过去；用完清 `sharding._known_tables` 缓存并 dispose。

### 真实环境（自起 8013 实例 + 真 MariaDB + algo-service@8100，未碰 8000/8100/5199）

```
### 创建 FL 任务  {"id":"fl-000004","traceId":"tr-20260822-1ffa7909"}
### 启动训练      {"id":"fl-000004","status":"running","traceId":"tr-20260822-1ffa7909"}   # 与创建同一个 traceId
### 训练结束      {"status":"success","currentRound":3,"modelVersion":"v4"}
每轮存证：[(1,'ev-000220'), (2,'ev-000221'), (3,'ev-000222')]

### GET /audit/trace/<该 traceId>
summary: {"durationMs":2000,"result":"success","riskLevel":"medium","steps":6}
steps:   fl:create → fl:train → fl:round → fl:round → fl:round → fl:finish      # 修复前只有 1 步、durationMs=0
         三条 fl:round 的 evidenceId 分别为 ev-000267 / ev-000268 / ev-000269（非空）

### 创建调度任务  {"id":"dp-000004","traceId":"tr-20260822-0c8ae17f"}
### run           {"evidenceId":"ev-000223","signPayload":"dispatch:issue:dp-000004:sm3:e262699567ad…"}
### issue（签名留空 → 托管代签）
                  {"issued":true,"commandId":"cmd-000004","evidenceId":"ev-000224",
                   "targets":["Node-A","Node-C","Node-D"]}
### GET /dispatch/tasks/dp-000004
                  "signature":"245c3b59c025256cc8580bc4971a174121d8724ca2677e842059f00e32900a0e1bd5b8a85a4795d2e8e3160d9e96d553328faa0e8d3f91abfdd8ede750fee522"
                  "signPayload":"dispatch:issue:dp-000004:sm3:e262699567ad…"     # 边端可做真验签
### 非目标节点 ack {"code":1001,"message":"节点 Node-Z 不在本次下发的目标节点内（Node-A、Node-C、Node-D），拒绝回执"}
### 目标节点 ack   {"taskId":"dp-000004","nodeId":"Node-A","ackStatus":"partial",
                   "acked":1,"total":3,"evidenceId":"ev-000226"}
### GET /audit/trace/<该调度 traceId>
summary: {"durationMs":9000,"riskLevel":"high","steps":5}
steps:   dispatch:create → dispatch:run → dispatch:issue
         → dispatch:ack(failed，非目标节点被拒) → dispatch:ack(success)
         # adopt_trace 会把任务 traceId 写回 request.state，被拒的那一步同样落在这条链上

### 过去的 expireAt  {"code":1001,"message":"expireAt 必须晚于当前时间：2020-01-01T00:00:00+08:00"}
```

验证脚本：`<scratchpad>/verify_fix_algo.py`（8013 实例已在验证后关闭）。

## 四、是否需要执行迁移脚本

**不需要。** 本轮改动没有动表结构，`algo_dispatch_task.signature` 列在
`sql/01_schema.sql:407` 本来就有，只是以前没往里写。

## 五、遗留 / 需要别人配合

1. ~~`/audit/trace` 的 `steps[].evidenceId` 为 null~~ **已闭环**：fix-evidence 按
   `MSG-fix-algo-to-fix-evidence-001.md` 在 `modules/audit/service.py:108` 加了
   `payload.get("evidenceId")` 透传，复测 `fl:round` 三步的 evidenceId 为
   ev-000267 / ev-000268 / ev-000269。
2. 乙方 E2E `frontend/e2e/10-audit.spec.js:31` 当初把断言从 ≥3 步放宽到 ≥1 步，现在可以改回。
3. B-010（存证链写入的死锁重试）在存证模块内部，属 fix-evidence；本轮的 `core/retry.py`
   是通用件，那边如果要用直接 import 即可。
4. `main.py` 无人认领，本轮改了 `_envelope` 一处（加 `request` 参数）——如果别人也动了这个文件，
   合并时注意这 5 行。
