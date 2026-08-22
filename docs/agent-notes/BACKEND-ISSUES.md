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

## B-002 备注（非缺陷）后端探活端点为根路径 `GET /health`

- `backend/main.py:171`，不走 `/api/v1` 包装；`/api/v1/health` 返回 404 的 `code 1005`。乙方 compose / install.sh / nginx 已按 `/health` 适配，手册已写明。

## B-010 ｜中 ｜ 并发写存证时死锁/锁等待，失败后存证被静默丢弃，高危日志缺 evidence_id
- 复现：多个 Agent 并发调用（FL 轮次上链 + 审计上链 + 业务上链）时，`scratchpad/backend.log` 出现 `存证上链失败 …(1213, 'Deadlock found')` 132 次、`(1205, 'Lock wait timeout')` 2 次；`audit_log_202608` 中 `id>160` 的 406 条 high 日志有 6 条 `evidence_id IS NULL`（如 id 1609/1610 login failed）。
- 期望：文档(四)5「审计日志摘要自动推送至存证模块上链，避免删除篡改」为硬要求；`chain.write` 的 `SELECT … FOR UPDATE` 尾块行锁与 `uk_height` 在并发下应重试（死锁/锁超时重试 2~3 次）或串行化写链队列，失败应告警而非仅打日志。
- 用例：API-AUD-01b / API-AUD-21 在并发高峰期间间歇失败（单独运行通过）。

## B-011 ｜中 ｜ `algo_fl_round.loss` 为 `DECIMAL(10,6)`，算法返回 loss ≥ 10000 时整轮落库与上链失败
- 复现：日志 `落库联邦学习进度失败 fl-000011：(1264, "Out of range value for column 'loss'")` 562 次，同一任务每轮重复报错；对应 `存证上链失败 category=algo`。乙方 algo-service 的负荷预测 loss 为 kW² 量级的 MSE，可能超过 9999.999999。
- 期望：列改为 `DOUBLE` 或 `DECIMAL(18,6)`，且单轮落库失败不应阻断后续轮次；建议同时要求乙方对 loss 做归一化（已在 MSG-test-api-to-integration-001 提醒）。
- 影响：前端收敛曲线缺轮、`rounds[].evidenceId` 缺失，违反「每轮梯度哈希上链」。

## B-012 ｜低 ｜ 并发审批/驳回同一申请时 500（StaleDataError）
- 复现：日志 6 次 `sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table 'perm_application' expected to update 1 row(s); 0 were matched.`，由两个客户端几乎同时 approve/reject 同一 pending 申请触发，响应为 500/5000。
- 期望：加行锁或 `WHERE status='pending'` 的条件更新，后到者返回 409/1006。

## B-013 P2 `POST /did/{did}/rotate-key` 强制要求请求体，契约未定义该接口的 body

- 提出：integration，2026-08-22
- 涉及：`backend/modules/did/schema.py:24`（`DidRotateKeyRequest`）、`backend/modules/did/router.py:83-88`（`body: DidRotateKeyRequest` 为必填参数）
- 复现：`curl -X POST http://127.0.0.1:8000/api/v1/did/<did>/rotate-key -H "Authorization: Bearer <admin>"`（无 body）
- 期望（契约 2.2 表格「POST /did/{did}/rotate-key 密钥轮换」，未定义请求体，无 body 应可轮换）：`code 0`，返回新公私钥与 version
- 实际：`{"code":1001,"message":"参数错误： Field required"}`（HTTP 400），身份中心「轮换密钥」按钮在真后端 400
- 影响页面：`/identity`（密钥轮换）
- 建议：`body: DidRotateKeyRequest = DidRotateKeyRequest()` 给默认值。乙方已兼容（`frontend/src/api/did.js:16-18` 固定发 `{}`），两端都可用，无需回归。

## B-014 P1 `POST /assets`、`POST /risk/assess` 偶发 500，且 traceId 退化为 `tr-00000000-00000000`

- 提出：integration，2026-08-22（Playwright 全页面巡检，admin `/edge/privacy`、edge `/edge/privacy`）
- 复现：多 Agent 并发操作时提交资产登记 / 风险评估；`scratchpad/backend.log` 对应 `sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table 'algo_risk_assessment' expected to update 1 row(s); 0 were matched.`
- 期望（契约 1.1 code 语义 + 1.2 traceId 由 backend 生成、格式 `tr-YYYYMMDD-<8hex>`）：并发下要么成功，要么返回 1006；异常响应的 traceId 仍应是真实 traceId
- 实际：`{"code":5000,"message":"服务器内部错误","data":null,"traceId":"tr-00000000-00000000"}`；该 traceId 无法用于 `/audit/trace/{traceId}` 追踪，违背「任务级全流程追踪」
- 影响页面：`/assets`（登记）、`/edge/risk`、`/edge/privacy`
- 建议：与 B-010/B-012 同源（乐观锁/行锁冲突），统一加重试或条件更新；异常处理分支里沿用请求入口生成的 traceId，不要回落到全零串

## B-015 P2 `/audit/trace/{traceId}` 对 FL / 调度链路只返回 1 步，缺少 login→did:verify→permission:check→evidence:write 的完整链

- 提出：integration，2026-08-22
- 复现：`GET /fl/tasks?size=1` 取 traceId（如 `tr-20260822-e942207b`）→ `GET /audit/trace/tr-20260822-e942207b`
- 期望（契约 2.7 示例）：`steps` 为 auth/did/permission/algo/evidence 的多步链，`steps[].evidenceId` 非空
- 实际：`steps` 仅 1 条 `{module:"algo",action:"fl:train",evidenceId:null}`，`summary.durationMs=0`
- 影响页面：`/audit` 全流程追踪时间轴（演示第 10 步说服力下降）；乙方 E2E 已把断言从 ≥3 步放宽到 ≥1 步（`frontend/e2e/10-audit.spec.js:31`），后端修复后可改回
- 建议：登录、权限校验、每轮上链的审计记录复用同一 traceId（契约 1.2「该请求产生的所有审计日志、存证记录共用同一 traceId」）

## B-016 P3（说明，非缺陷）调度任务下发/回执后 `status` 仍为 `success`，且详情不回传原始签名

- 后端用 `issued/commandId/issuedAt/ackStatus/ackDetail[]` 表达下发与回执，`status` 保持契约 1.6 的 `taskStatus` 枚举——**符合契约**，是 MSW 桩多造了 `issued`/`acked` 两个非契约状态。
- 乙方已在 `frontend/src/api/dispatch.js:normalizeDispatchTask` 做归一化，两种模式都可用。
- 另：`GET /dispatch/tasks/{id}` 回传 `signPayload` 但不回传 `signature`，边端「DID 签名校验」无法复核签名字节，前端已降级为「按 DID 文档状态判定」并如实标注（`frontend/src/views/TerminalResponse.vue:254-268`）。如后端能回传 `signature`（脱敏无碍，它本就是公开可验证数据），边端即可做真验签，建议补上。

## B-017 P3 `/evidence/chain/status` 的 `brokenAt` 返回 evidenceId 字符串，契约示例为高度/`null`

- 复现：`GET /api/v1/evidence/chain/status` → `{"height":406,"intact":false,"brokenAt":"ev-000203"}`
- 期望（契约 2.6 `{"height":128,"lastHash":"...","intact":true,"brokenAt":null,...}`，与 `height` 同名词义，应为区块高度或 null）
- 实际：字符串 evidenceId；前端按高度比较「受影响区块」会失效
- 影响页面：`/evidence`（断裂点标红 / 受影响块）。乙方已兼容：拿到字符串时回查该存证的 `blockHeight`（`frontend/src/views/EvidenceCenter.vue:normalizeChain`），并保留 `brokenAtId` 展示
- 建议：`brokenAt` 用高度，另加 `brokenAtEvidenceId` 字符串字段

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

## B-021 ｜中 ｜ WebSocket 未按契约每 5 秒周期推送 `node_status`，只在设备上线时推一次
- 提出：test-func，2026-08-22，用例 TC-311-04 / TC-UI-13
- 复现：`ws://127.0.0.1:8000/ws?token=<admin JWT>` 建连后空闲监听 16 秒。
- 期望：契约 2.13「node_status：节点状态与实时指标，每 5 秒推送」，16 秒内应收到 ≥2 条。
- 实际：0 条。全仓库 `node_status` 的推送点只有 `backend/modules/node/service.py:176`（`POST /nodes/{id}/online` 成功后推一次）；`backend/ws/manager.py` 只声明了消息类型，没有定时任务。本轮 TC-311-02 是靠「用节点托管私钥真实签 nonce 上线」才拿到这条消息，六类消息本身格式正确。
- 影响：首页拓扑 / 节点卡片的实时指标在真后端模式下不会自动刷新，需要前端自己轮询 `GET /nodes`。
- 建议：`ws/manager.py` 起一个 5 秒的后台任务广播 `GET /nodes` 的实时指标，或在文档里把该消息明确降级为事件型并通知前端改轮询。
- 乙方兼容（integration 2026-08-22 复核，`qa/check_backend_live.sh` ws 用例同样 0 条）：`frontend/src/stores/perspective.js:startNodePolling()` 每 15 秒兜底轮询 `GET /nodes`，且只在这 15 秒内没收到过 node_status 时才发请求（mock 模式零额外请求）。

## B-022 ｜低（负载相关）｜ 30 并发登录在机器有其它负载时尾延迟超 5 秒，个别请求 ReadTimeout
- 提出：test-func，2026-08-22，用例 TC-NF-02
- 复现：30 线程同时 `POST /auth/login`（6 个演示账号轮流）。
- 期望：全部 200 且最大耗时 < 5s（顺序调用时 TC-NF-01 主要查询接口 < 2s）。
- 实际：机器上有其它 Agent 同时压测时，最大耗时 4.8s / 5.9s，个别请求 `httpx.ReadTimeout`（30s）；机器空闲时同一用例最大耗时 **2323ms，全部 200，判定通过**。
- 原因：bcrypt 校验为 CPU 密集且 `uvicorn` 单进程单 worker，登录请求在 GIL 上排队，尾延迟对机器负载非常敏感。
- 建议：bcrypt cost 降到 10、把 `verify_password` 放进线程池，或部署时 `--workers N`；演示前避免多人同时登录。
- 契约：无明确性能条款。空闲环境达标，故降为「低（负载相关）」；答辩机若与其它服务混跑，建议 `--workers 2~4` 或把 `verify_password` 放线程池。

## B-023 ｜低 ｜ `createdAt` 与 `updatedAt` 时区口径不一致，同一时刻相差 8 小时
- 提出：test-func，2026-08-22，用例 TC-312-04
- 复现：`POST /did/register` 后 `GET /did/{did}`。
- 期望：两个字段指向同一时刻（契约统一 `+08:00`）。
- 实际：`createdAt=2026-08-22T20:28:04+08:00`、`updatedAt=2026-08-22T12:28:04+08:00`，差 28800 秒。`created_at` 由应用侧 `now_cst()` 写入，`updated_at` 走模型的 `server_default=func.now()`（`backend/modules/did/model.py:23,42` 等多张表同样写法），取的是**数据库服务器**时间；本机 MariaDB 时区为 UTC，于是两者差 8 小时。
- 影响：按 `updatedAt` 排序/筛选会得到错误顺序；部署到 UTC 主机的 Docker 环境同样复现（除非给 db 容器设 `TZ=Asia/Shanghai`）。
- 建议：`updated_at` 也由应用侧写入，或建库时统一 `time_zone='+08:00'` 并在部署文档中写明。

## B-024 P3 WebSocket 鉴权失败时握手直接 403，而不是先接受再以 close code 4001 关闭

- 提出：integration，2026-08-22（`qa/check_backend_live.sh` ws 用例）
- 复现：`ws://127.0.0.1:8000/ws`（无 token）或 `?token=bad`
- 期望（契约 2.13「鉴权失败立即关闭（code 4001）」）：完成握手后以 4001 关闭
- 实际：HTTP 403 拒绝握手，客户端拿不到 4001，无法区分「鉴权失败」与「网络不可达」，前端只能按通用错误退避重连
- 影响：前端重连策略（`frontend/src/api/ws.js`）无法在 token 失效时立即跳登录；不影响演示
- 建议：`websocket.accept()` 后 `close(code=4001)`

## B-025 ｜中 ｜ 存证被篡改后 `POST /evidence/demo/restore` 实际不可用，链无法恢复（B-001 的现场后果）

- 现象：联调库中 `ev-000203 / ev-000236 / ev-000272 / ev-001067` 四条存证先后被篡改演示改写，链 `intact=false, brokenAt=ev-001067`。
  对四条逐一调用 `POST /evidence/demo/restore {"evidenceId":"ev-xxxxxx"}` 全部返回 `1005 没有找到 ev-xxxxxx 的原始快照备份，无法自动还原`。
- 期望：篡改演示可逆——每次篡改都应留下可用的原始快照（且不被同一/后续篡改覆盖、不随 Redis 过期而失效）。
- 根因（只读定位，与 B-001 同源）：原始快照存在 Redis，键按 evidenceId 覆盖写且有 TTL；多轮演示或 Redis 清理后备份即丢失。
- 影响：答辩现场若连续演示两次篡改，链会永久停在 broken 状态，`/evidence/chain/status` 一直显示不完整，只能重置数据库恢复。
- 建议：把原始快照写进 MySQL（例如 `chain_evidence.payload_snapshot` 之外单独一列或一张 `chain_evidence_backup` 表），restore 后删除备份；或提供 `POST /evidence/demo/restore-all`。
- 乙方临时对策：演示前用 `DROP DATABASE energy_tds` + 重导 `backend/sql/01_schema.sql`、`02_seed.sql`（约 1 秒）恢复干净且 intact 的链。

## B-026 ｜低 ｜ `POST /dispatch/tasks/{id}/ack` 不写存证、不返回 evidenceId
- 提出：test-func，2026-08-22，用例 TC-UI-07 / TC-39-07
- 涉及：`backend/modules/algo/service.py:646-672`（`ack_dispatch` 只更新 `ack_detail`/`ack_status` 并推 WS，没有 `write_evidence`），`router.py:155` 的 `@audited(risk="low")` 也不会触发上链。
- 复现：`/cloud/aggregate` 签名下发 → `/edge/response`「确认执行并回执」→ 回执面板「回执存证」显示 `--`；接口返回 `{"taskId","nodeId","ackStatus","acked","total"}`，无 `evidenceId`。
- 期望：需求 3.6「实现数据接入、授权、联邦任务、模型版本、**调度结果**存证」。下发（issue）已回 `evidenceId`，回执作为调度闭环的最后一环也应上链，前端「回执存证」才有内容；契约 2.10 未定义 ack 响应体，建议补 `evidenceId`。
- 影响：终端响应页的「回执存证」永远为空；调度链路在存证中心查不到 ack 环节。
- 严重度低：不影响下发与执行本身，仅影响存证完整性与演示效果。
