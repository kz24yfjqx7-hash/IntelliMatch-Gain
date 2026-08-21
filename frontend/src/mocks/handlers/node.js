/** 节点与拓扑（契约 §2.8） */
import { http } from 'msw'
import { db } from '../db.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, writeAudit, writeEvidence, raiseAlert, MockError, now, inRange } from '../helpers.js'
import { mockVerifySignature } from './did.js'
import { sha256Hex } from '../../utils/sha256.js'

function nodeDto(n) {
  return { id: n.id, name: n.name, status: n.status, model: n.model, did: n.did, didStatus: n.didStatus, metrics: { ...n.metrics }, lastSeenAt: n.lastSeenAt, location: n.location }
}

export const nodeHandlers = [
  http.get(`${BASE}/nodes`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    const q = query(request)
    let list = db.nodes.slice()
    if (q.status) list = list.filter(n => n.status === q.status)
    const page = paginate(list, q)
    return ok({ ...page, items: page.items.map(nodeDto) }, traceId)
  })),

  http.get(`${BASE}/nodes/:id`, handle(async ({ request, params, traceId }) => {
    requireAuth(request)
    const n = db.nodes.find(x => x.id === params.id)
    if (!n) throw new MockError(1005, '节点不存在')
    const didRec = db.dids.find(d => d.did === n.did)
    return ok({ ...nodeDto(n), didDocument: didRec?.didDocument || null, assetsCount: db.assets.filter(a => a.sourceDid === n.did).length }, traceId)
  })),

  http.get(`${BASE}/nodes/:id/metrics`, handle(async ({ request, params, traceId }) => {
    requireAuth(request)
    const n = db.nodes.find(x => x.id === params.id)
    if (!n) throw new MockError(1005, '节点不存在')
    const q = query(request)
    let list = db.nodeMetrics[n.id] || []
    if (q.from || q.to) list = list.filter(m => inRange(m.ts, q.from, q.to))
    else list = list.slice(-24 * 7) // 默认最近 7 天
    const interval = q.interval || 'hour'
    if (interval === 'day') {
      const groups = new Map()
      for (const m of list) {
        const day = m.ts.slice(0, 10)
        if (!groups.has(day)) groups.set(day, { ts: `${day}T00:00:00+08:00`, pvOutput: 0, storageOutput: 0, load: 0, soc: 0, n: 0 })
        const g = groups.get(day)
        g.pvOutput += m.pvOutput; g.storageOutput += m.storageOutput; g.load += m.load; g.soc += m.soc; g.n++
      }
      list = Array.from(groups.values()).map(g => ({ ts: g.ts, pvOutput: +(g.pvOutput / g.n).toFixed(1), storageOutput: +(g.storageOutput / g.n).toFixed(1), load: +(g.load / g.n).toFixed(1), soc: +(g.soc / g.n).toFixed(1) }))
    }
    return ok({ nodeId: n.id, interval, items: list, total: list.length }, traceId)
  })),

  http.post(`${BASE}/nodes/:id/online`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    const n = db.nodes.find(x => x.id === params.id)
    if (!n) throw new MockError(1005, '节点不存在')
    const { did, nonce, signature } = await body(request)
    const r = mockVerifySignature(did, nonce, signature)
    const matches = r.valid && (did === n.did)
    if (!matches) {
      writeAudit({ traceId, user, module: 'did', action: 'node:online', resourceType: 'node', resourceId: n.id, result: 'denied', riskLevel: 'high', detail: `节点 ${n.id} 接入被拒：${r.reason || 'DID 与节点不匹配'}` })
      if (r.abnormal) raiseAlert({ ruleCode: 'R02_ABNORMAL_DID', riskLevel: 'high', message: `异常 DID 尝试接入节点 ${n.id}`, actorDid: did, traceId })
      throw new MockError(1004, r.reason || 'DID 与节点绑定关系不匹配，拒绝接入')
    }
    n.status = n.status === 'offline' ? 'online' : n.status
    n.lastSeenAt = now()
    const ev = writeEvidence({ category: 'identity', refId: n.id, actorDid: did, traceId, payload: { op: 'node:online', nodeId: n.id, did, nonce } })
    writeAudit({ traceId, user, module: 'did', action: 'node:online', resourceType: 'node', resourceId: n.id, detail: `节点 ${n.id} DID 验签通过并上线`, evidenceId: ev.evidence_id })
    return ok({ accepted: true, nodeId: n.id, sessionToken: 'ns-' + sha256Hex(`${n.id}|${nonce}|${Date.now()}`).slice(0, 32), evidenceId: ev.evidence_id }, traceId)
  }))
]
