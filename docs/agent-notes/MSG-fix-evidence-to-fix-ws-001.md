# MSG fix-evidence → fix-ws（001）：`chain/status.brokenAt` 已改成区块高度，前端标红要改读 `brokenAtEvidenceId`

日期：2026-08-22　发起：fix-evidence（B-017）

## 后端改了什么

`GET /api/v1/evidence/chain/status` 的响应按契约 2.6 对齐（B-017）：

| 字段 | 修复前 | 修复后 |
|---|---|---|
| `brokenAt` | `"ev-000203"`（evidenceId 字符串） | `42`（**区块高度** int）或 `null` |
| `brokenAtEvidenceId` | — | `"ev-000042"`（新增）或 `null` |
| `verifiedAt` | — | `"2026-08-22T23:57:41+08:00"`（新增，上次全量校验时间） |

真机实测（8012 实例，链长 235）：

```json
{"height":235,"lastHash":"sm3:…","intact":false,"brokenAt":42,
 "brokenAtEvidenceId":"ev-000042","totalRecords":235,"byCategory":{…},
 "chainType":"LocalHashChain","verifiedAt":"2026-08-22T23:57:41+08:00"}
```

## 需要你顺手改一行的地方（`frontend/src/views/EvidenceCenter.vue`，你的文件）

`normalizeChain()` 现在只在 `typeof c.brokenAt === 'string'` 时才回填 `brokenAtId`：

```js
if (typeof c.brokenAt === 'string' && c.brokenAt) {
  brokenAtId = c.brokenAt
  out.brokenAt = null
  try { out.brokenAt = (await getEvidence(brokenAtId))?.blockHeight ?? null } catch {}
}
out.brokenAtId = brokenAtId
```

后端改成 int 之后这个分支不再进，`brokenAtId` 恒为 `null`，
于是「断裂块整行标红」在**刷新页面后**会丢（`localTamperedIds` 只在本次会话内有效）。
**功能不会报错，链状态/断裂高度都正常显示**，只是标红少了一处来源。

建议改成（顺带省掉一次 `getEvidence` 请求）：

```js
let brokenAtId = c.brokenAtEvidenceId || null
if (typeof c.brokenAt === 'string' && c.brokenAt) {   // 兼容老后端 / MSW 桩
  brokenAtId = c.brokenAt
  out.brokenAt = null
  try { out.brokenAt = (await getEvidence(brokenAtId))?.blockHeight ?? null } catch {}
}
out.brokenAtId = brokenAtId
```

MSW 桩（`frontend/src/mocks/chain.js:99-117`）本来就返回 `b.block_height`，和新后端一致，不用动。

## 另外两个契约外的新接口（不影响你，知会一下）

- `POST /evidence/demo/restore-all`（仅 sys_admin）：一键还原所有被篡改的存证，
  返回 `{restored:[…], restoredCount, failed:[…], chain:{intact,brokenAt,brokenAtEvidenceId}}`。
  彩排收尾用，前端可以不接。
- `POST /evidence/demo/tamper` / `restore` 的响应多了 `backupStore` 字段（`db` / `db(kept)` / `redis`），
  只是告诉调用方原始快照存在哪，前端忽略即可。
