/** 风险评估（契约 §2.12） */
import { http } from 'msw'
import { db, nextId } from '../db.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, writeAudit, writeEvidence, MockError, now } from '../helpers.js'

const GRAN = { second: 95, minute: 70, '15min': 50, hour: 35, day: 15 }

export function assess(features = {}) {
  const freq = Number(features.queryFreq) || 0
  const gran = GRAN[features.dataGranularity] ?? 50
  const fields = Number(features.exposedFields) || 0
  const epsRemain = features.epsilonRemaining === undefined ? 1.0 : Number(features.epsilonRemaining) // 缺省按 1.0（与 algo 一致）
  const factors = [
    { name: '查询频率', weight: 0.35, score: Math.min(100, freq * 7), desc: `5分钟内${freq}次查询` },
    { name: '数据粒度', weight: 0.25, score: gran, desc: `采集粒度 ${features.dataGranularity || 'minute'}` },
    { name: '暴露字段', weight: 0.20, score: Math.min(100, fields * 12), desc: `暴露 ${fields} 个字段` },
    { name: '剩余预算', weight: 0.20, score: Math.min(100, Math.max(0, (1 - epsRemain) * 100)), desc: `剩余 ε ${epsRemain.toFixed(2)}` }
  ].map(f => ({ ...f, score: Math.round(f.score) }))
  const riskScore = Number(factors.reduce((s, f) => s + f.weight * f.score, 0).toFixed(1))
  const level = riskScore >= 80 ? 'critical' : riskScore >= 60 ? 'high' : riskScore >= 40 ? 'medium' : 'low'
  const suggestion = level === 'critical' ? '建议暂停对外查询并将 ε 降至 0.3 以下，启用字段级脱敏'
    : level === 'high' ? '建议将差分隐私 ε 由 1.0 降至 0.5，并限制分钟级数据查询频率'
      : level === 'medium' ? '建议保持 ε=1.0 并开启查询频率监控' : '当前风险可控，维持现有隐私策略'
  return { riskScore, level, factors, suggestion }
}

export const riskHandlers = [
  http.post(`${BASE}/risk/assess`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const { nodeId, features = {} } = await body(request)
    const node = db.nodes.find(n => n.id === nodeId)
    if (!node) throw new MockError(1005, '节点不存在')
    await new Promise(r => setTimeout(r, 120))
    const r = assess(features)
    const ev = writeEvidence({ category: 'audit', refId: nodeId, actorDid: user.did, traceId, payload: { op: 'risk:assess', nodeId, riskScore: r.riskScore, level: r.level, features } })
    db.riskHistory.push({ id: nextId('risk'), nodeId, riskScore: r.riskScore, level: r.level, factors: r.factors, features, at: now(), traceId, evidenceId: ev.evidence_id })
    writeAudit({ traceId, user, module: 'algo', action: 'risk:assess', resourceType: 'node', resourceId: nodeId, riskLevel: r.level === 'critical' ? 'high' : r.level === 'high' ? 'medium' : 'low', detail: `风险评估 ${nodeId}：${r.riskScore}（${r.level}）`, evidenceId: ev.evidence_id })
    return ok({ nodeId, ...r, evidenceId: ev.evidence_id, traceId }, traceId)
  })),

  http.get(`${BASE}/risk/history`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    const q = query(request)
    let list = db.riskHistory.slice().reverse()
    if (q.nodeId) list = list.filter(r => r.nodeId === q.nodeId)
    if (q.level) list = list.filter(r => r.level === q.level)
    return ok(paginate(list, q), traceId)
  }))
]
