# 后端（甲方 backend/）问题记录

> 只记录不改。格式：编号、严重级别、复现步骤、期望 vs 实际、涉及文件:行号、契约章节。

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
