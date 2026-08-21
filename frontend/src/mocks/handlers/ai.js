/** AI 智能分析（契约 §2.11）—— mock 恒为 source:'cache' 的中文模板 */
import { http } from 'msw'
import { db, chain, nextId } from '../db.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, writeAudit, writeEvidence, MockError, now } from '../helpers.js'

const ACTION_CN = { discharge: '放电', charge: '充电', idle: '待机' }

export function buildDispatchExplanation(strategy, nodes) {
  const acts = strategy?.actions || []
  const main = acts.filter(a => a.action !== 'idle').sort((a, b) => b.powerKw - a.powerKw)[0]
  const totalLoad = nodes.reduce((s, n) => s + n.metrics.load, 0)
  const totalPv = nodes.reduce((s, n) => s + n.metrics.pvOutput, 0)
  const mainNode = nodes.find(n => n.id === main?.nodeId)
  const reasoning = [
    `全网总负荷 ${totalLoad.toFixed(1)}kW，总光伏出力 ${totalPv.toFixed(1)}kW，${totalLoad > totalPv ? '供需缺口需由储能放电补足' : '光伏富余可用于储能充电'}`,
    ...(mainNode ? [`${mainNode.name} 当前负荷 ${mainNode.metrics.load}kW、SOC ${mainNode.metrics.soc}%，${main.action === 'discharge' ? '放电' : '充电'} ${main.powerKw}kW 后仍处于 20%~95% 安全区间`] : []),
    ...acts.filter(a => a.action !== 'idle' && a.nodeId !== main?.nodeId).map(a => `${a.nodeId} ${ACTION_CN[a.action]} ${a.powerKw}kW（Q=${a.qValue}）：${a.reason}`),
    `所有动作均满足单节点 ≤30kW 约束，策略总回报 ${strategy?.totalReward ?? '--'}`
  ]
  const answer = mainNode
    ? `${mainNode.name}当前负荷 ${mainNode.metrics.load}kW${acts.length > 1 ? '为全网最高' : ''}，SOC ${mainNode.metrics.soc}% 高于安全下限，DQN 给出${ACTION_CN[main.action]} ${main.powerKw}kW 的最优动作（Q 值 ${main.qValue}）。` +
      `${acts.filter(a => a.action !== 'idle').length > 1 ? '其余节点按光伏富余与 SOC 状态分别安排充放电，' : ''}整体实现削峰填谷并保障储能安全边界。`
    : '当前各节点供需基本平衡，维持待机即可。'
  return { answer, reasoning }
}

function buildAnswer(scene, context, question) {
  if (scene === 'dispatch') {
    const task = db.dispatchTasks.find(t => t.id === context?.taskId) || db.dispatchTasks.filter(t => t.strategy).slice(-1)[0]
    if (!task?.strategy) return { answer: '暂无可解释的调度策略，请先运行调度任务。', reasoning: ['未找到任务策略'] }
    const nodes = db.nodes.filter(n => task.nodeIds.includes(n.id))
    return buildDispatchExplanation(task.strategy, nodes)
  }
  if (scene === 'risk') {
    const node = db.nodes.find(n => n.id === context?.nodeId) || db.nodes[0]
    const last = db.riskHistory.filter(r => r.nodeId === node.id).slice(-1)[0]
    return {
      answer: `${node.name}最近一次隐私风险评分 ${last?.riskScore ?? '--'}（${last?.level ?? '--'}）。主要风险来自查询频率与数据粒度：分钟级数据被高频查询时，攻击者可通过差分攻击推断单体负荷特征。建议将差分隐私 ε 从 1.0 降至 0.5，并对 L3 以上字段启用字段级脱敏。`,
      reasoning: ['查询频率因子权重 0.35', '数据粒度因子权重 0.30', '暴露字段因子权重 0.20', '剩余隐私预算因子权重 0.15']
    }
  }
  if (scene === 'data') {
    const total = db.assets.length
    const l34 = db.assets.filter(a => a.level === 'L3' || a.level === 'L4').length
    const auth = db.assets.filter(a => a.authStatus === 'authorized').length
    return {
      answer: `平台当前登记能源数据资产 ${total} 条，其中 L3/L4 敏感及核心数据 ${l34} 条（占 ${(l34 / total * 100).toFixed(0)}%），已授权 ${auth} 条，全部 ${total} 条已完成 SM3 摘要上链。光伏与负荷类数据占比最高，建议对调度类 L4 数据严格限制导出。`,
      reasoning: [`按等级：${['L1', 'L2', 'L3', 'L4'].map(l => `${l} ${db.assets.filter(a => a.level === l).length}`).join('，')}`, `按类型：${['pv', 'wind', 'storage', 'load', 'dispatch'].map(t => `${t} ${db.assets.filter(a => a.dataType === t).length}`).join('，')}`]
    }
  }
  if (scene === 'audit') {
    const high = db.auditLogs.filter(l => l.riskLevel === 'high' || l.riskLevel === 'critical')
    const open = db.alerts.filter(a => a.status === 'open')
    const s = chain.status()
    return {
      answer: `平台累计审计日志 ${db.auditLogs.length} 条，高风险 ${high.length} 条，未确认告警 ${open.length} 条。链上存证 ${s.totalRecords} 条，完整性校验${s.intact ? '通过' : `失败（断裂于高度 ${s.brokenAt}）`}。${high.length ? '高风险事件主要为越权访问与异常 DID 接入，建议复核相关主体权限。' : '未发现需要人工介入的安全事件。'}`,
      reasoning: [`高风险动作分布：${[...new Set(high.map(l => l.action))].join('、') || '无'}`, `开放告警规则：${[...new Set(open.map(a => a.ruleCode))].join('、') || '无'}`]
    }
  }
  // qa
  const q = String(question || '')
  if (/did|身份/i.test(q)) return { answer: `DID 是去中心化身份标识，平台格式为 did:vpp:<类型>:<SM3(公钥) 前 32 位>。当前已签发 ${db.dids.length} 个 DID，其中活跃 ${db.dids.filter(d => d.status === 'active').length} 个。设备接入必须通过 SM2 验签，冻结/注销的 DID 会被拒绝并触发 R02 告警。`, reasoning: ['DID 文档含 verificationMethod 公钥', '状态机 active→frozen→revoked'] }
  if (/联邦|fl|隐私/i.test(q)) return { answer: '联邦学习让各节点数据不出域：每轮节点本地训练后只上传裁剪加噪后的模型增量，云端按样本量加权平均（FedAvg）。差分隐私噪声保证单条样本不可被推断，Top-k 稀疏化把通信量压缩约 90%，每轮梯度哈希上链可追溯。', reasoning: ['FedAvg 加权平均', 'DP：L2 裁剪 + 高斯噪声', 'Top-k 稀疏化'] }
  if (/存证|链|篡改/i.test(q)) { const s = chain.status(); return { answer: `本地哈希链当前高度 ${s.height}，块哈希 = SM3(前块哈希 + 数据摘要 + 时间戳)，任何一条记录被改动都会导致其后所有块校验失败。当前完整性：${s.intact ? '完好' : `断裂于高度 ${s.brokenAt}`}。`, reasoning: ['逐块重算校验', '原始数据只存摘要'] } }
  return { answer: `已收到问题「${q || '（空）'}」。平台当前节点 ${db.nodes.length} 个、资产 ${db.assets.length} 条、存证 ${chain.height} 条、未确认告警 ${db.alerts.filter(a => a.status === 'open').length} 条。可以询问调度解释、隐私风险、数据分布、审计概况或 DID/联邦/存证原理。`, reasoning: ['离线缓存问答', '基于平台当前统计'] }
}

export const aiHandlers = [
  http.post(`${BASE}/ai/analyze`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const { scene = 'qa', context = {}, question = '' } = await body(request)
    if (!['dispatch', 'risk', 'data', 'qa', 'audit'].includes(scene)) throw new MockError(1001, 'scene 非法')
    const started = Date.now()
    await new Promise(r => setTimeout(r, 150 + Math.random() * 250))
    const { answer, reasoning } = buildAnswer(scene, context, question)
    const latencyMs = Date.now() - started
    const ev = writeEvidence({ category: 'audit', refId: `ai-${scene}`, actorDid: user.did, traceId, payload: { op: 'ai:analyze', scene, question, answerHashInput: answer } })
    const rec = { id: nextId('ai'), scene, question, answer, reasoning, source: 'cache', latencyMs, context, actorDid: user.did, actorName: user.realName, traceId, evidenceId: ev.evidence_id, createdAt: now() }
    db.aiHistory.push(rec)
    writeAudit({ traceId, user, module: 'algo', action: 'ai:analyze', resourceType: 'algo', resourceId: scene, detail: `AI 分析（${scene}）：${String(question).slice(0, 40)}`, evidenceId: ev.evidence_id })
    return ok({ answer, reasoning, source: 'cache', latencyMs, evidenceId: ev.evidence_id, traceId }, traceId)
  })),

  http.get(`${BASE}/ai/history`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    const q = query(request)
    let list = db.aiHistory.slice().reverse()
    if (q.scene) list = list.filter(h => h.scene === q.scene)
    if (q.actorDid) list = list.filter(h => h.actorDid === q.actorDid)
    return ok(paginate(list, q), traceId)
  }))
]
