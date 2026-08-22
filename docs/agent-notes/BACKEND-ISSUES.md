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
