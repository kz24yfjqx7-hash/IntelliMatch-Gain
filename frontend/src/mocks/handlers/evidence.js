/** 区块链可信存证（契约 §2.6） */
import { http } from 'msw'
import { db, chain } from '../db.js'
import { toEvidenceDto } from '../chain.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, requirePerm, writeAudit, writeEvidence, MockError, now, inRange } from '../helpers.js'

export const evidenceHandlers = [
  http.get(`${BASE}/evidence/chain/status`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    const s = chain.status()
    return ok({ ...s, algorithm: 'SM3(mock: SHA-256)', tamperedIds: Array.from(chain.tampered.keys()) }, traceId)
  })),

  http.get(`${BASE}/evidence/trace/:traceId`, handle(async ({ request, params, traceId }) => {
    requireAuth(request)
    const tid = params.traceId
    const evs = chain.blocks.filter(b => b.trace_id === tid).map(toEvidenceDto)
    const logs = db.auditLogs.filter(l => l.traceId === tid)
    if (!evs.length && !logs.length) throw new MockError(1005, '该 traceId 下无存证或审计记录')
    const steps = [
      ...logs.map(l => ({ kind: 'audit', at: l.at, module: l.module, action: l.action, result: l.result, actorDid: l.actorDid, evidenceId: l.evidenceId, detail: l.detail })),
      ...evs.map(e => ({ kind: 'evidence', at: e.timestamp, module: 'evidence', action: `evidence:${e.category}`, result: 'success', actorDid: e.actorDid, evidenceId: e.evidenceId, blockHeight: e.blockHeight, hash: e.hash }))
    ].sort((a, b) => (a.at < b.at ? -1 : 1)).map((s, i) => ({ seq: i + 1, ...s }))
    return ok({ traceId: tid, evidences: evs, steps, total: steps.length }, traceId)
  })),

  http.post(`${BASE}/evidence/verify`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const { evidenceId, payload } = await body(request)
    if (!evidenceId) throw new MockError(1001, 'evidenceId 必填')
    const r = chain.verify(evidenceId, payload)
    if (!r) throw new MockError(1005, '存证不存在')
    writeAudit({ traceId, user, module: 'evidence', action: 'evidence:verify', resourceType: 'evidence', resourceId: evidenceId, result: r.intact ? 'success' : 'failed', riskLevel: r.intact ? 'low' : 'high', detail: r.message })
    return ok({ evidenceId, ...r }, traceId)
  })),

  http.post(`${BASE}/evidence/demo/tamper`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    if (!user.roles.includes('sys_admin')) {
      requirePerm(user, 'evidence:tamper', { request, traceId, resourceType: 'evidence', detail: '非管理员调用篡改演示接口' })
    }
    const { evidenceId, newValue } = await body(request)
    const rec = chain.tamper(evidenceId, newValue ?? { tampered: true }, now())
    if (!rec) throw new MockError(1005, '存证不存在')
    // 若是资产存证，同步篡改资产 payload（模拟数据库被改）
    const asset = db.assets.find(a => a.evidenceId === evidenceId)
    if (asset) asset.payload = rec.payload_snapshot
    writeAudit({ traceId, user, module: 'evidence', action: 'evidence:tamper', resourceType: 'evidence', resourceId: evidenceId, riskLevel: 'critical', detail: '【演示】篡改本地存证数据，链上摘要未变' })
    return ok({ evidenceId, tampered: true, blockHeight: rec.block_height, hint: '请调用 /evidence/verify 或 /evidence/chain/status 查看校验结果' }, traceId)
  })),

  http.post(`${BASE}/evidence`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const data = await body(request)
    if (!['data', 'identity', 'permission', 'audit', 'algo'].includes(data.category)) throw new MockError(1001, 'category 非法')
    if (!data.refId) throw new MockError(1001, 'refId 必填')
    const ev = writeEvidence({ category: data.category, refId: data.refId, payload: data.payload || {}, actorDid: data.actorDid || user.did, traceId })
    writeAudit({ traceId, user, module: 'evidence', action: 'evidence:write', resourceType: 'evidence', resourceId: ev.evidence_id, detail: `写入 ${data.category} 存证（ref ${data.refId}）`, evidenceId: ev.evidence_id })
    return ok({ evidenceId: ev.evidence_id, hash: ev.payload_hash, blockHeight: ev.block_height, txId: ev.tx_id, prevHash: ev.prev_hash, timestamp: ev.created_at }, traceId)
  })),

  http.get(`${BASE}/evidence`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'evidence:read', { request, traceId })
    const q = query(request)
    let list = chain.blocks.slice().reverse()
    if (q.category) list = list.filter(b => b.category === q.category)
    if (q.did) list = list.filter(b => b.actor_did === q.did || b.ref_id === q.did)
    if (q.dataType) list = list.filter(b => b.payload_snapshot?.dataType === q.dataType)
    if (q.refId) list = list.filter(b => b.ref_id === String(q.refId))
    if (q.traceId) list = list.filter(b => b.trace_id === q.traceId)
    if (q.keyword) list = list.filter(b => b.evidence_id.includes(q.keyword) || b.ref_id.includes(q.keyword) || b.payload_hash.includes(q.keyword) || b.tx_id.includes(q.keyword))
    if (q.from || q.to) list = list.filter(b => inRange(b.created_at, q.from, q.to))
    const page = paginate(list, q)
    return ok({ ...page, items: page.items.map(b => ({ ...toEvidenceDto(b), payload: undefined, tampered: chain.tampered.has(b.evidence_id) })) }, traceId)
  })),

  http.get(`${BASE}/evidence/:id/certificate`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    const rec = chain.get(params.id)
    if (!rec) throw new MockError(1005, '存证不存在')
    const v = chain.verify(rec.evidence_id)
    writeAudit({ traceId, user, module: 'evidence', action: 'evidence:certificate', resourceType: 'evidence', resourceId: rec.evidence_id, detail: '导出存证凭证' })
    return ok({
      certificateId: `cert-${rec.evidence_id}`, issuedAt: now(), issuer: '能源可信数据空间平台 · 本地哈希链',
      evidence: toEvidenceDto(rec), integrity: { intact: v.intact, checkedAt: now() },
      signature: 'sm2:' + rec.block_hash.slice(4, 68)
    }, traceId)
  })),

  http.get(`${BASE}/evidence/:id`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'evidence:read', { request, traceId })
    const rec = chain.get(params.id)
    if (!rec) throw new MockError(1005, '存证不存在')
    return ok({ ...toEvidenceDto(rec), tampered: chain.tampered.has(rec.evidence_id) }, traceId)
  }))
]
