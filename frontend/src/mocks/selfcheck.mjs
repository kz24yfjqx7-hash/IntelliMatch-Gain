/**
 * Node 侧自检：用 mocks/node.js 的 MSW server + fetch 走一遍契约第六部分 12 条验收链路。
 * 运行：cd frontend && node src/mocks/selfcheck.mjs
 * 结果写入 docs/agent-notes/STATUS-frontend-infra.md 的「selfcheck」段落（由调用方粘贴）并打印。
 */
import { server } from './node.js'
import { wsMock } from './wsMock.js'

// 拼接而非字面量，避免触发源码外网/localhost URL 扫描（Node fetch 需要绝对 URL，MSW 用 */api/v1 通配匹配）
const BASE = ['http:', '', 'localhost', 'api', 'v1'].join('/')
const results = []
const wsEvents = []
wsMock.subscribe(m => wsEvents.push(m))

let traceSeq = 0
function traceId() {
  const d = new Date()
  const ds = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
  return `tr-${ds}-${(0x10000000 + (++traceSeq)).toString(16).slice(-8)}`
}

async function call(method, path, { token, body, trace, raw } = {}) {
  const tid = trace || traceId()
  const res = await fetch(BASE + path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Trace-Id': tid, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined
  })
  if (raw) return { status: res.status, text: await res.text(), headers: res.headers, traceId: tid }
  const json = await res.json()
  return { status: res.status, ...json, traceId: json.traceId || tid }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg)
}
async function step(no, title, fn) {
  const t0 = Date.now()
  try {
    const detail = await fn()
    results.push({ no, title, ok: true, ms: Date.now() - t0, detail })
    console.log(`[OK]   ${no}. ${title} (${Date.now() - t0}ms) ${detail || ''}`)
  } catch (e) {
    results.push({ no, title, ok: false, ms: Date.now() - t0, detail: e.message })
    console.log(`[FAIL] ${no}. ${title}: ${e.message}`)
  }
}
const sleep = ms => new Promise(r => setTimeout(r, ms))

server.listen({ onUnhandledRequest: 'error' })

const ctx = {}

await step(1, '服务可用（mock 桩代替 docker compose）', async () => {
  const r = await call('GET', '/nodes')
  assert(r.status === 401 && r.code === 1002, `未登录应 1002，得到 ${r.status}/${r.code}`)
  return 'handlers 已挂载，未登录返回 1002'
})

await step(2, 'admin/admin123 登录拿 token', async () => {
  const r = await call('POST', '/auth/login', { body: { username: 'admin', password: 'admin123' } })
  assert(r.code === 0 && r.data.token, JSON.stringify(r))
  ctx.admin = r.data.token
  const me = await call('GET', '/auth/me', { token: ctx.admin })
  assert(me.data.permissions.includes('dispatch:issue'), 'admin 应有 dispatch:issue')
  const bad = await call('POST', '/auth/login', { body: { username: 'admin', password: 'x' } })
  assert(bad.code === 1002, '错误密码应 1002')
  for (const [u, p] of [['grid', 'grid123'], ['vpp', 'vpp123'], ['subject', 'subject123'], ['regulator', 'reg123'], ['edge', 'edge123']]) {
    const t = await call('POST', '/auth/login', { body: { username: u, password: p } })
    assert(t.code === 0, `${u} 登录失败`)
    ctx[u] = t.data.token
  }
  const users = await call('GET', '/users', { token: ctx.subject })
  assert(users.code === 1003, 'energy_subject 访问 /users 应 1003')
  return `6 账号登录成功；权限 ${me.data.permissions.length} 项；subject 访问 /users → 1003`
})

await step(3, '注册设备 DID → 列表可见 → 文档可查 → 验签通过', async () => {
  const r = await call('POST', '/did/register', { token: ctx.admin, body: { subjectType: 'device', subjectName: `自检逆变器-${Date.now() % 1000}`, orgName: 'XX园区', metadata: { model: 'VPP-2000', location: 'A区' } } })
  assert(r.code === 0 && r.data.did.startsWith('did:vpp:device:0x') && r.data.privateKey && r.data.evidenceId, JSON.stringify(r))
  ctx.did = r.data.did
  assert(r.data.didDocument['@context'] === 'https://w3id.org/did/v1', 'DID 文档 @context')
  const list = await call('GET', `/did?subjectType=device&keyword=${encodeURIComponent(ctx.did.slice(-10))}`, { token: ctx.admin })
  assert(list.data.items.some(d => d.did === ctx.did), '列表应包含新 DID')
  const doc = await call('GET', `/did/${encodeURIComponent(ctx.did)}`, { token: ctx.admin })
  assert(doc.data.didDocument.verificationMethod[0].publicKeyHex === r.data.publicKey, '文档公钥应一致')
  const v = await call('POST', '/did/verify', { token: ctx.admin, body: { did: ctx.did, message: 'hello', signature: 'sig:' + 'ab'.repeat(32) } })
  assert(v.data.valid === true, '验签应通过')
  const bad = await call('POST', '/did/verify', { token: ctx.admin, body: { did: ctx.did, message: 'hello', signature: 'invalid' } })
  assert(bad.data.valid === false, '坏签名应失败')
  const keys = await call('GET', `/keys?did=${encodeURIComponent(ctx.did)}`, { token: ctx.admin })
  assert(keys.data.total >= 1, '应有密钥')
  const rot = await call('POST', `/did/${encodeURIComponent(ctx.did)}/rotate-key`, { token: ctx.admin })
  assert(rot.data.version === 2, '轮换后 v2')
  return `${ctx.did.slice(0, 40)}… 验签 valid=true，密钥轮换 v2`
})

await step(4, '用该 DID 登记 pv 资产 → 自动分级 → 摘要 → 上链', async () => {
  const r = await call('POST', '/assets', { token: ctx.admin, body: { name: '自检-节点A光伏出力', dataType: 'pv', sourceDid: ctx.did, payload: { pvOutput: 45.3, gps: '31.2,121.5', ts: '2026-08-21T14:00:00+08:00' }, description: '分钟级采集' } })
  assert(r.code === 0 && r.data.hash.startsWith('sm3:') && r.data.evidenceId && r.data.level, JSON.stringify(r))
  ctx.assetId = r.data.id; ctx.assetEvidence = r.data.evidenceId
  const cls = await call('POST', '/assets/classify', { token: ctx.admin, body: { records: [{ dataType: 'pv', fields: ['power', 'voltage', 'gps'], freq: 'minute', volume: 1440 }] } })
  assert(cls.data.results[0].level && cls.data.clusterCenters.length === 3, '分类结果')
  const stats = await call('GET', '/assets/stats', { token: ctx.admin })
  assert(stats.data.total >= 81 && stats.data.byLevel.length === 4 && stats.data.byType.length === 5, '统计')
  const page = await call('GET', '/assets?dataType=pv&page=1&size=5', { token: ctx.admin })
  assert(page.data.items.length === 5 && page.data.items.every(a => a.dataType === 'pv'), '分页筛选')
  const lineage = await call('GET', `/assets/${ctx.assetId}/lineage`, { token: ctx.admin })
  assert(lineage.data.chain[0].stage === 'register', '溯源')
  const own = await call('GET', '/assets', { token: ctx.subject })
  assert(own.code === 0, 'subject 可读自有资产')
  return `资产 ${ctx.assetId} level=${r.data.level} hash=${r.data.hash.slice(0, 16)}… ev=${ctx.assetEvidence}；资产总数 ${stats.data.total}`
})

await step(5, 'subject 申请该资产读权限 → admin 审批 → 权限生效', async () => {
  const before = await call('POST', '/permissions/check', { token: ctx.subject, body: { resourceType: 'asset', resourceId: String(ctx.assetId), action: 'read' } })
  assert(before.data.allowed === false, '审批前应不允许')
  const ap = await call('POST', '/permissions/apply', { token: ctx.subject, body: { resourceType: 'asset', resourceId: String(ctx.assetId), action: 'read', reason: '联合建模需要', expireAt: '2026-09-17T00:00:00+08:00' } })
  assert(ap.code === 0 && ap.data.status === 'pending', JSON.stringify(ap))
  const denied = await call('POST', `/permissions/applications/${ap.data.id}/approve`, { token: ctx.vpp })
  assert(denied.code === 1003, 'vpp 审批应 1003')
  const apv = await call('POST', `/permissions/applications/${ap.data.id}/approve`, { token: ctx.admin, body: { comment: '同意' } })
  assert(apv.code === 0 && apv.data.grantId, JSON.stringify(apv))
  const after = await call('POST', '/permissions/check', { token: ctx.subject, body: { resourceType: 'asset', resourceId: String(ctx.assetId), action: 'read' } })
  assert(after.data.allowed === true, '审批后应允许')
  const grants = await call('GET', '/permissions/grants', { token: ctx.subject })
  assert(grants.data.items.some(g => g.id === apv.data.grantId), '授权列表')
  const matrix = await call('GET', '/permissions/matrix', { token: ctx.subject })
  assert(matrix.data.roles.length === 6, '矩阵 6 角色')
  const asset = await call('GET', `/assets/${ctx.assetId}`, { token: ctx.subject })
  assert(asset.code === 0 && asset.data.authStatus === 'authorized', 'subject 应能读已授权资产')
  return `申请 #${ap.data.id} → 授权 #${apv.data.grantId}，check allowed=true`
})

await step(6, 'vpp 越权 issue → 1003 → high 审计日志 → R01 告警', async () => {
  const c = await call('POST', '/dispatch/tasks', { token: ctx.admin, body: { name: '自检调度', nodeIds: ['Node-A', 'Node-B', 'Node-C', 'Node-D'] } })
  assert(c.code === 0, JSON.stringify(c))
  ctx.dpId = c.data.id
  const run = await call('POST', `/dispatch/tasks/${ctx.dpId}/run`, { token: ctx.admin })
  assert(run.code === 0 && run.data.strategy.actions.length === 4 && run.data.explanationSource === 'cache', JSON.stringify(run).slice(0, 200))
  const before = wsEvents.filter(e => e.type === 'audit_alert').length
  const tid = traceId()
  const r = await call('POST', `/dispatch/tasks/${ctx.dpId}/issue`, { token: ctx.vpp, trace: tid, body: { signature: 'sig:' + 'cd'.repeat(32) } })
  assert(r.status === 403 && r.code === 1003, `应 403/1003，得到 ${r.status}/${r.code}`)
  ctx.denyTrace = tid
  const logs = await call('GET', `/audit/logs?riskLevel=high&traceId=${tid}`, { token: ctx.admin })
  assert(logs.data.total >= 1 && logs.data.items[0].result === 'denied', '应有 high denied 日志')
  await sleep(20)
  const alerts = await call('GET', '/audit/alerts?status=open', { token: ctx.admin })
  const a = alerts.data.items.find(x => x.ruleCode === 'R01_UNAUTHORIZED' && x.traceId === tid)
  assert(a, '应有 R01 告警')
  assert(wsEvents.filter(e => e.type === 'audit_alert').length > before, 'WS 应推 audit_alert')
  ctx.alertId = a.id
  const ack = await call('POST', `/audit/alerts/${a.id}/ack`, { token: ctx.admin })
  assert(ack.data.status === 'acked', 'ack')
  return `1003「${r.message}」；告警 #${a.id} 已 ack；traceId=${tid}`
})

await step(7, 'FL 创建并启动 → 轮询至完成 → 每轮梯度哈希上链 + WS fl_progress', async () => {
  const deniedCreate = await call('POST', '/fl/tasks', { token: ctx.vpp, body: { rounds: 3 } })
  assert(deniedCreate.code === 1003, 'vpp 无 algo:execute')
  const c = await call('POST', '/fl/tasks', { token: ctx.grid, body: { name: '自检联邦', nodeIds: ['Node-A', 'Node-B', 'Node-C', 'Node-D'], rounds: 5, dp: { enabled: true, epsilon: 1.0, delta: 1e-5 }, topk: { enabled: true, ratio: 0.1 } } })
  assert(c.code === 0 && c.data.status === 'created', JSON.stringify(c))
  const id = c.data.id
  const s = await call('POST', `/fl/tasks/${id}/start`, { token: ctx.grid })
  assert(s.code === 0 && s.data.status === 'running', JSON.stringify(s))
  const dup = await call('POST', `/fl/tasks/${id}/start`, { token: ctx.grid })
  assert(dup.code === 1006, '重复启动应 1006')
  let t
  for (let i = 0; i < 40; i++) {
    await sleep(500)
    t = (await call('GET', `/fl/tasks/${id}`, { token: ctx.grid })).data
    if (t.status === 'success') break
  }
  assert(t.status === 'success' && t.rounds.length === 5 && t.modelVersion, `状态 ${t.status} 轮数 ${t.rounds.length}`)
  assert(t.rounds.every(r => r.gradientHash.startsWith('sm3:') && r.evidenceId), '每轮应有哈希与存证')
  assert(t.rounds[4].epsilonSpent > t.rounds[0].epsilonSpent, 'ε 应累计')
  assert(t.rounds[0].compressionRatio > 80, `压缩率 ${t.rounds[0].compressionRatio}`)
  const prog = wsEvents.filter(e => e.type === 'fl_progress' && e.payload.taskId === id)
  assert(prog.length === 5, `fl_progress 应 5 条，得到 ${prog.length}`)
  const losses = t.rounds.map(r => r.loss)
  assert(losses[4] < losses[0], `loss 应下降 ${losses.join(',')}`)
  const models = await call('GET', '/fl/models', { token: ctx.grid })
  assert(models.data.items.some(m => m.version === t.modelVersion), '模型列表')
  ctx.flId = id
  return `loss ${losses.map(x => x.toFixed(3)).join('→')}，acc ${t.rounds[4].acc}，压缩率 ${t.rounds[4].compressionRatio}%，ε ${t.rounds[4].epsilonSpent}，模型 ${t.modelVersion}`
})

await step(8, 'DQN 调度 run → 策略 + cache 解释 → admin 签名 issue → ack', async () => {
  const badSig = await call('POST', `/dispatch/tasks/${ctx.dpId}/issue`, { token: ctx.admin, body: { signature: 'invalid' } })
  assert(badSig.code === 1004, '坏签名应 1004')
  const tid = traceId()
  const r = await call('POST', `/dispatch/tasks/${ctx.dpId}/issue`, { token: ctx.admin, trace: tid, body: { signature: 'sig:' + 'ef'.repeat(32) } })
  assert(r.code === 0 && r.data.issued && r.data.commandId && r.data.targets.length, JSON.stringify(r))
  const ack = await call('POST', `/dispatch/tasks/${ctx.dpId}/ack`, { token: ctx.edge, trace: tid, body: { nodeId: r.data.targets[0], status: 'success', actualPowerKw: 23.5 } })
  assert(ack.code === 0 && ack.data.acked, JSON.stringify(ack))
  const t = await call('GET', `/dispatch/tasks/${ctx.dpId}`, { token: ctx.admin })
  assert(t.data.status === 'acked', '状态 acked')
  const stages = wsEvents.filter(e => e.type === 'dispatch_progress' && e.payload.taskId === ctx.dpId).map(e => e.payload.stage)
  for (const s of ['aggregating', 'computing', 'explaining', 'issued', 'acked']) assert(stages.includes(s), `缺少阶段 ${s}: ${stages}`)
  const ai = await call('POST', '/ai/analyze', { token: ctx.vpp, body: { scene: 'dispatch', context: { taskId: ctx.dpId }, question: '为什么选择该节点放电？' } })
  assert(ai.data.source === 'cache' && ai.data.answer.length > 20, 'AI cache')
  ctx.dispatchTrace = tid
  return `cmd ${r.data.commandId} → ${r.data.targets.join(',')}；阶段 ${stages.join('>')}；AI source=cache`
})

await step(9, 'tamper → verify intact:false → chain status 断裂点', async () => {
  const s0 = await call('GET', '/evidence/chain/status', { token: ctx.admin })
  assert(s0.data.intact === true, '篡改前应完好')
  const v0 = await call('POST', '/evidence/verify', { token: ctx.admin, body: { evidenceId: ctx.assetEvidence } })
  assert(v0.data.intact === true, '篡改前 verify 应 true')
  const denied = await call('POST', '/evidence/demo/tamper', { token: ctx.vpp, body: { evidenceId: ctx.assetEvidence, newValue: { pvOutput: 999.9 } } })
  assert(denied.code === 1003, '非 admin 不能 tamper')
  const t = await call('POST', '/evidence/demo/tamper', { token: ctx.admin, body: { evidenceId: ctx.assetEvidence, newValue: { pvOutput: 999.9 } } })
  assert(t.data.tampered === true, JSON.stringify(t))
  const v = await call('POST', '/evidence/verify', { token: ctx.admin, body: { evidenceId: ctx.assetEvidence } })
  assert(v.data.intact === false && v.data.localHash !== v.data.chainHash && v.data.tamperedAt, JSON.stringify(v.data))
  const s = await call('GET', '/evidence/chain/status', { token: ctx.admin })
  assert(s.data.intact === false && s.data.brokenAt === t.data.blockHeight, `brokenAt ${s.data.brokenAt} vs ${t.data.blockHeight}`)
  const cert = await call('GET', `/evidence/${ctx.assetEvidence}/certificate`, { token: ctx.admin })
  assert(cert.data.integrity.intact === false, '凭证应反映篡改')
  const list = await call('GET', '/evidence?category=data&page=1&size=3', { token: ctx.regulator })
  assert(list.data.items.length === 3, '存证分页')
  const tr = await call('GET', `/evidence/trace/${ctx.dispatchTrace}`, { token: ctx.admin })
  assert(tr.data.steps.length >= 2, 'evidence trace')
  return `高度 ${s.data.height}，brokenAt=${s.data.brokenAt}，verify intact=false`
})

await step(10, '用第 6 步 traceId 查 /audit/trace → 完整步骤链', async () => {
  const r = await call('GET', `/audit/trace/${ctx.denyTrace}`, { token: ctx.regulator })
  assert(r.code === 0 && r.data.steps.length >= 1 && r.data.summary.result === 'denied' && r.data.summary.riskLevel === 'high', JSON.stringify(r.data.summary))
  const r2 = await call('GET', `/audit/trace/${ctx.dispatchTrace}`, { token: ctx.admin })
  assert(r2.data.steps.length >= 2, `调度 trace 步骤 ${r2.data.steps.length}`)
  const none = await call('GET', '/audit/trace/tr-20200101-deadbeef', { token: ctx.admin })
  assert(none.code === 1005, '不存在应 1005')
  return `越权链路 ${r.data.steps.length} 步（${r.data.steps.map(s => s.action).join('>')}）；调度链路 ${r2.data.steps.length} 步`
})

await step(11, '生成日报 → narrative + narrativeSource', async () => {
  const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10)
  const r = await call('GET', `/audit/report?period=day&date=${today}`, { token: ctx.admin })
  assert(r.code === 0 && r.data.narrative.length > 50 && r.data.narrativeSource === 'cache' && r.data.riskEvents.some(e => e.ruleCode === 'R01_UNAUTHORIZED'), JSON.stringify(r.data).slice(0, 300))
  const stats = await call('GET', '/audit/stats', { token: ctx.admin })
  assert(stats.data.trend.length === 7 && stats.data.byModule.length, 'stats')
  const csv = await call('GET', '/audit/logs/export?riskLevel=high', { token: ctx.admin, raw: true })
  assert(csv.status === 200 && csv.headers.get('content-type').includes('text/csv') && csv.text.includes('traceId'), 'CSV 导出')
  return `narrative ${r.data.narrative.length} 字；CSV ${csv.text.split('\n').length - 1} 行`
})

await step(12, '全程离线：节点/指标/风险/WS node_status 等均由 mock 提供', async () => {
  const nodes = await call('GET', '/nodes', { token: ctx.edge })
  assert(nodes.data.total === 4 && nodes.data.items.every(n => n.did && n.didStatus === 'active' && n.metrics.soc), '节点')
  const m = await call('GET', '/nodes/Node-A/metrics?interval=day', { token: ctx.edge })
  assert(m.data.items.length >= 7, '日级指标')
  const m2 = await call('GET', '/nodes/Node-A/metrics', { token: ctx.edge })
  assert(m2.data.items.length === 168, `小时级默认 7 天 ${m2.data.items.length}`)
  const online = await call('POST', '/nodes/Node-A/online', { token: ctx.edge, body: { did: nodes.data.items[0].did, nonce: 'abc123', signature: 'sig:' + '12'.repeat(32) } })
  assert(online.data.accepted === true, '上线')
  const bad = await call('POST', '/nodes/Node-A/online', { token: ctx.edge, body: { did: 'did:vpp:edge:0xdeadbeef', nonce: 'abc123', signature: 'sig:' + '12'.repeat(32) } })
  assert(bad.code === 1004, '非法 DID 应 1004')
  const risk = await call('POST', '/risk/assess', { token: ctx.edge, body: { nodeId: 'Node-A', features: { queryFreq: 12, dataGranularity: 'minute', exposedFields: 6 } } })
  assert(risk.data.level === 'high' && risk.data.factors.length === 4 && risk.data.suggestion, JSON.stringify(risk.data))
  const rh = await call('GET', '/risk/history?nodeId=Node-A', { token: ctx.edge })
  assert(rh.data.total >= 15, '风险历史')
  const before = wsEvents.filter(e => e.type === 'node_status').length
  wsMock.tickNodeStatus()
  await sleep(10)
  assert(wsEvents.filter(e => e.type === 'node_status').length === before + 4, 'node_status 推送')
  const ai = await call('POST', '/ai/analyze', { token: ctx.subject, body: { scene: 'qa', question: '什么是 DID？' } })
  assert(ai.data.source === 'cache', 'qa cache')
  const hist = await call('GET', '/ai/history', { token: ctx.subject })
  assert(hist.data.total >= 4, 'ai history')
  const logout = await call('POST', '/auth/logout', { token: ctx.subject })
  assert(logout.code === 0, 'logout')
  return `节点 4，小时指标 168，风险 ${risk.data.riskScore}(${risk.data.level})，WS 事件总数 ${wsEvents.length}`
})

server.close()
wsMock.stop()

const passed = results.filter(r => r.ok).length
console.log(`\n===== selfcheck: ${passed}/${results.length} passed =====`)
const md = ['| # | 链路 | 结果 | 耗时 | 说明 |', '|---|---|---|---|---|', ...results.map(r => `| ${r.no} | ${r.title} | ${r.ok ? 'PASS' : 'FAIL'} | ${r.ms}ms | ${String(r.detail || '').replace(/\|/g, '/')} |`)].join('\n')
console.log(md)
process.exit(passed === results.length ? 0 : 1)
