/** 联邦学习（契约 §2.9）—— mock 内用 fedavgLite 真算 */
import { http } from 'msw'
import { db, nextId } from '../db.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, requirePerm, writeAudit, writeEvidence, raiseAlert, MockError, now } from '../helpers.js'
import { wsMock } from '../wsMock.js'
import { createFedAvgJob } from '../algo/fedavgLite.js'

const SAMPLES = { 'Node-A': 480, 'Node-B': 320, 'Node-C': 410, 'Node-D': 360 }

function taskDto(t) {
  return {
    id: t.id, name: t.name, status: t.status, currentRound: t.currentRound, totalRounds: t.totalRounds, nodes: t.nodes, nodeIds: t.nodeIds,
    dp: t.dp, topk: t.topk, rounds: t.rounds, modelVersion: t.modelVersion, anomaly: t.anomaly, traceId: t.traceId, createdBy: t.createdBy,
    createdAt: t.createdAt, startedAt: t.startedAt, finishedAt: t.finishedAt, updatedAt: t.updatedAt
  }
}

export const flHandlers = [
  http.get(`${BASE}/fl/models`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'model:read', { request, traceId })
    const q = query(request)
    let list = db.flModels.slice().reverse()
    if (q.status) list = list.filter(m => m.status === q.status)
    return ok(paginate(list, q), traceId)
  })),

  http.post(`${BASE}/fl/models/:version/publish`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'algo:execute', { request, traceId, resourceType: 'model', resourceId: params.version })
    const m = db.flModels.find(x => x.version === params.version)
    if (!m) throw new MockError(1005, '模型版本不存在')
    if (m.status === 'published') throw new MockError(1006, '模型已发布')
    m.status = 'published'; m.publishedAt = now(); m.publishedBy = user.did
    const ev = writeEvidence({ category: 'algo', refId: m.version, actorDid: user.did, traceId, payload: { op: 'model:publish', version: m.version, taskId: m.taskId, loss: m.loss, acc: m.acc } })
    writeAudit({ traceId, user, module: 'algo', action: 'model:publish', resourceType: 'model', resourceId: m.version, riskLevel: 'medium', detail: `发布模型 ${m.version}`, evidenceId: ev.evidence_id })
    return ok({ ...m, evidenceId: ev.evidence_id }, traceId)
  })),

  http.post(`${BASE}/fl/tasks`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'algo:execute', { request, traceId, resourceType: 'algo', detail: '无 algo:execute 权限创建联邦任务' })
    const data = await body(request)
    const nodeIds = Array.isArray(data.nodeIds) && data.nodeIds.length ? data.nodeIds : db.nodes.map(n => n.id)
    const unknown = nodeIds.filter(id => !db.nodes.find(n => n.id === id))
    if (unknown.length) throw new MockError(1001, `节点不存在：${unknown.join(',')}`)
    const rounds = Math.min(50, Math.max(1, parseInt(data.rounds, 10) || 10))
    const id = `fl-${String(nextId('fl')).padStart(6, '0')}`
    const task = {
      id, name: data.name || `负荷预测联合建模-${id}`, status: 'created', currentRound: 0, totalRounds: rounds, nodeIds,
      nodes: nodeIds.map(nid => { const n = db.nodes.find(x => x.id === nid); return { nodeId: nid, did: n.did, joined: n.status !== 'offline', samples: SAMPLES[nid] || 300 } }),
      dp: { enabled: data.dp?.enabled !== false, epsilon: Number(data.dp?.epsilon) || 1.0, delta: Number(data.dp?.delta) || 1e-5, epsilonSpent: 0 },
      topk: { enabled: data.topk?.enabled !== false, ratio: Number(data.topk?.ratio) || 0.1, compressionRatio: 0 },
      rounds: [], modelVersion: null, anomaly: null, traceId, createdBy: user.did, simulatePoison: data.simulatePoison || null,
      createdAt: now(), startedAt: null, finishedAt: null, updatedAt: now()
    }
    db.flTasks.push(task)
    writeAudit({ traceId, user, module: 'algo', action: 'fl:create', resourceType: 'algo', resourceId: id, detail: `创建联邦任务 ${task.name}（${rounds} 轮，ε=${task.dp.epsilon}，top-k=${task.topk.ratio}）` })
    return ok({ id, status: task.status, createdAt: task.createdAt, traceId }, traceId)
  })),

  http.get(`${BASE}/fl/tasks`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    const q = query(request)
    let list = db.flTasks.slice().reverse()
    if (q.status) list = list.filter(t => t.status === q.status)
    const page = paginate(list, q)
    return ok({ ...page, items: page.items.map(t => ({ ...taskDto(t), rounds: undefined, roundCount: t.rounds.length, lastLoss: t.rounds[t.rounds.length - 1]?.loss ?? null })) }, traceId)
  })),

  http.get(`${BASE}/fl/tasks/:id`, handle(async ({ request, params, traceId }) => {
    requireAuth(request)
    const t = db.flTasks.find(x => x.id === params.id)
    if (!t) throw new MockError(1005, '任务不存在')
    return ok(taskDto(t), traceId)
  })),

  http.get(`${BASE}/fl/tasks/:id/rounds`, handle(async ({ request, params, traceId }) => {
    requireAuth(request)
    const t = db.flTasks.find(x => x.id === params.id)
    if (!t) throw new MockError(1005, '任务不存在')
    return ok({ taskId: t.id, status: t.status, currentRound: t.currentRound, totalRounds: t.totalRounds, items: t.rounds, total: t.rounds.length }, traceId)
  })),

  http.post(`${BASE}/fl/tasks/:id/start`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'algo:execute', { request, traceId, resourceType: 'algo', resourceId: params.id, detail: '无 algo:execute 权限启动联邦训练' })
    const t = db.flTasks.find(x => x.id === params.id)
    if (!t) throw new MockError(1005, '任务不存在')
    if (t.status === 'running') throw new MockError(1006, '任务已在运行')
    if (t.status === 'success') throw new MockError(1006, '任务已完成')
    const runTrace = traceId
    t.status = 'running'; t.startedAt = now(); t.updatedAt = t.startedAt; t.rounds = []; t.currentRound = 0; t.anomaly = null; t.traceId = runTrace
    writeAudit({ traceId: runTrace, user, module: 'algo', action: 'fl:train', resourceType: 'algo', resourceId: t.id, detail: `启动联邦训练 ${t.totalRounds} 轮` })
    wsMock.emitLog('info', 'algo', `联邦任务 ${t.id} 开始训练（${t.nodes.length} 节点，${t.totalRounds} 轮）`, runTrace)

    const job = createFedAvgJob({
      taskId: t.id, rounds: t.totalRounds, nodes: t.nodes.filter(n => n.joined), dp: t.dp, topk: t.topk, simulatePoison: t.simulatePoison, intervalMs: 1000,
      onRound(round, { anomaly }) {
        // 每轮梯度哈希上链（algo 类存证）
        const ev = writeEvidence({ category: 'algo', refId: t.id, actorDid: user.did, traceId: runTrace, payload: { op: 'fl:round', taskId: t.id, round: round.round, loss: round.loss, acc: round.acc, gradientHash: round.gradientHash } })
        const rec = { ...round, evidenceId: ev.evidence_id, at: ev.created_at }
        t.rounds.push(rec)
        t.currentRound = round.round
        t.dp.epsilonSpent = round.epsilonSpent
        t.topk.compressionRatio = round.compressionRatio
        t.updatedAt = now()
        wsMock.emit('fl_progress', { taskId: t.id, round: round.round, totalRounds: t.totalRounds, loss: round.loss, acc: round.acc, compressionRatio: round.compressionRatio, epsilonSpent: round.epsilonSpent, gradientHash: round.gradientHash, evidenceId: ev.evidence_id }, runTrace)
        if (anomaly && !t.anomaly) {
          t.anomaly = anomaly
          writeAudit({ traceId: runTrace, user, module: 'algo', action: 'fl:anomaly', resourceType: 'algo', resourceId: t.id, result: 'failed', riskLevel: 'high', detail: `${anomaly.type}：${anomaly.detail}` })
          raiseAlert({ ruleCode: 'R05_SUSPICIOUS_GRAD', riskLevel: 'high', message: `联邦任务 ${t.id} 检测到${anomaly.type === 'gradient_poisoning' ? `可疑梯度（${anomaly.nodeId}）` : '隐私预算超限'}`, actorDid: anomaly.nodeId ? db.nodes.find(n => n.id === anomaly.nodeId)?.did : null, traceId: runTrace })
        }
      },
      onDone() {
        if (t.status !== 'running') return
        t.status = 'success'; t.finishedAt = now(); t.updatedAt = t.finishedAt
        const version = `v${nextId('model')}`
        t.modelVersion = version
        const last = t.rounds[t.rounds.length - 1]
        db.flModels.push({ version, taskId: t.id, loss: last?.loss ?? null, acc: last?.acc ?? null, rounds: t.totalRounds, status: 'draft', createdAt: t.finishedAt, publishedAt: null })
        const ev = writeEvidence({ category: 'algo', refId: t.id, actorDid: user.did, traceId: runTrace, payload: { op: 'fl:complete', taskId: t.id, modelVersion: version, finalLoss: last?.loss, finalAcc: last?.acc } })
        writeAudit({ traceId: runTrace, user, module: 'algo', action: 'fl:complete', resourceType: 'algo', resourceId: t.id, detail: `训练完成，模型 ${version}，loss=${last?.loss}`, evidenceId: ev.evidence_id })
        db.flJobs.delete(t.id)
      }
    })
    db.flJobs.set(t.id, job)
    return ok({ id: t.id, status: t.status, startedAt: t.startedAt, traceId: runTrace }, traceId)
  })),

  http.post(`${BASE}/fl/tasks/:id/cancel`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'algo:execute', { request, traceId, resourceType: 'algo', resourceId: params.id })
    const t = db.flTasks.find(x => x.id === params.id)
    if (!t) throw new MockError(1005, '任务不存在')
    if (t.status !== 'running' && t.status !== 'created') throw new MockError(1006, `任务状态 ${t.status} 不可取消`)
    const job = db.flJobs.get(t.id)
    if (job) job.cancel()
    db.flJobs.delete(t.id)
    t.status = 'cancelled'; t.finishedAt = now(); t.updatedAt = t.finishedAt
    writeAudit({ traceId, user, module: 'algo', action: 'fl:cancel', resourceType: 'algo', resourceId: t.id, riskLevel: 'medium', detail: `取消训练（已完成 ${t.currentRound} 轮）` })
    return ok({ id: t.id, status: t.status }, traceId)
  }))
]
