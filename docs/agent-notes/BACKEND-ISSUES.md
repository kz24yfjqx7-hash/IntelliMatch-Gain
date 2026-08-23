# 后端（甲方 backend/）问题记录

> 只记录不改。格式：编号、严重级别、复现步骤、期望 vs 实际、涉及文件:行号、契约章节。
>
> 编号不连续（缺 B-003~B-009）：多个测试 Agent 并行提单时各自预留了号段，合并去重后空出这些号，
> 不代表遗漏。各测试文档中引用的编号与本文件一致。

## B-001 P2 同一存证连续两次 `demo/tamper` 会覆盖原始快照备份，`demo/restore` 无法还原

- 提出：ops-doc，2026-08-22
- 涉及：`backend/modules/evidence/service.py:208-211`（`redis_client.safe_set(_TAMPER_BACKUP_PREFIX + evidence_id, json.dumps(original))` 无条件覆盖）、`:236-241`（restore 直接取该 key）
- 复现：
  1. `POST /evidence/demo/tamper {"evidenceId":"ev-X","newValue":{"pvOutput":999.9}}`（例如 qa/check_backend_live.sh 跑过一次）
  2. 再次对同一 ev-X 调 tamper（例如前端「篡改演示」对话框默认选中的就是同一条）
  3. `POST /evidence/demo/restore {"evidenceId":"ev-X"}`
- 期望：`restored:true` 且 `verification.intact:true`，链状态恢复 `intact:true`
- 实际：`restored:true` 但 `verification.intact:false`（备份里已是第一次篡改后的 `pvOutput:999.9`），链永久断裂；只能手工把原始 payload 写回 Redis key `evidence:tamper:backup:ev-X` 后再 restore（本次联调即如此恢复 ev-000203 / ev-000272）。
- 建议：备份用 `SET NX`（已存在则不覆盖），或 tamper 前先检查 verify 结果为 intact 才写备份；restore 成功后再删除 key。
- 契约：2.6 `/evidence/demo/tamper`（restore 为契约外补充接口）。
- **✅ 已修复（fix-evidence，2026-08-22）**：原始快照改为落库到 `chain_evidence_backup`，**已存在的备份一律不覆盖**（保住最初那份），restore 成功后删除备份行；Redis 仅作为「表不存在」时的降级，同样遵守「不覆盖」。
  `backend/modules/evidence/model.py:30`（新 `ChainEvidenceBackup`）、`service.py:_save_backup/_load_backup/_drop_backup/tamper/restore`、`sql/01_schema.sql` + `sql/03_migrate_20260822.sql`。
  表不存在时会自动建表（同审计按月分表的 `ensure_table` 路子），没有建表权限才降级 Redis，任何情况都不 500。回归：`backend/tests/test_evidence_fixes.py::test_B001_连续篡改两次仍能还原到最初状态` 等 4 条。

## B-002 备注（非缺陷）后端探活端点为根路径 `GET /health`

- `backend/main.py:171`，不走 `/api/v1` 包装；`/api/v1/health` 返回 404 的 `code 1005`。乙方 compose / install.sh / nginx 已按 `/health` 适配，手册已写明。

## B-010 ｜中 ｜ 并发写存证时死锁/锁等待，失败后存证被静默丢弃，高危日志缺 evidence_id
- 复现：多个 Agent 并发调用（FL 轮次上链 + 审计上链 + 业务上链）时，`scratchpad/backend.log` 出现 `存证上链失败 …(1213, 'Deadlock found')` 132 次、`(1205, 'Lock wait timeout')` 2 次；`audit_log_202608` 中 `id>160` 的 406 条 high 日志有 6 条 `evidence_id IS NULL`（如 id 1609/1610 login failed）。
- 期望：文档(四)5「审计日志摘要自动推送至存证模块上链，避免删除篡改」为硬要求；`chain.write` 的 `SELECT … FOR UPDATE` 尾块行锁与 `uk_height` 在并发下应重试（死锁/锁超时重试 2~3 次）或串行化写链队列，失败应告警而非仅打日志。
- 用例：API-AUD-01b / API-AUD-21 在并发高峰期间间歇失败（单独运行通过）。
- **✅ 已修复（fix-evidence，2026-08-22）**：`chain.write` 两道防线——① 进程内 `_WRITE_LOCK` 把「取尾块→插入」串行化；② 死锁(1213)/锁超时(1205)/SQLite locked 自动回滚重试 3 次（0.05s 起指数退避 + 抖动）。重试用尽不再静默丢弃：`write_evidence` 返回 `chainError` 让调用方可感知，并经 `modules/audit/service.report_chain_write_failure()` 写一条 `to_chain=False` 的 critical 审计日志 + WebSocket 错误日志。
  `backend/modules/evidence/chain.py:38-90,120-160`、`modules/evidence/service.py:write_evidence`、`modules/audit/service.py:report_chain_write_failure`。
  回归：`tests/test_evidence_fixes.py` 的 B-010 五条（含 4 线程 × 3 次的并发写单测）；真机 30 并发越权请求 → 30 条 high 审计日志 `evidence_id` 全部非空，后端日志 0 次锁冲突。

## B-011 ｜中 ｜ `algo_fl_round.loss` 为 `DECIMAL(10,6)`，算法返回 loss ≥ 10000 时整轮落库与上链失败
- 复现：日志 `落库联邦学习进度失败 fl-000011：(1264, "Out of range value for column 'loss'")` 562 次，同一任务每轮重复报错；对应 `存证上链失败 category=algo`。乙方 algo-service 的负荷预测 loss 为 kW² 量级的 MSE，可能超过 9999.999999。
- 期望：列改为 `DOUBLE` 或 `DECIMAL(18,6)`，且单轮落库失败不应阻断后续轮次；建议同时要求乙方对 loss 做归一化（已在 MSG-test-api-to-integration-001 提醒）。
- 影响：前端收敛曲线缺轮、`rounds[].evidenceId` 缺失，违反「每轮梯度哈希上链」。

## B-012 ｜低 ｜ 并发审批/驳回同一申请时 500（StaleDataError）
- 复现：日志 6 次 `sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table 'perm_application' expected to update 1 row(s); 0 were matched.`，由两个客户端几乎同时 approve/reject 同一 pending 申请触发，响应为 500/5000。
- 期望：加行锁或 `WHERE status='pending'` 的条件更新，后到者返回 409/1006。
- **✅ 已修复（fix-algo，2026-08-22）**：`modules/permission/service.py` 新增 `_claim_pending()`（条件更新 `WHERE id=:id AND status='pending'`，rowcount=0 即 1006），approve/reject 都改走它；`revoke_grant` 同样用 `WHERE status='active'` 占位；四个写接口统一套 `core/retry.py::run_with_retry`（死锁/锁等待重试 3 次，仍冲突返回 1006）。回归：`tests/test_fix_algo.py::test_B012_并发审批同一申请只有一个成功`、`::test_B012_并发审批与驳回不产生500`（多线程 + 真实会话 + 文件版 SQLite）。

## B-013 P2 `POST /did/{did}/rotate-key` 强制要求请求体，契约未定义该接口的 body

- 提出：integration，2026-08-22
- 涉及：`backend/modules/did/schema.py:24`（`DidRotateKeyRequest`）、`backend/modules/did/router.py:83-88`（`body: DidRotateKeyRequest` 为必填参数）
- 复现：`curl -X POST http://127.0.0.1:8000/api/v1/did/<did>/rotate-key -H "Authorization: Bearer <admin>"`（无 body）
- 期望（契约 2.2 表格「POST /did/{did}/rotate-key 密钥轮换」，未定义请求体，无 body 应可轮换）：`code 0`，返回新公私钥与 version
- 实际：`{"code":1001,"message":"参数错误： Field required"}`（HTTP 400），身份中心「轮换密钥」按钮在真后端 400
- 影响页面：`/identity`（密钥轮换）
- 建议：`body: DidRotateKeyRequest = DidRotateKeyRequest()` 给默认值。乙方已兼容（`frontend/src/api/did.js:16-18` 固定发 `{}`），两端都可用，无需回归。
- **✅ 已修复（fix-identity，2026-08-22）**：`backend/modules/did/router.py:84-86` 给 `body` 加默认值 `DidRotateKeyRequest()`，无 body / 带 body 两种调用都返回 `code 0`。回归 `backend/tests/test_did_fix_b020_b013_b023.py::test_b013_轮换密钥不带请求体也能成功`；真机 `curl -X POST /api/v1/did/<did>/rotate-key`（不带 `-d`）实测 `{"code":0,...,"version":5}`。

## B-014 P1 `POST /assets`、`POST /risk/assess` 偶发 500，且 traceId 退化为 `tr-00000000-00000000`

- 提出：integration，2026-08-22（Playwright 全页面巡检，admin `/edge/privacy`、edge `/edge/privacy`）
- 复现：多 Agent 并发操作时提交资产登记 / 风险评估；`scratchpad/backend.log` 对应 `sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table 'algo_risk_assessment' expected to update 1 row(s); 0 were matched.`
- 期望（契约 1.1 code 语义 + 1.2 traceId 由 backend 生成、格式 `tr-YYYYMMDD-<8hex>`）：并发下要么成功，要么返回 1006；异常响应的 traceId 仍应是真实 traceId
- 实际：`{"code":5000,"message":"服务器内部错误","data":null,"traceId":"tr-00000000-00000000"}`；该 traceId 无法用于 `/audit/trace/{traceId}` 追踪，违背「任务级全流程追踪」
- 影响页面：`/assets`（登记）、`/edge/risk`、`/edge/privacy`
- 建议：与 B-010/B-012 同源（乐观锁/行锁冲突），统一加重试或条件更新；异常处理分支里沿用请求入口生成的 traceId，不要回落到全零串
- **✅ 已修复（fix-algo，2026-08-22）**：
  1) 根因是「`db.flush()` 插入 → `write_evidence` 撞死锁被 MySQL 整事务回滚（异常被存证层吞掉）→ 紧接着的 `UPDATE ... SET evidence_id` 匹配 0 行 → StaleDataError」。新增 `backend/core/retry.py::run_with_retry`（把整段写事务当可重放单元，命中死锁 1213 / 锁等待 1205 / 唯一键 1062 / StaleDataError 就 rollback 重放，3 次仍冲突按契约返回 1006），已接到 `modules/algo/analysis.py:assess/analyze`、`modules/asset/service.py:create_asset`、`modules/algo/service.py` 的建任务/run/issue/ack、`modules/permission/service.py` 四个写接口。
  2) traceId 全零：未捕获异常冒泡到 Starlette 最外层 `ServerErrorMiddleware`，那里已在 `TraceMiddleware.finally` 之外，contextvar 被 reset 成默认值。`core/middleware.py:105-133` 改为把 traceId 挂 `request.state.trace_id` 且异常分支不 reset，`main.py:41-48` 的 `_envelope` 优先取 `request.state.trace_id`。回归：`tests/test_fix_algo.py::test_B014_*`（6 线程并发风险评估/资产登记无 5000、500 响应 traceId 与请求头一致）。

## B-015 P2 `/audit/trace/{traceId}` 对 FL / 调度链路只返回 1 步，缺少 login→did:verify→permission:check→evidence:write 的完整链

- 提出：integration，2026-08-22
- 复现：`GET /fl/tasks?size=1` 取 traceId（如 `tr-20260822-e942207b`）→ `GET /audit/trace/tr-20260822-e942207b`
- 期望（契约 2.7 示例）：`steps` 为 auth/did/permission/algo/evidence 的多步链，`steps[].evidenceId` 非空
- 实际：`steps` 仅 1 条 `{module:"algo",action:"fl:train",evidenceId:null}`，`summary.durationMs=0`
- 影响页面：`/audit` 全流程追踪时间轴（演示第 10 步说服力下降）；乙方 E2E 已把断言从 ≥3 步放宽到 ≥1 步（`frontend/e2e/10-audit.spec.js:31`），后端修复后可改回
- 建议：登录、权限校验、每轮上链的审计记录复用同一 traceId（契约 1.2「该请求产生的所有审计日志、存证记录共用同一 traceId」）
- **✅ 已修复（fix-algo，2026-08-22）**：`core/middleware.py` 新增 `adopt_trace()`（接口拿到任务后把上下文 traceId 换成任务创建时的 traceId）与 `audit_step()`（给后台协程这类挂不上装饰器的步骤补埋点）；`modules/algo/service.py` 的 `start_fl_task/cancel/publish_model/run_dispatch/issue_dispatch/ack_dispatch` 与 `router.py:start_fl_task` 全部沿用任务 traceId，`persist_fl_progress` 每轮补 `fl:round`、结束补 `fl:finish`。实测（8013 实例 + 真库 + algo@8100）：FL `tr-20260822-1ffa7909` 从 1 步变 6 步（fl:create→fl:train→fl:round×3→fl:finish，durationMs=2000），调度 `tr-20260822-0c8ae17f` 4 步（create→run→issue→ack）。`steps[].evidenceId` 由 fix-evidence 按 `MSG-fix-algo-to-fix-evidence-001.md` 在 `modules/audit/service.py:108` 做了透传，复测已非空（`fl:round` 三步分别是 ev-000267/268/269）。另：`adopt_trace` 会把任务 traceId 同步写回 `request.state`，被拒绝的步骤（如非目标节点回执）也落在同一条链上（实测调度链 5 步，含一条 `dispatch:ack / failed`）。回归：`tests/test_fix_algo.py::test_B015_*`。

## B-016 P3（说明，非缺陷）调度任务下发/回执后 `status` 仍为 `success`，且详情不回传原始签名

- 后端用 `issued/commandId/issuedAt/ackStatus/ackDetail[]` 表达下发与回执，`status` 保持契约 1.6 的 `taskStatus` 枚举——**符合契约**，是 MSW 桩多造了 `issued`/`acked` 两个非契约状态。
- 乙方已在 `frontend/src/api/dispatch.js:normalizeDispatchTask` 做归一化，两种模式都可用。
- 另：`GET /dispatch/tasks/{id}` 回传 `signPayload` 但不回传 `signature`，边端「DID 签名校验」无法复核签名字节，前端已降级为「按 DID 文档状态判定」并如实标注（`frontend/src/views/TerminalResponse.vue:254-268`）。如后端能回传 `signature`（脱敏无碍，它本就是公开可验证数据），边端即可做真验签，建议补上。
- **✅ 已补上（fix-algo，2026-08-22）**：`modules/algo/service.py::_dispatch_to_item` 增加 `signature` 字段；配合 B-004 的托管代签落库，`GET /dispatch/tasks/{id}` 现在同时返回 `signPayload` + `signature`，边端可做真验签（`status` 枚举保持不变，仍按契约）。

## B-017 P3 `/evidence/chain/status` 的 `brokenAt` 返回 evidenceId 字符串，契约示例为高度/`null`

- 复现：`GET /api/v1/evidence/chain/status` → `{"height":406,"intact":false,"brokenAt":"ev-000203"}`
- 期望（契约 2.6 `{"height":128,"lastHash":"...","intact":true,"brokenAt":null,...}`，与 `height` 同名词义，应为区块高度或 null）
- 实际：字符串 evidenceId；前端按高度比较「受影响区块」会失效
- 影响页面：`/evidence`（断裂点标红 / 受影响块）。乙方已兼容：拿到字符串时回查该存证的 `blockHeight`（`frontend/src/views/EvidenceCenter.vue:normalizeChain`），并保留 `brokenAtId` 展示
- 建议：`brokenAt` 用高度，另加 `brokenAtEvidenceId` 字符串字段
- **✅ 已修复（fix-evidence，2026-08-22）**：`backend/modules/evidence/chain.py::_status_full` 现在返回 `brokenAt`=区块高度（int 或 null）、`brokenAtEvidenceId`=存证 ID（str 或 null），另加 `verifiedAt`。真机实测 `{"height":235,"intact":false,"brokenAt":42,"brokenAtEvidenceId":"ev-000042"}`。
  提醒前端：`EvidenceCenter.vue::normalizeChain` 现在拿到 int 会走 `brokenAtId=null` 分支，断裂块的标红要改读 `brokenAtEvidenceId`（详见 `MSG-fix-evidence-to-fix-ws-001.md`）。

## B-018 P3（说明）`/audit/*` 仅 sys_admin/regulator 可读，其它角色的读操作会计入 R01 风控

- `backend/modules/audit/router.py:17` `_AUDITOR = require_roles("sys_admin","regulator")`。契约 1.6 的权限串里没有 audit 资源，按角色控制合理。
- 但前端铃铛轮询 `/audit/alerts` 曾导致 vpp/subject/grid/edge 登录即产生 high 风险审计日志并累计触发 R01（污染审计数据）。乙方已按角色门控（`frontend/src/stores/user.js:canReadAudit`、`AppLayout.vue:47`、`AppHeader.vue:114`、`router/index.js` 路由 meta.roles）。
- 建议：后端对「前端自发的只读探测」可考虑不计入 R01 计数，或在契约里补一条 `audit:read` 权限串，避免两边靠约定对齐。

## B-019 P3（说明）`energy_subject` / `edge_node` 的资产列表 scope=own，种子数据下为空

- `backend/modules/asset/service.py:268-270` 按 `owner_did` 过滤；subject 登录 `/assets` 表格为空（total=0）。属设计内 RBAC。
- 乙方已在资产中心空态加提示说明（`frontend/src/views/AssetsCenter.vue`），不作为缺陷。

## B-020 ｜中 ｜ 同一 DID 绑定 ECC/RSA 密钥后，原 SM2 密钥无法验签；ECC/RSA 只能生成不能用于验证
- 提出：test-func，2026-08-22，用例 TC-33-06（`qa/func-tests/api_func_test.py::t_key_ecc_breaks_verify`）
- 复现：
  1. `POST /did/register {"subjectType":"edge"}` 得到 DID 与 SM2 私钥（version 1）
  2. `POST /keys {"did":D,"algorithm":"SM2"}`（v2）、`POST /keys {"did":D,"algorithm":"ECC"}`（v3）、`POST /keys {"did":D,"algorithm":"RSA"}`（v4，随后 revoke）
  3. 用 v2 的 SM2 私钥对任意原文签名 → `POST /did/verify`
- 期望：需求 3.3「采用 ECC/RSA 非对称密码机制」，同一 DID 上多把活跃密钥共存时，用其中任意一把活跃密钥的签名都应验签通过（或至少 verify 支持指定 keyId / keyVersion）。
- 实际：`{"valid":false,"keyVersion":3,"reason":"签名与公钥不匹配，原文可能被篡改或签名伪造"}` —— `verify_signature` 只取 `status='active'` 且 `version` 最大的那一把，且只实现了 SM2 验签算法。于是**绑定一把 ECC 密钥就会让该 DID 的所有 SM2 签名认证失效**，而 ECC/RSA 自身又没有验签实现，等于该 DID 再也无法完成可信接入。
- 涉及：`backend/modules/did/service.py` verify 路径（按 version desc 取单把密钥）、`backend/core/gm_crypto.py`（只有 SM2 sign/verify，`generate_keypair_by_algorithm` 能生成 ECC/RSA）。
- 建议：verify 时遍历该 DID 全部 active 密钥择一匹配，或请求体支持 `keyId`；ECC/RSA 补验签实现，否则 `POST /keys` 不应接受这两种算法。
- 契约：2.3 密钥管理 / 2.2 `POST /did/verify`。
- **✅ 已修复（fix-identity，2026-08-22）**：两条建议都做了。
  ① `backend/core/gm_crypto.py`：椭圆曲线点运算改为按曲线参数化（`_Curve` / `_ec_add` / `_ec_mul`），新增 secp256r1 上的 **ECDSA**（`ecdsa_sign` / `ecdsa_verify`，SHA-256）与 **RSA PKCS#1 v1.5 + SHA-256**（`rsa_generate_keypair` / `rsa_sign` / `rsa_verify`，纯 `pow()`，Miller-Rabin 生成素数，零新增依赖）；`generate_keypair_by_algorithm` 不再返回随机串占位，并新增统一入口 `sign_by_algorithm` / `verify_by_algorithm`（算法未知或格式非法一律返回 False，不抛异常）。RSA 取 1024 位、公钥只存模数 n、私钥存 p‖q，使公钥 256 / 签名 256 / 托管密文 576 字符都落在既有列宽内，**不需要改表**。
  ② `backend/modules/did/service.py`：新增 `_active_keys()` / `_match_key()`，`verify_detail` 与 `verify_signature` 改为遍历该 DID 全部 active 密钥逐把尝试，命中即通过；响应回传实际命中的 `keyId` / `keyVersion` / `algorithm`（契约 2.2 的 `valid` / `subjectType` / `status` / `reason` 字段名保持不变）。`sign_with_custody` 按密钥算法选签名算法（优先 SM2），并对存量「随机串占位」的旧 ECC/RSA 密钥做异常降级（返回 None，不 500）。
  回归：`backend/tests/test_did_fix_b020_b013_b023.py`（8 条 B-020 用例）；真机 8021 实测同一 DID 上 SM2 v1 / SM2 v2 / ECC v3 / RSA v4 四把活跃密钥的签名 `valid` 全为 true，改一个字的原文 `valid:false`。

## B-021 ｜中 ｜ WebSocket 未按契约每 5 秒周期推送 `node_status`，只在设备上线时推一次
- 提出：test-func，2026-08-22，用例 TC-311-04 / TC-UI-13
- 复现：`ws://127.0.0.1:8000/ws?token=<admin JWT>` 建连后空闲监听 16 秒。
- 期望：契约 2.13「node_status：节点状态与实时指标，每 5 秒推送」，16 秒内应收到 ≥2 条。
- 实际：0 条。全仓库 `node_status` 的推送点只有 `backend/modules/node/service.py:176`（`POST /nodes/{id}/online` 成功后推一次）；`backend/ws/manager.py` 只声明了消息类型，没有定时任务。本轮 TC-311-02 是靠「用节点托管私钥真实签 nonce 上线」才拿到这条消息，六类消息本身格式正确。
- 影响：首页拓扑 / 节点卡片的实时指标在真后端模式下不会自动刷新，需要前端自己轮询 `GET /nodes`。
- 建议：`ws/manager.py` 起一个 5 秒的后台任务广播 `GET /nodes` 的实时指标，或在文档里把该消息明确降级为事件型并通知前端改轮询。
- 乙方兼容（integration 2026-08-22 复核，`qa/check_backend_live.sh` ws 用例同样 0 条）：`frontend/src/stores/perspective.js:startNodePolling()` 每 15 秒兜底轮询 `GET /nodes`，且只在这 15 秒内没收到过 node_status 时才发请求（mock 模式零额外请求）。
- **✅ 已修复（fix-ws，2026-08-22）**：`backend/ws/manager.py` 新增 5 秒周期广播任务（`NODE_STATUS_INTERVAL` / `broadcast_node_status_once` / `_node_status_loop` / `start_node_status_task` / `stop_node_status_task`，挂在 `register_ws_endpoint` 内的 `@app.on_event("startup"/"shutdown")` 上，没动 main.py 启动逻辑）；`backend/modules/node/service.py` 新增 `tick_node_metrics()`——在库里真实值上做有界随机游走并落库（soc 与 storageOutput 符号耦合，offline 节点不动），保证「推出去的值 == GET /nodes 读到的值」。无 WS 连接时直接返回，不开会话不打库。实测 8014 实例 16 秒收到 4 节点 × 3 轮 = 12 条，payload `{nodeId,status,metrics}` 与契约 2.13 一致。单测 `tests/test_websocket.py::test_B021_*` 共 5 条。前端 `startNodePolling()` 兜底轮询按要求保留未动。

## B-022 ｜低（负载相关）｜ 30 并发登录在机器有其它负载时尾延迟超 5 秒，个别请求 ReadTimeout
- 提出：test-func，2026-08-22，用例 TC-NF-02
- 复现：30 线程同时 `POST /auth/login`（6 个演示账号轮流）。
- 期望：全部 200 且最大耗时 < 5s（顺序调用时 TC-NF-01 主要查询接口 < 2s）。
- 实际：机器上有其它 Agent 同时压测时，最大耗时 4.8s / 5.9s，个别请求 `httpx.ReadTimeout`（30s）；机器空闲时同一用例最大耗时 **2323ms，全部 200，判定通过**。
- 原因：bcrypt 校验为 CPU 密集且 `uvicorn` 单进程单 worker，登录请求在 GIL 上排队，尾延迟对机器负载非常敏感。
- 建议：bcrypt cost 降到 10、把 `verify_password` 放进线程池，或部署时 `--workers N`；演示前避免多人同时登录。
- 契约：无明确性能条款。空闲环境达标，故降为「低（负载相关）」；答辩机若与其它服务混跑，建议 `--workers 2~4` 或把 `verify_password` 放线程池。
- **✅ 已修复（fix-ws，2026-08-22）**：**根因不是 bcrypt**。实测本机 30 线程并发 `verify_password` 总墙钟仅 340ms（bcrypt 4.x 是 Rust 实现，计算期间释放 GIL；且 `/auth/login` 是同步路由，FastAPI 早已把它丢进 anyio 工作线程，从未阻塞事件循环）。真正卡点是 SQLAlchemy 连接池被打满——日志 `QueuePool limit of size 5 overflow 5 reached, connection timed out, timeout 30.00`，因为一个写请求要同时占两条连接（`get_db` 会话 + `@audited` 里 `write_audit_log()` 另开的审计会话）。改动：`backend/core/database.py` `pool_size 5→10`、`max_overflow 5→20`、新增 `pool_timeout=8`（上限 30 条，远低于 MariaDB `max_connections=151`；打满时快速失败而非吊死 30 秒）；`backend/core/security.py` 钉住 `bcrypt__rounds=10`（passlib 默认 12，而 `02_seed.sql` 演示账号哈希是 cost=10，不钉住则新建用户登录慢 4 倍；cost 从哈希串自身读出，旧哈希仍可验），并新增 `verify_password_async()` 供未来的异步鉴权路径使用。TC-NF-02 同写法实测：修复前 30/30 全部 `ReadTimeout`（wall 33349ms），修复后 **30/30 全 200，最大 3543ms / 3446ms < 5000ms 门槛**（其中服务端仅占约 0.6s，其余是测量客户端每次新建 httpx Client 的自身开销）。

## B-023 ｜低 ｜ `createdAt` 与 `updatedAt` 时区口径不一致，同一时刻相差 8 小时
- 提出：test-func，2026-08-22，用例 TC-312-04
- 复现：`POST /did/register` 后 `GET /did/{did}`。
- 期望：两个字段指向同一时刻（契约统一 `+08:00`）。
- 实际：`createdAt=2026-08-22T20:28:04+08:00`、`updatedAt=2026-08-22T12:28:04+08:00`，差 28800 秒。`created_at` 由应用侧 `now_cst()` 写入，`updated_at` 走模型的 `server_default=func.now()`（`backend/modules/did/model.py:23,42` 等多张表同样写法），取的是**数据库服务器**时间；本机 MariaDB 时区为 UTC，于是两者差 8 小时。
- 影响：按 `updatedAt` 排序/筛选会得到错误顺序；部署到 UTC 主机的 Docker 环境同样复现（除非给 db 容器设 `TZ=Asia/Shanghai`）。
- 建议：`updated_at` 也由应用侧写入，或建库时统一 `time_zone='+08:00'` 并在部署文档中写明。
- **✅ 已修复（fix-identity，2026-08-22）**：两条建议都做了（互为保险，无需改表）。
  ① `backend/core/response.py` 新增 `now_naive()`（东八区、秒级、无 tzinfo）；`backend/modules/{did,auth,algo,asset,node,permission,evidence,audit}/model.py` 全部 34 个时间列改为 `default=now_naive`（`updated_at` 另加 `onupdate=now_naive`），`server_default=func.now()` 保留仅用于建表 DDL。
  ② `backend/core/database.py` 加 `connect` 事件，每条 MySQL 连接执行 `SET time_zone='+08:00'`，让 DDL 里的 `CURRENT_TIMESTAMP` / 原生 SQL 的 `NOW()` 也走东八区（SQLite 跳过）。
  回归：`backend/tests/test_did_fix_b020_b013_b023.py` 4 条 B-023 用例（含「不允许再出现只有 server_default 的时间列」的防回归断言）；真机 MariaDB 实测新建 DID `createdAt=updatedAt=2026-08-22T23:45:45+08:00`，8 小时差消失（存量老数据的 `updated_at` 仍是旧值，不回填）。

## B-024 P3 WebSocket 鉴权失败时握手直接 403，而不是先接受再以 close code 4001 关闭

- 提出：integration，2026-08-22（`qa/check_backend_live.sh` ws 用例）
- 复现：`ws://127.0.0.1:8000/ws`（无 token）或 `?token=bad`
- 期望（契约 2.13「鉴权失败立即关闭（code 4001）」）：完成握手后以 4001 关闭
- 实际：HTTP 403 拒绝握手，客户端拿不到 4001，无法区分「鉴权失败」与「网络不可达」，前端只能按通用错误退避重连
- 影响：前端重连策略（`frontend/src/api/ws.js`）无法在 token 失效时立即跳登录；不影响演示
- 建议：`websocket.accept()` 后 `close(code=4001)`
- **✅ 已修复（fix-ws，2026-08-22）**：`backend/ws/manager.py` 两处 `close(code=4001)` 之前补 `await websocket.accept()`（缺 token / 鉴权失败各一处）。前端 `frontend/src/api/ws.js` 的 `socket.onclose` 改为读 `evt.code`，等于 4001（导出常量 `WS_CLOSE_AUTH_FAILED`）时不再退避重连，而是清重连定时器 + `forceLogout()`（清会话并 `router.replace('/login?redirect=...')`），与 `src/api/request.js` 收到 `code 1002` 的处理一致。8014 实测：无 token → `close code=4001 reason='缺少 token'`；错误 token → `close code=4001 reason='鉴权失败'`。单测 `tests/test_websocket.py::test_B024_鉴权失败先完成握手再以4001关闭`。

## B-025 ｜中 ｜ 存证被篡改后 `POST /evidence/demo/restore` 实际不可用，链无法恢复（B-001 的现场后果）

- 现象：联调库中 `ev-000203 / ev-000236 / ev-000272 / ev-001067` 四条存证先后被篡改演示改写，链 `intact=false, brokenAt=ev-001067`。
  对四条逐一调用 `POST /evidence/demo/restore {"evidenceId":"ev-xxxxxx"}` 全部返回 `1005 没有找到 ev-xxxxxx 的原始快照备份，无法自动还原`。
- 期望：篡改演示可逆——每次篡改都应留下可用的原始快照（且不被同一/后续篡改覆盖、不随 Redis 过期而失效）。
- 根因（只读定位，与 B-001 同源）：原始快照存在 Redis，键按 evidenceId 覆盖写且有 TTL；多轮演示或 Redis 清理后备份即丢失。
- 影响：答辩现场若连续演示两次篡改，链会永久停在 broken 状态，`/evidence/chain/status` 一直显示不完整，只能重置数据库恢复。
- 建议：把原始快照写进 MySQL（例如 `chain_evidence.payload_snapshot` 之外单独一列或一张 `chain_evidence_backup` 表），restore 后删除备份；或提供 `POST /evidence/demo/restore-all`。
- 乙方临时对策：演示前用 `DROP DATABASE energy_tds` + 重导 `backend/sql/01_schema.sql`、`02_seed.sql`（约 1 秒）恢复干净且 intact 的链。
- **✅ 已修复（fix-evidence，2026-08-22）**：与 B-001 同一处修复（快照落库 + 不覆盖 + 用后即删）。另补契约外的一键还原 `POST /evidence/demo/restore-all`（`backend/modules/evidence/router.py:119`、`service.py::restore_all`，仅 sys_admin），返回 `{restored,restoredCount,failed,chain{intact,brokenAt,brokenAtEvidenceId}}`。真机实测：同一条存证连续篡改 2 次 → restore → `verification.intact:true`、链 `intact:true / brokenAt:null`；再也不需要重建库。

## B-026 ｜低 ｜ `POST /dispatch/tasks/{id}/ack` 不写存证、不返回 evidenceId
- 提出：test-func，2026-08-22，用例 TC-UI-07 / TC-39-07
- 涉及：`backend/modules/algo/service.py:646-672`（`ack_dispatch` 只更新 `ack_detail`/`ack_status` 并推 WS，没有 `write_evidence`），`router.py:155` 的 `@audited(risk="low")` 也不会触发上链。
- 复现：`/cloud/aggregate` 签名下发 → `/edge/response`「确认执行并回执」→ 回执面板「回执存证」显示 `--`；接口返回 `{"taskId","nodeId","ackStatus","acked","total"}`，无 `evidenceId`。
- 期望：需求 3.6「实现数据接入、授权、联邦任务、模型版本、**调度结果**存证」。下发（issue）已回 `evidenceId`，回执作为调度闭环的最后一环也应上链，前端「回执存证」才有内容；契约 2.10 未定义 ack 响应体，建议补 `evidenceId`。
- 影响：终端响应页的「回执存证」永远为空；调度链路在存证中心查不到 ack 环节。
- 严重度低：不影响下发与执行本身，仅影响存证完整性与演示效果。

## 补记：`docs/测试文档-接口与安全.md` §4 独立编号的三条（本文件原先没有条目）

> 该文档的 §4 缺陷表用了自己的号段，与本文件不连号。这里补三条对应记录，避免查不到修复情况。

### §4 B-001（中）`require_roles` 路径的 1003 拒绝不写审计日志，仅计 R01 —— 用例 API-AUD-01

- 现象：`GET /users`、`/audit/*` 这类用 `require_roles(...)` 保护的接口越权被拒时，按 traceId 查 `/audit/logs` 为空；用权限串（`@require_permission`）保护的接口是写的。违反需求(四)1「所有访问与操作留痕」。
- 根因：`require_roles` 是 FastAPI **依赖**，在被 `@audited` 装饰的业务函数**之前**执行，装饰器根本没机会跑。
- **✅ 已修复（fix-evidence，2026-08-22）**：`backend/core/deps.py::_audit_role_denial()` 在拒绝分支补写审计，字段口径与 `@audited` 的 denied 分支完全一致（`result=denied`、`riskLevel` 经 `_escalate` 抬到 high、高危自动上链），module 由请求路径首段映射（`/users`→auth、`/audit/*`→audit…），action 为 `<module>:<read|write|update|delete>`。依赖先于业务函数执行 ⇒ 同一次拒绝只记一条，不会和 `@audited` 重复。
  真机实测：`vpp GET /users` → 403/1003，`/audit/logs?traceId=…` 命中 1 条 `module=auth, action=auth:read, result=denied, riskLevel=high, actorName=虚拟电厂运营商, evidenceId=ev-000229`。
  回归：`backend/tests/test_evidence_fixes.py::test_B001_require_roles拒绝也写审计日志` / `test_B001_audit接口的越权也留痕` / `test_B001_同一次拒绝只记一条不重复`。

### §4 B-002（中）`energy_subject` 的 `evidence:read` scope=own 未落实 —— 用例 API-EV-17 / 17b

- 现象：能源主体 `GET /evidence` 返回全部 985 条（含他人存证），`GET /evidence/{id}` 能读到 admin 写的存证。DB-SCHEMA 权限矩阵规定该角色是「仅自有」。
- **✅ 已修复（fix-evidence，2026-08-22）**：照搬资产那边已经正确的写法——列表在 SQL 层按 `actor_did` 过滤（`backend/modules/evidence/router.py::search` 用 `has_own_scope_only(principal,"evidence","read")` + `service.search(owner_only=…)`），详情与凭证导出给 `@require_permission` 补上 `resource_id_arg="evidence_id"`，让权限中心用 `_OWNER_SQL["evidence"]` 解析属主。`sys_admin/grid_dispatcher/vpp_operator/regulator`（scope=all）不受影响。
  真机实测：subject 列表 `total=6`，actorDid 全是自己；读 admin 的 `ev-000219` 详情与凭证均 403/1003；admin / regulator 仍是 `total=220`。
  回归：`tests/test_evidence_fixes.py` 的 5 条 B-002 用例（含 4 个 scope=all 角色的参数化反证）。

### §4 B-007（低）`/evidence/chain/status` 全链重算，600 条 P95 2.4s —— 用例 API-PERF-04

- **✅ 已修复（fix-evidence，2026-08-22）**：两级增量校验，**检出能力不打折**。
  ① 结果级：一条纯 SQL 聚合算全表指纹（行数 + 最大高度 + 逐行 `CRC32(payload_hash|prev_hash|block_hash|created_at|payload_snapshot)` 的普通和与按高度加权和），指纹没变才复用上次的**全量校验结论**；任何一行被改（篡改演示就是直接改库）指纹必变，立刻回到全量校验。
  ② 逐块级：记住「高度 H 的这组 (payload 的 SHA-256 摘要, payload_hash, prev_hash, block_hash, ts) 已通过 SM3 校验」，五元组完全一致才跳过两次 SM3；篡改的那一行必然 memo miss，必然重新走完整校验。
  单条 `/evidence/verify` 永不走缓存。`backend/modules/evidence/chain.py:93-190`。
  真机实测（链长 236）：全量重算 470ms → 链未变 **0.7ms**、链有变（新块/篡改）**12.3ms**，指纹 SQL 单次 0.65ms；不再随链长线性增长。
  回归：`tests/test_evidence_fixes.py::test_PERF04_*` 三条（含「篡改照样被检出并定位到高度」）。
- **✅ 已修复（fix-algo，2026-08-22）**：`modules/algo/service.py::ack_dispatch` 补 `write_evidence(category="algo", action="dispatch:ack")` 并在响应里返回 `evidenceId`，WS 的 `dispatch_progress` 也带上；同时补了目标节点校验（见 B-008）。实测 `POST /dispatch/tasks/dp-000004/ack {"nodeId":"Node-A"}` → `{"ackStatus":"partial","acked":1,"total":3,"evidenceId":"ev-000226"}`。回归：`tests/test_fix_algo.py::test_B026_回执上链并返回evidenceId`。

---

## 本轮由 fix-algo 一并修复的、原记录在《测试文档-接口与安全》§4 的三条

- **B-003 `POST /permissions/apply` 接受过去的 expireAt（用例 API-PERM-13）**
  **✅ 已修复（fix-algo，2026-08-22）**：`modules/permission/service.py::_parse_expire` 增加「必须晚于当前时间」校验，返回 1001。实测 `expireAt=2020-01-01T00:00:00+08:00` → `{"code":1001,"message":"expireAt 必须晚于当前时间：2020-01-01T00:00:00+08:00"}`。回归：`test_B003_申请过去的expireAt返回1001` / `test_B003_合法的将来expireAt正常受理`。
- **B-004 托管代签时 `algo_dispatch_task.signature` 为 NULL（用例 API-DP-18b）**
  **✅ 已修复（fix-algo，2026-08-22）**：`core/middleware.py::_verify_signature` 把托管私钥代签出来的签名回写进请求体（新增 `_write_back`），`issue_dispatch` 照常落库；`_dispatch_to_item` 增加 `signature` 字段回传（B-016 的建议一并落地，前端 `TerminalResponse.vue:254-268` 的真验签分支现在能跑通）。实测 dp-000004 详情返回 64 字节 SM2 签名 `245c3b59…fee522`，与 `signPayload` 离线验签通过。回归：`test_B004_托管代签的签名落库且详情回传`。
- **B-008 `/dispatch/tasks/{id}/ack` 接受非目标节点回执（用例 API-DP-14）**
  **✅ 已修复（fix-algo，2026-08-22）**：`ack_dispatch` 先按策略 actions（退化时按 nodeIds）算出本次下发的目标集合，不在集合内返回 1001（与用例期望的「400/1001 或 404/1005」一致；语义上是请求参数不合法，故选 1001）。实测 `nodeId=Node-Z` → `{"code":1001,"message":"节点 Node-Z 不在本次下发的目标节点内（Node-A、Node-C、Node-D），拒绝回执"}`，且该拒绝同样落在任务的 traceId 链上。回归：`test_B008_非目标节点回执被拒`。

---

## 修复轮之后发现的两条（2026-08-22 晚 / 2026-08-23）

## B-027 ｜严重 ｜ 同一请求内业务会话与审计会话争链尾行锁，回收授权接口挂 200 秒（修复轮引入的回归）
- 现象：`POST /permissions/grants/{id}/revoke` 200 s 后才返回（客户端早已超时）。日志 3 × `Lock wait timeout exceeded`（默认 50 s）。
- 根因：B-010 的修法让业务会话在 `write_evidence` 时对链尾 `SELECT … FOR UPDATE` 持锁到请求结束；`@audited` 的 `write_audit_log` 另开会话、另开连接再写链（高危审计自动上链）→ 自己等自己。
- **✅ 已修复（主 Agent，2026-08-22，提交 `b6fb566`）**：`@audited` 把请求会话透传 `write_audit_log(..., request_db=)`，审计存证与业务同连接写入并提交；`chain._write_once` 在 MySQL 上设会话级 `innodb_lock_wait_timeout=3`。实测 200 s → 35 ms。复测见 `docs/测试文档-接口与安全.md` §4.2 B-013（该文档自己的号段）。

## B-028 ｜中 ｜ R03 高频权限变更告警丢失、驳回/回收请求多挂 9 秒、Redis 去重标记提前种下导致静默 10 分钟
- 现象：全量复测 API-AUD-23 失败（5 次申请+驳回后无 R03）。后端日志 02:58:06 INSERT `al-000061` → 02:58:15 打「【风控告警】R03」，但 `audit_alert` 无此行、id 67 空洞，`al-000061` 随后被一条 R01 复用。
- 根因：B-027 同族、另一条路径。`_log_change → fire_perm_change → _create_alert` 另开会话上链，而请求事务刚 `write_evidence` 过、链尾行锁在手 → 3 s × 3 次重试 → 上链失败，重试中的 rollback 把同一会话刚 flush 的告警行一起回滚；`risk:alerted:R03:<did>` 在落库前已种下（TTL 600 s）→ 该主体 R03 静默 10 分钟。
- **✅ 已修复（主 Agent，2026-08-23，提交 `7edd38c`）**：`rules.fire(..., db=)` 透传请求会话，告警行与存证块同连接、不自行 commit（保住 `run_with_retry` 整段重放语义）；去重标记改为 `_create_alert` 成功后再种。`permission/service._log_change` 传 `db`。新增单测 3 条，backend pytest 337/337。对应《测试文档-接口与安全》§4.2 B-014、《测试文档》§6.4 B-014。

## B-029 ｜中 ｜ 联邦学习 anomaly / error 不落库不返回；R05 告警所有任务共用一个去重桶
- 现象：用户现场任务 fl-000070（单节点 + DP ε=0.6）loss 10 轮发散到 9×10⁷ 仍报 success（算法侧缺陷，已在同一提交修：裁剪封顶 + 发散熔断）。修好算法后复验 fl-000071：算法服务返回 `status=failed, anomaly=training_diverged, error=…`，但后端 `GET /fl/tasks/{id}` 里 anomaly/error 为空，也没有 R05 告警。
- 根因：`persist_fl_progress` 只把 `job.anomaly` 送进 `fire_suspicious_gradient` 就丢掉，`algo_fl_task` 没有列存它；R05 触发时 `actor_did=None`，`rules.fire` 的去重主体退化为 `anonymous`，10 分钟内第二个异常任务（本次被 12:23 的 fl-000068 压住）永远不告警。
- **✅ 已修复（主 Agent，2026-08-23，提交 `827398a`）**：`algo_fl_task` 新增 `anomaly JSON`、`error VARCHAR(512)`（`01_schema.sql` 已含，存量库执行 `sql/04_migrate_20260823.sql`）；`persist_fl_progress` 落库并只在异常首次出现时触发 R05（轮询重复送同一异常不再反复进风控）；`rules.fire(dedup_key=)`，R05 以 `fl:<taskId>` 去重、挂创建者名下、消息按 投毒/预算耗尽/训练发散 措辞。单测 `test_B029_…`，backend pytest 338/338；前端失败任务显示 `error`。

## B-030 ｜中 ｜ 风控告警 alert_id 用 COUNT(*)+1 拼接，并发下撞唯一键导致告警丢失
- 现象：24 路并发写压测（`qa/api-tests/lock_stress.py`）中，两个并发告警读到相同 `COUNT(*)` → 拼出同一个 `al-000099` → `1062 Duplicate entry` → 告警被 `fire()` 吞掉丢失（实测 10 次）。
- **✅ 已修复（主 Agent，2026-08-23，提交 `3e3acb1`）**：`_insert_alert` 改为先用 uuid 临时占位插入拿到行自增主键、再回填成 `al-<id>`（`alert_id` 是 NOT NULL UNIQUE），与 COUNT 无关，天然不撞。新增 `test_B030_…`；干净单后端 24 路 × 70s 压测 alert_id 重复 0、未捕获异常 0、无自锁离群（最大 1197ms）。

## B-031 ｜中 ｜ 发布模型接口后端门控用 model:read，比前端 algo:execute 松，可越权发布
- 现象：`POST /fl/models/{version}/publish` 后端 `@require_permission("model","read")`，而前端「发布」按钮是 `v-permission="'algo:execute'"`（admin/grid）。持 model:read 的 vpp/regulator/edge 前端看不到按钮，但**直接调接口就能发布模型**（high 风险写操作），绕过前端裁剪。
- **✅ 已修复（主 Agent，2026-08-23，提交见下）**：后端改 `@require_permission("algo","execute")`，与前端及「创建/启动联邦学习」一致。新增 `test_发布模型需要algo_execute权限`（vpp/subject/regulator/edge → 1003，grid 通过）。实测 vpp/regulator/edge publish → 1003，grid/admin → 过权限。契约只列该接口未规定权限，本修复不违反契约。手册 §3.3 同步（发布模型门控标注 algo:execute）。
