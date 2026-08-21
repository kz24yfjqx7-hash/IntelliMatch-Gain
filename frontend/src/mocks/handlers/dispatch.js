/** 智能调度（契约 §2.10）—— 规则版策略代替 DQN（mock） */
import { http } from 'msw'
import { db, nextId } from '../db.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, requirePerm, writeAudit, writeEvidence, MockError, now, delay } from '../helpers.js'
import { wsMock } from '../wsMock.js'
import { mockVerifySignature } from './did.js'
import { buildDispatchExplanation } from './ai.js'

/** 规则策略：负荷最高且 SOC 充足的节点放电；光伏富余且 SOC 偏低的节点充电；其余待机 */
export function ruleStrategy(nodes, timeWindow) {
  const qTable = []
  const actions = []
  const violations = []
  const sorted = nodes.slice().sort((a, b) => b.metrics.load - a.metrics.load)
  for (const n of nodes) {
    const m = n.metrics
    const dis = 1 + (m.load / 150) * 3.5 + ((m.soc - 50) / 45) * 2.5 - (m.pvOutput / 60)
    const chg = 2 + (m.pvOutput / 50) * 3 + ((80 - m.soc) / 60) * 3 - (m.load / 150) * 2
    const idle = 5 - Math.abs(m.storageOutput) / 30
    let q = { charge: +chg.toFixed(2), idle: +idle.toFixed(2), discharge: +dis.toFixed(2) }
    let action = Object.entries(q).sort((a, b) => b[1] - a[1])[0][0]
    // 约束：SOC 20~95、单节点 ≤30kW；越限改为次优动作并记录
    if (action === 'discharge' && m.soc <= 25) { const applied = q.idle >= q.charge ? 'idle' : 'charge'; violations.push({ nodeId: n.id, constraint: 'socMin', attempted: 'discharge', applied, detail: `SOC ${m.soc}% 不足，禁止放电` }); action = applied }
    if (action === 'charge' && m.soc >= 92) { violations.push({ nodeId: n.id, constraint: 'socMax', attempted: 'charge', applied: 'idle', detail: `SOC ${m.soc}% 过高，禁止充电` }); action = 'idle' }
    let powerKw = 0
    let reason = '功率平衡，维持待机'
    if (action === 'discharge') { powerKw = Math.min(30, (m.soc - 20) * 0.5, m.load * 0.2); reason = n.id === sorted[0].id ? '负荷最高且SOC充足' : '负荷偏高且SOC充足' }
    if (action === 'charge') { powerKw = Math.min(30, (95 - m.soc) * 0.4, Math.max(5, m.pvOutput * 0.6)); reason = '光伏富余且SOC偏低' }
    actions.push({ nodeId: n.id, action, powerKw: +powerKw.toFixed(1), qValue: q[action], reason })
    qTable.push({ nodeId: n.id, ...q })
  }
  // 保证至少一个放电节点（演示需要）
  if (!actions.some(a => a.action === 'discharge')) {
    const top = sorted.find(n => n.metrics.soc > 30)
    if (top) { const a = actions.find(x => x.nodeId === top.id); a.action = 'discharge'; a.powerKw = +Math.min(30, (top.metrics.soc - 20) * 0.5, top.metrics.load * 0.2).toFixed(1); a.reason = '负荷最高且SOC充足'; a.qValue = qTable.find(x => x.nodeId === top.id).discharge }
  }
  const totalReward = +actions.reduce((s, a) => s + (a.action === 'idle' ? 0.5 : a.qValue * 0.6 + a.powerKw * 0.1), 0).toFixed(1)
  return { strategy: { actions, totalReward, timeWindow }, qTable, constraintsChecked: { socMin: 20, socMax: 95, maxPowerKw: 30, violations } }
}

function taskDto(t) {
  const { ackHistory, ...rest } = t
  return rest
}

export const dispatchHandlers = [
  http.post(`${BASE}/dispatch/tasks`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'dispatch:read', { request, traceId, resourceType: 'dispatch' })
    const data = await body(request)
    const nodeIds = Array.isArray(data.nodeIds) && data.nodeIds.length ? data.nodeIds : db.nodes.map(n => n.id)
    const id = `dp-${String(nextId('dispatch')).padStart(6, '0')}`
    const d = now().slice(0, 10)
    const task = {
      id, name: data.name || `统一调度-${id}`, status: 'created', nodeIds, timeWindow: data.timeWindow || `${d}T15:00~16:00+08:00`, objective: data.objective || '平衡边缘节点负荷并保障储能安全边界',
      strategy: null, qTable: null, constraintsChecked: null, explanation: null, explanationSource: null, evidenceId: null, issueEvidenceId: null, commandId: null, signerDid: null, targets: [], ack: null,
      traceId, createdBy: user.did, createdAt: now(), ranAt: null, issuedAt: null, ackedAt: null, updatedAt: now()
    }
    db.dispatchTasks.push(task)
    writeAudit({ traceId, user, module: 'algo', action: 'dispatch:create', resourceType: 'dispatch', resourceId: id, detail: `创建调度任务 ${task.name}` })
    return ok({ id, status: task.status, createdAt: task.createdAt, traceId }, traceId)
  })),

  http.get(`${BASE}/dispatch/tasks`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'dispatch:read', { request, traceId })
    const q = query(request)
    let list = db.dispatchTasks.slice().reverse()
    if (q.status) list = list.filter(t => t.status === q.status)
    const page = paginate(list, q)
    return ok({ ...page, items: page.items.map(taskDto) }, traceId)
  })),

  http.get(`${BASE}/dispatch/tasks/:id`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'dispatch:read', { request, traceId })
    const t = db.dispatchTasks.find(x => x.id === params.id)
    if (!t) throw new MockError(1005, '任务不存在')
    return ok(taskDto(t), traceId)
  })),

  http.post(`${BASE}/dispatch/tasks/:id/run`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'dispatch:read', { request, traceId, resourceType: 'dispatch', resourceId: params.id })
    const t = db.dispatchTasks.find(x => x.id === params.id)
    if (!t) throw new MockError(1005, '任务不存在')
    if (t.status === 'running') throw new MockError(1006, '任务正在运行')
    t.status = 'running'; t.updatedAt = now(); t.traceId = traceId
    const nodes = db.nodes.filter(n => t.nodeIds.includes(n.id))
    wsMock.emit('dispatch_progress', { taskId: t.id, stage: 'aggregating', detail: `汇聚 ${nodes.length} 个节点实时指标` }, traceId)
    await delay(250)
    wsMock.emit('dispatch_progress', { taskId: t.id, stage: 'computing', detail: 'DQN 策略推理（mock：规则版）' }, traceId)
    const r = ruleStrategy(nodes, t.timeWindow)
    await delay(300)
    wsMock.emit('dispatch_progress', { taskId: t.id, stage: 'explaining', detail: '生成 AI 解释（离线缓存）' }, traceId)
    const exp = buildDispatchExplanation(r.strategy, nodes)
    await delay(250)
    const ev = writeEvidence({ category: 'algo', refId: t.id, actorDid: user.did, traceId, payload: { op: 'dispatch:run', taskId: t.id, actions: r.strategy.actions, totalReward: r.strategy.totalReward } })
    Object.assign(t, { status: 'success', strategy: r.strategy, qTable: r.qTable, constraintsChecked: r.constraintsChecked, explanation: exp.answer, explanationSource: 'cache', evidenceId: ev.evidence_id, ranAt: now(), updatedAt: now(), targets: r.strategy.actions.filter(a => a.action !== 'idle').map(a => a.nodeId) })
    db.aiHistory.push({ id: nextId('ai'), scene: 'dispatch', question: '调度策略解释', answer: exp.answer, reasoning: exp.reasoning, source: 'cache', latencyMs: 260, context: { taskId: t.id }, actorDid: user.did, traceId, createdAt: now() })
    writeAudit({ traceId, user, module: 'algo', action: 'dispatch:run', resourceType: 'dispatch', resourceId: t.id, riskLevel: 'medium', detail: `生成策略：${r.strategy.actions.filter(a => a.action !== 'idle').map(a => `${a.nodeId} ${a.action} ${a.powerKw}kW`).join('；')}`, evidenceId: ev.evidence_id })
    return ok({ id: t.id, status: t.status, strategy: t.strategy, qTable: t.qTable, constraintsChecked: t.constraintsChecked, explanation: t.explanation, reasoning: exp.reasoning, explanationSource: t.explanationSource, evidenceId: ev.evidence_id, traceId }, traceId)
  })),

  http.post(`${BASE}/dispatch/tasks/:id/issue`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    const t = db.dispatchTasks.find(x => x.id === params.id)
    if (!t) throw new MockError(1005, '任务不存在')
    // 权限校验：vpp_operator 等无 dispatch:issue → 1003 + high 审计 + R01 告警（立即）
    requirePerm(user, 'dispatch:issue', { request, traceId, resourceType: 'dispatch', resourceId: t.id, detail: '越权尝试下发调度指令', alertImmediately: true })
    if (!t.strategy) throw new MockError(1006, '任务尚未生成策略，请先 run')
    if (t.status === 'issued' || t.status === 'acked') throw new MockError(1006, '指令已下发')
    const { signature } = await body(request)
    const v = mockVerifySignature(user.did, t.id, signature)
    if (!v.valid) {
      writeAudit({ traceId, user, module: 'permission', action: 'dispatch:issue', resourceType: 'dispatch', resourceId: t.id, result: 'failed', riskLevel: 'high', detail: `签名校验失败：${v.reason}` })
      throw new MockError(1004, `签名无效：${v.reason}`)
    }
    const commandId = `cmd-${String(nextId('command')).padStart(6, '0')}`
    const targets = t.strategy.actions.filter(a => a.action !== 'idle').map(a => a.nodeId)
    const ev = writeEvidence({ category: 'audit', refId: t.id, actorDid: user.did, traceId, payload: { op: 'dispatch:issue', taskId: t.id, commandId, signerDid: user.did, signature: String(signature).slice(0, 32), targets } })
    Object.assign(t, { status: 'issued', commandId, signerDid: user.did, targets, issueEvidenceId: ev.evidence_id, issuedAt: now(), updatedAt: now() })
    writeAudit({ traceId, user, module: 'permission', action: 'dispatch:issue', resourceType: 'dispatch', resourceId: t.id, riskLevel: 'medium', detail: `签名下发指令 ${commandId} → ${targets.join(',')}`, evidenceId: ev.evidence_id })
    wsMock.emit('dispatch_progress', { taskId: t.id, stage: 'issued', detail: `指令 ${commandId} 已下发至 ${targets.join(',')}` }, traceId)
    return ok({ issued: true, commandId, signerDid: user.did, evidenceId: ev.evidence_id, targets, traceId }, traceId)
  })),

  http.post(`${BASE}/dispatch/tasks/:id/ack`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    const t = db.dispatchTasks.find(x => x.id === params.id)
    if (!t) throw new MockError(1005, '任务不存在')
    if (t.status !== 'issued' && t.status !== 'acked') throw new MockError(1006, '指令尚未下发，无法回执')
    const data = await body(request)
    const nodeId = data.nodeId || t.targets[0]
    const action = t.strategy.actions.find(a => a.nodeId === nodeId)
    const ack = { nodeId, status: data.status || 'success', actualPowerKw: data.actualPowerKw ?? action?.powerKw ?? 0, responseDelaySec: data.responseDelaySec ?? 1.2, at: now() }
    t.ack = ack
    t.ackHistory = [...(t.ackHistory || []), ack]
    const allAcked = t.targets.every(id => (t.ackHistory || []).some(a => a.nodeId === id))
    Object.assign(t, { status: 'acked', ackedAt: now(), updatedAt: now(), allAcked })
    const ev = writeEvidence({ category: 'audit', refId: t.id, actorDid: db.nodes.find(n => n.id === nodeId)?.did || user.did, traceId, payload: { op: 'dispatch:ack', taskId: t.id, commandId: t.commandId, ...ack } })
    writeAudit({ traceId, user, module: 'algo', action: 'dispatch:ack', resourceType: 'dispatch', resourceId: t.id, detail: `节点 ${nodeId} 回执：${ack.status}，实际功率 ${ack.actualPowerKw}kW`, evidenceId: ev.evidence_id })
    wsMock.emit('dispatch_progress', { taskId: t.id, stage: 'acked', detail: `节点 ${nodeId} 执行回执 ${ack.status}` }, traceId)
    return ok({ acked: true, taskId: t.id, commandId: t.commandId, ack, allAcked, evidenceId: ev.evidence_id }, traceId)
  }))
]
