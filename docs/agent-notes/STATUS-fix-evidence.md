# STATUS · fix-evidence（2026-08-22）

负责范围：`backend/modules/evidence/**`、`backend/modules/audit/**`、`backend/core/deps.py`
（另按需动了 `backend/sql/01_schema.sql` 与 `backend/tests/`）。`contract/` 全程只读，
接口路径 / 字段名 / 枚举值一个都没改——只加了契约未定义的**附加**字段与一个契约外接口。

## 一、修了什么

| # | 缺陷 | 状态 | 改动落点 |
|---|---|---|---|
| 1 | §4 B-002 / API-EV-17、17b：`energy_subject` 的 `evidence:read` 「仅自有」未落实 | ✅ | `modules/evidence/router.py`、`service.py::search` |
| 2 | §4 B-001 / API-AUD-01：`require_roles` 拒绝不写审计日志 | ✅ | `core/deps.py::_audit_role_denial` |
| 3 | B-001 + B-025：篡改演示不可逆 | ✅ | `modules/evidence/model.py`、`service.py`、`router.py`、`sql/01_schema.sql` |
| 4 | B-017：`chain/status.brokenAt` 返回 evidenceId 字符串 | ✅ | `modules/evidence/chain.py::_status_full` |
| 5 | §4 B-007 / API-PERF-04：chain/status 全链重算 | ✅ | `modules/evidence/chain.py` 两级增量校验 |
| 6 | B-010：并发写存证死锁后静默丢弃 | ✅ | `modules/evidence/chain.py::write`、`service.py`、`modules/audit/service.py` |
| 附 | fix-algo 在 MSG-001 里请求的 `steps[].evidenceId` 透传（B-015 尾巴） | ✅ | `modules/audit/service.py::_write` |

### 1. 存证「仅自有」（§4 B-002）

照搬 `modules/asset/service.py` 已经正确的写法，两端保持一致：

- 列表：`router.py::search` 用 `has_own_scope_only(principal,"evidence","read")` 决定 `owner_only`，
  `service.search()` 在 SQL 里加 `chain_evidence.actor_did = :own`。
- 详情 / 凭证导出：给 `@require_permission("evidence","read")` 补上 `resource_id_arg="evidence_id"`，
  权限中心走 `_OWNER_SQL["evidence"]`（`SELECT actor_did FROM chain_evidence …`）判属主。
- 详情顺带补了 `@audited(action="evidence:read")`，和资产详情一致（需求「所有访问留痕」）。
- `scope='all'` 的 `sys_admin / grid_dispatcher / vpp_operator / regulator` 完全不受影响。

### 2. `require_roles` 拒绝要留痕（§4 B-001）

根因：`require_roles` 是 FastAPI **依赖**，在被 `@audited` 装饰的业务函数**之前**执行，
所以装饰器根本没机会跑，这条 1003 只进了 `main.py` 里的 R01 风控计数。

在 `core/deps.py` 的拒绝分支调 `core.middleware._write_audit(...)`，字段口径和 `@audited`
的 denied 分支完全一致（`result=denied`、`riskLevel` 经 `_escalate` 抬到 high、高危自动上链）。
`module` 由请求路径首段映射（`/users`→auth、`/audit/*`→audit、`/fl|dispatch|models|risk|privacy|ai`→algo…），
`action` = `<module>:<read|write|update|delete>`。写审计整段吞异常——绝不能盖掉本该返回的 1003。

**不会重复记账**：依赖先于业务函数执行，`@audited` 那条路径根本进不去
（`POST /evidence/demo/tamper` 同时挂了两者，实测 traceId 下只有 1 条日志）。

### 3. 篡改演示可逆（B-001 + B-025）

新表 `chain_evidence_backup`（`ChainEvidenceBackup` 模型 + `01_schema.sql` + 已有的 `03_migrate_20260822.sql`）：

- **已存在的备份一律不覆盖** ⇒ 同一条存证连续篡改 N 次，还原的仍是**最初**那份；
- restore 成功后删除备份行，下一轮演示重新记录；
- Redis 只作为「表不存在」时的降级，且同样遵守「不覆盖」。

**兼容「迁移未执行」**：探测表不存在时会自动 `CREATE TABLE IF NOT EXISTS`
（和审计按月分表的 `sharding.ensure_table` 一个路子），没有建表权限才降级 Redis，**任何情况都不 500**。
探测刻意用**独立会话**：表不存在会让事务进失败态，借调用方的会话去探，那次 rollback 会把
restore 刚写回去的快照一起丢掉（这个坑在联调时真踩到了）。建表后额外回滚一次调用方事务，
避开 MySQL 的 `1412 Table definition has changed`（一个库一辈子只走一次）。

新增契约外接口 `POST /evidence/demo/restore-all`（仅 sys_admin，路径不与契约 2.6 冲突）：
一键把所有备份写回，返回 `{restored, restoredCount, failed, chain{intact,brokenAt,brokenAtEvidenceId}}`。

### 4. `brokenAt` 语义（B-017）

`brokenAt` 改为区块高度（int / null），新增 `brokenAtEvidenceId`（str / null）与 `verifiedAt`。
**前端要顺手改一行**，见 `MSG-fix-evidence-to-fix-ws-001.md`：`normalizeChain` 目前只在
`brokenAt` 是字符串时才回填 `brokenAtId`，改成 int 之后「断裂块标红」在刷新后会丢一处来源
（不报错、链状态与断裂高度都正常）。

### 5. chain/status 增量校验（§4 B-007 / API-PERF-04）—— **取舍与验证**

**取舍**：只优化「要不要重算」，绝不优化「怎么判定完整」。判定完整性的仍然是原来那套
逐块 SM3 全量重算，一行代码没删。两级缓存：

1. **结果级**：一条纯 SQL 聚合算全表指纹 —— 行数、最大高度、逐行
   `CRC32(payload_hash|prev_hash|block_hash|created_at|payload_snapshot)` 的**普通和**与**按高度加权和**。
   指纹由数据库端算（C 实现，0.65ms），覆盖 payload 快照与三个哈希列；
   任何一行被改（篡改演示正是直接改库）指纹必变 ⇒ 缓存失效 ⇒ 回到全量校验。
   加权和让「两行互换」也逃不掉。指纹只做「要不要重算」的判断，从不参与完整性结论。
   非 MySQL 方言（单测用的 SQLite）拿不到指纹 ⇒ **永远全量校验**，所以单测跑的一直是最严路径。
2. **逐块级**：记住「高度 H 的这组 (payload 的 SHA-256 摘要, payload_hash, prev_hash, block_hash, ts)
   已经通过 SM3 校验」，五元组完全一致才跳过两次 SM3。摘要用 SHA-256（C 实现，微秒级）
   只当「这一行有没有变过」的记忆键 —— 内容一变摘要必变，**被篡改的那一行必然 memo miss，
   必然重新走完整 SM3 校验**。记忆键含内容，所以库被重建、高度被复用也不会误判。

单条 `POST /evidence/verify` 永远不走任何缓存，答辩现场的「单条标红」是硬校验。
`tamper` / `restore` 还会主动 `invalidate_status_cache()` 作双保险。

**验证方法**（三条腿）：
- 单测 `test_PERF04_增量校验后篡改照样被检出`：先跑一次 status 把整条链喂进记忆，
  再绕过应用层直接改库，断言 status 仍然 `intact:false` 且 `brokenAt` 精确定位到那一块，改回去又恢复 intact。
- 单测 `test_PERF04_已校验过的块不再重算SM3`：数 `calc_payload_hash` 调用次数，
  第二次 status 为 0 次，新增一个块后只算 1 次。
- 真机（8012，MariaDB，链长 236）：

  | 场景 | 耗时 |
  |---|---|
  | 全量重算（修复前口径） | 501 / 470 / 469 ms |
  | 链未变（指纹命中） | 0.6～0.7 ms |
  | 链有变（新块或篡改，逐块记忆命中） | 12.3 ms |
  | 指纹 SQL 单次 | 0.65 ms |

  按修复前的线性增长，1700 条时约 3.4s；现在与链长基本脱钩。

**残余风险（如实记录）**：构造一次 CRC32 普通和与加权和**同时**碰撞的改动，能骗过「要不要重算」
这一步。这需要针对性构造，且单条 verify、凭证导出、任何一次新块写入引起的重算都会立刻暴露它；
演示与验收场景下可接受。想彻底关掉这层，把 `_table_fingerprint()` 返回 `None` 即可退回全量校验。

### 6. 并发写存证（B-010）

两道防线：

1. 进程内 `_WRITE_LOCK` 把「取尾块 `SELECT … FOR UPDATE` → 插入新块」串行化，
   uvicorn 单进程多线程的场景下把并发退化成排队，从源头消掉绝大部分锁竞争；
2. 死锁(1213) / 锁超时(1205) / SQLite `database is locked` 自动 `rollback` 后重试 3 次，
   0.05s 起指数退避 + 随机抖动。

重试用尽不再静默丢弃：`write_evidence` 在返回值里带 `chainError`（调用方可感知），
并经 `modules/audit/service.report_chain_write_failure()` 写一条 **`to_chain=False`** 的
critical 审计日志 + WebSocket error 日志。刻意不走 `rules.fire`：R01～R05 规则码是契约 2.7 固定的
不能私自加 R06，而且 `fire → _create_alert` 内部还会 `write_evidence`，链写不动时只会再失败一次
（另有线程局部 `_ALERT_GUARD` 挡住递归）。审计写入仍然全部收敛在审计模块里，
`test_audit.py::test_业务代码里没有手写审计调用` 这条架构约束不破。

## 二、怎么验证的

### 后端单测

```
cd backend && .venv/bin/python -m pytest -q
```
`333 passed, 1 failed` —— 唯一失败的是环境约定允许的
`test_audit.py::test_审计报告在算法服务不可用时降级`（它假设算法服务不可达，本机 8100 在跑）。

新增 `backend/tests/test_evidence_fixes.py`（26 条，用例名带缺陷编号）：
B-002 五条（含 4 个 scope=all 角色的参数化反证）、§4 B-001 三条、B-001/B-025 四条
（连续两次篡改、备份用后即删、一键还原、迁移未执行降级 Redis）、B-017 一条、
API-PERF-04 三条、B-010 六条（含 4 线程 × 3 次的并发写存证单测）。
另把 `test_evidence.py` / `test_evidence_chain.py` / `test_security.py` 里三处
`brokenAt == evidenceId` 的断言按 B-017 新语义改成高度 + `brokenAtEvidenceId`。

### 真实环境（自起 8012 实例，真 MariaDB + 真 Redis，未碰共享的 8000/8100/5199）

```
$ curl -s "$B/evidence?size=50" -H "Bearer <subject>"
total= 6   actorDid集合= {'did:vpp:user:0x4018374cd87a3d4a566223e8bbc2e852'}     # 全是自己的
$ curl -s "$B/evidence?size=5" -H "Bearer <admin>"      → total= 220
$ curl -s "$B/evidence?size=5" -H "Bearer <regulator>"  → total= 220

$ curl -s "$B/evidence/ev-000219" -H "Bearer <subject>"                      # admin 写的存证
HTTP 403 {"code":1003,"message":"角色 energy_subject 对该资源仅有自有数据范围的 evidence:read 权限"}
$ curl -s "$B/evidence/ev-000219/certificate" -H "Bearer <subject>"          → 同样 403/1003
$ curl -s "$B/evidence/ev-000227" -H "Bearer <subject>"                      → 200，自己的读得到

$ curl -s "$B/users" -H "Bearer <vpp>" -H "X-Trace-Id: tr-20260822-fixev001"
HTTP 403 {"code":1003,"message":"该操作仅限 sys_admin，当前角色 vpp_operator"}
$ curl -s "$B/audit/logs?traceId=tr-20260822-fixev001" -H "Bearer <admin>"
{"items":[{"id":346,"traceId":"tr-20260822-fixev001",
  "actorDid":"did:vpp:user:0x09019061e23e720e3ffbb089101b5a69","actorName":"虚拟电厂运营商",
  "module":"auth","action":"auth:read","result":"denied","riskLevel":"high",
  "detail":"该操作仅限 sys_admin，当前角色 vpp_operator","ip":"127.0.0.1",
  "evidenceId":"ev-000229","hash":"sm3:a9715dd8…","at":"2026-08-22T23:54:57+08:00"}],"total":1}

# 同一条存证连续篡改两次 → 还原
$ POST /evidence/demo/tamper {"evidenceId":"ev-000042","newValue":{"pvOutput":999.9}}
  backupStore= db        original = {'action':'asset:register','assetId':1025,…}
$ POST /evidence/demo/tamper {"evidenceId":"ev-000042","newValue":{"pvOutput":111.1,"extra":"第二次"}}
  backupStore= db(kept)                                  # ← 备份没被第二次篡改覆盖
$ GET /evidence/chain/status
  {'height':235,'intact':False,'brokenAt':42,'brokenAtEvidenceId':'ev-000042'}   time=0.020s
$ POST /evidence/demo/restore {"evidenceId":"ev-000042"}
  {"restored":true,"backupStore":"db",
   "verification":{"intact":true,"localHash":"sm3:084f49c2…","chainHash":"sm3:084f49c2…",
                   "blockHeight":42,"tamperedAt":null,"message":"数据完整，与链上摘要一致"}}
$ GET /evidence/chain/status   ×3        time=0.031s / 0.0085s / 0.0067s
  {'height':236,'intact':True,'brokenAt':None,'brokenAtEvidenceId':None,'totalRecords':236}

$ POST /evidence/demo/restore-all
  {"restored":["ev-000100"],"restoredCount":1,"failed":[],
   "chain":{"intact":true,"brokenAt":null,"brokenAtEvidenceId":null}}

# B-010：30 个并发越权请求（每条都要写 high 审计日志并上链）
30 个 403；audit_log_202608 里对应 30 条日志，缺 evidence_id 的 0 条；
后端日志 grep "存证上链失败" = 0、"存证上链锁冲突" = 0
```

## 三、要不要执行迁移脚本

**不必须**。`chain_evidence_backup` 在表不存在时会由后端自动建（已在联调库上真实发生过一次，
表已存在）。仍然建议在正式部署前执行一次 `backend/sql/03_migrate_20260822.sql`
（里面还有 fix-algo 那条 `algo_fl_round.loss` 改 DOUBLE，那条**必须**人工执行，我改不了）。
全新部署走 `01_schema.sql` 即可，已把建表语句补进去。

## 四、遗留 / 需要别人接的

1. **前端一行**：`frontend/src/views/EvidenceCenter.vue::normalizeChain` 改读 `brokenAtEvidenceId`，
   见 `MSG-fix-evidence-to-fix-ws-001.md`（归 fix-ws）。不改不报错，只是刷新后断裂块少一处标红来源。
2. **qa 脚本的一条断言**：`qa/api-tests/run_all.py:485` 的 API-EV-09 断言 `brokenAt is None` 仍然成立；
   但 `qa/check_backend_live.sh:355` 的「篡改后 `d['brokenAt']` 非空」在断裂点恰好是**高度 0** 时
   会因为 `0` 为假而误判（极端情况，未改动 qa 文件，留给 qa 的负责人决定）。
3. **共享联调库数据**：本轮验证在 8012 上产生了 ~40 条新审计日志与对应存证块
   （含 30 条 vpp 越权 denied，属于真实的越权演示数据），链 `intact:true`，无残留篡改。
4. **`_table_fingerprint` 的残余风险**：见上文「取舍与验证」最后一段。
5. 顺手收了 fix-algo 在 `MSG-fix-algo-to-fix-evidence-001.md` 里提的第一条建议
   （`_write()` 沿用调用方已有的 `evidenceId`）；第二条「把 `chain_evidence` 并进 `/audit/trace` 时间轴」
   **没做** —— 会让 `steps` 的语义从「操作」变成「操作 + 存证」，偏离契约 2.7，不值得在答辩前动。
