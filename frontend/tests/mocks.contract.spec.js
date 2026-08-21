/**
 * MSW 假后端契约一致性测试：用 fetch 走契约第二部分每个接口，
 * 断言包装结构 {code,message,data,traceId}、分页结构、枚举值、权限矩阵生效、篡改检测、FL 真算。
 * server 来自 tests/setup.js（src/mocks/node.js）。
 */
import { describe, it, expect, beforeAll } from 'vitest'

const BASE = 'http://localhost/api/v1'
const TRACE_RE = /^tr-\d{8}-[0-9a-f]{8}$/
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?[+-]\d{2}:\d{2}$/
const DID_RE = /^did:vpp:(user|device|org|edge):0x[0-9a-f]{32}$/
const HASH_RE = /^(sm3|sha256):[0-9a-f]{64}$/
const ROLES = ['sys_admin', 'grid_dispatcher', 'vpp_operator', 'energy_subject', 'regulator', 'edge_node']
const LEVELS = ['L1', 'L2', 'L3', 'L4']
const TASK_STATUS = ['created', 'running', 'success', 'failed', 'cancelled']
const RISK = ['low', 'medium', 'high', 'critical']
const CATEGORY = ['data', 'identity', 'permission', 'audit', 'algo']

const tokens = {}
async function api(method, path, { body, token, traceId, raw } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  if (traceId) headers['X-Trace-Id'] = traceId
  const res = await fetch(BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  if (raw) return res
  const json = await res.json()
  return { status: res.status, json, headers: res.headers }
}
function expectWrapped(r, code = 0) {
  expect(r.json).toHaveProperty('code')
  expect(r.json).toHaveProperty('message')
  expect(r.json).toHaveProperty('data')
  expect(r.json).toHaveProperty('traceId')
  expect(r.json.traceId).toMatch(TRACE_RE)
  expect(r.json.code).toBe(code)
  const httpByCode = { 0: 200, 1001: 400, 1002: 401, 1003: 403, 1004: 403, 1005: 404, 1006: 409, 1007: 429, 2001: 502, 2002: 502, 5000: 500 }
  expect(r.status).toBe(httpByCode[code])
  return r.json.data
}
function expectPage(data) {
  expect(Array.isArray(data.items)).toBe(true)
  expect(typeof data.total).toBe('number')
  expect(typeof data.page).toBe('number')
  expect(typeof data.size).toBe('number')
  return data
}
async function login(u, p) {
  const r = await api('POST', '/auth/login', { body: { username: u, password: p } })
  const d = expectWrapped(r)
  tokens[u] = d.token
  return d
}
const A = () => ({ token: tokens.admin })
const sleep = ms => new Promise(r => setTimeout(r, ms))

beforeAll(async () => {
  expect(globalThis.__mswServer, 'src/mocks/node.js 未就绪（frontend-infra）').toBeTruthy()
  await login('admin', 'admin123')
  await login('vpp', 'vpp123')
  await login('subject', 'subject123')
}, 20000)

describe('2.1 认证', () => {
  it('login 响应结构与六个演示账号', async () => {
    const d = await login('admin', 'admin123')
    expect(d.expiresIn).toBe(28800)
    expect(d.user).toMatchObject({ username: 'admin', roles: ['sys_admin'] })
    expect(d.user.did).toMatch(DID_RE)
    for (const [u, p, role] of [['grid', 'grid123', 'grid_dispatcher'], ['regulator', 'reg123', 'regulator'], ['edge', 'edge123', 'edge_node']]) {
      const x = await login(u, p)
      expect(x.user.roles).toContain(role)
    }
  })
  it('错误密码 → 1002 或 1001', async () => {
    const r = await api('POST', '/auth/login', { body: { username: 'admin', password: 'bad' } })
    expect([1001, 1002]).toContain(r.json.code)
    expect(r.status).toBe(r.json.code === 1002 ? 401 : 400)
  })
  it('未带 token → 1002', async () => {
    const r = await api('GET', '/auth/me')
    expectWrapped(r, 1002)
  })
  it('traceId 沿用 X-Trace-Id', async () => {
    const r = await api('GET', '/auth/me', { ...A(), traceId: 'tr-20260821-0badcafe' })
    expect(r.json.traceId).toBe('tr-20260821-0badcafe')
  })
  it('GET /auth/me 含扁平 permissions', async () => {
    const d = expectWrapped(await api('GET', '/auth/me', A()))
    expect(Array.isArray(d.permissions)).toBe(true)
    expect(d.permissions).toContain('dispatch:issue')
    expect(d.permissions.every(p => /^[a-z]+:[a-z]+$/.test(p))).toBe(true)
  })
  it('GET /users 分页（sys_admin）', async () => {
    const d = expectPage(expectWrapped(await api('GET', '/users?page=1&size=10', A())))
    expect(d.total).toBeGreaterThanOrEqual(6)
    for (const u of d.items) u.roles.forEach(r => expect(ROLES).toContain(r))
  })
  it('GET /users 非管理员 → 1003', async () => {
    expectWrapped(await api('GET', '/users', { token: tokens.vpp }), 1003)
  })
})

describe('2.2 DID', () => {
  let did
  it('register → 列表可见 → 文档可查 → 验签', async () => {
    const d = expectWrapped(await api('POST', '/did/register', { ...A(), body: { subjectType: 'device', subjectName: 'qa-逆变器', orgName: 'qa', metadata: { model: 'VPP-2000' } } }))
    did = d.did
    expect(did).toMatch(/^did:vpp:device:0x[0-9a-f]{32}$/)
    expect(d.didDocument['@context']).toBe('https://w3id.org/did/v1')
    expect(d.didDocument.id).toBe(did)
    expect(d.didDocument.verificationMethod[0].type).toBe('SM2VerificationKey2023')
    expect(d.publicKey).toBeTruthy()
    expect(d.privateKey).toBeTruthy()
    expect(d.evidenceId).toMatch(/^ev-/)
    expect(d.chainTxId).toBeTruthy()

    const list = expectPage(expectWrapped(await api('GET', '/did?subjectType=device&size=200', A())))
    expect(list.items.some(x => x.did === did)).toBe(true)
    list.items.forEach(x => { expect(['user', 'device', 'org', 'edge']).toContain(x.subjectType); expect(['active', 'frozen', 'revoked']).toContain(x.status) })

    const doc = expectWrapped(await api('GET', `/did/${encodeURIComponent(did)}`, A()))
    expect(doc.id || doc.didDocument?.id || doc.did).toBeTruthy()

    const v = expectWrapped(await api('POST', '/did/verify', { ...A(), body: { did, message: 'hello', signature: 'deadbeef' } }))
    expect(typeof v.valid).toBe('boolean')
    expect(v.subjectType).toBe('device')
    expect(v.status).toBe('active')
  })
  it('freeze → frozen；rotate-key；resolve；不存在 → 1005', async () => {
    const s = expectWrapped(await api('POST', `/did/${encodeURIComponent(did)}/status`, { ...A(), body: { action: 'freeze', reason: 'qa' } }))
    expect(s.status).toBe('frozen')
    expect(s.evidenceId).toMatch(/^ev-/)
    const v = expectWrapped(await api('POST', '/did/verify', { ...A(), body: { did, message: 'x', signature: 'y' } }))
    expect(v.valid).toBe(false)
    const u = expectWrapped(await api('POST', `/did/${encodeURIComponent(did)}/status`, { ...A(), body: { action: 'unfreeze', reason: 'qa' } }))
    expect(u.status).toBe('active')
    expectWrapped(await api('POST', `/did/${encodeURIComponent(did)}/rotate-key`, A()))
    const rs = expectWrapped(await api('POST', '/did/resolve', { ...A(), body: { dids: [did] } }))
    expect(JSON.stringify(rs)).toContain(did)
    expectWrapped(await api('GET', '/did/did:vpp:device:0x00000000000000000000000000000000', A()), 1005)
  })
  it('密钥列表分页，algorithm 枚举', async () => {
    const d = expectPage(expectWrapped(await api('GET', `/keys?did=${encodeURIComponent(did)}`, A())))
    expect(d.total).toBeGreaterThanOrEqual(1)
    d.items.forEach(k => { expect(['SM2', 'ECC', 'RSA']).toContain(k.algorithm); expect(['active', 'frozen', 'revoked']).toContain(k.status) })
    const h = expectWrapped(await api('GET', `/keys/${d.items[0].id}/history`, A()))
    expect(Array.isArray(h.versions || h.items || h)).toBe(true)
  })
})

describe('2.4 资产', () => {
  let assetId
  it('登记 pv 资产 → hash sm3 → 上链；详情；溯源；stats；分页筛选', async () => {
    const nodes = expectWrapped(await api('GET', '/nodes', A()))
    const sourceDid = nodes.items[0].did
    const d = expectWrapped(await api('POST', '/assets', { ...A(), body: { name: 'qa-pv', dataType: 'pv', sourceDid, level: 'L2', payload: { pvOutput: 45.3, ts: '2026-08-21T14:00:00+08:00' }, description: 'qa' } }))
    assetId = d.id
    expect(d.hash).toMatch(HASH_RE)
    expect(LEVELS).toContain(d.level)
    expect(d.evidenceId).toMatch(/^ev-/)
    expect(d.chainTxId).toBeTruthy()
    expect(d.authStatus).toBeTruthy()
    expect(d.createdAt).toMatch(ISO_RE)

    const one = expectWrapped(await api('GET', `/assets/${assetId}`, A()))
    expect(one.id).toBe(assetId)
    const lin = expectWrapped(await api('GET', `/assets/${assetId}/lineage`, A()))
    expect(lin.assetId).toBe(assetId)
    expect(lin.chain[0].stage).toBe('register')
    expect(lin.chain[0].evidenceId).toMatch(/^ev-/)

    const st = expectWrapped(await api('GET', '/assets/stats', A()))
    expect(st.total).toBeGreaterThanOrEqual(80)
    expect(st.byLevel.map(x => x.level).sort()).toEqual(LEVELS)
    expect(st.byType.length).toBe(5)
    expect(typeof st.onChain).toBe('number')

    const page = expectPage(expectWrapped(await api('GET', '/assets?dataType=pv&page=1&size=5', A())))
    expect(page.size).toBe(5)
    expect(page.items.length).toBeLessThanOrEqual(5)
    page.items.forEach(a => { expect(a.dataType).toBe('pv'); expect(LEVELS).toContain(a.level) })
    const l3 = expectPage(expectWrapped(await api('GET', '/assets?level=L3&size=200', A())))
    l3.items.forEach(a => expect(a.level).toBe('L3'))
  })
  it('分类分级代理：返回 results / clusterCenters，gps 字段 ≥ L3', async () => {
    const d = expectWrapped(await api('POST', '/assets/classify', { ...A(), body: { records: [{ dataType: 'pv', fields: ['power', 'gps'], freq: 'minute', volume: 1440 }, { dataType: 'wind', fields: ['speed'], freq: 'day', volume: 10 }] } }))
    expect(d.results.length).toBe(2)
    d.results.forEach(r => { expect(LEVELS).toContain(r.level); expect(r.factors).toBeTruthy() })
    expect(LEVELS.indexOf(d.results[0].level)).toBeGreaterThanOrEqual(2)
    expect(Array.isArray(d.clusterCenters)).toBe(true)
  })
  it('不存在资产 → 1005', async () => {
    expectWrapped(await api('GET', '/assets/999999999', A()), 1005)
  })
})

describe('2.5 权限', () => {
  let appId
  it('角色列表 / 矩阵照 DB-SCHEMA', async () => {
    const roles = expectWrapped(await api('GET', '/roles', A()))
    const list = roles.items || roles
    expect(list.map(r => r.code)).toEqual(expect.arrayContaining(ROLES))
    const m = expectWrapped(await api('GET', '/permissions/matrix', A()))
    expect(m.resources).toEqual(['asset', 'model', 'dispatch', 'evidence', 'algo'])
    expect(m.actions).toEqual(['read', 'write', 'execute', 'issue', 'export'])
    const byCode = Object.fromEntries(m.roles.map(r => [r.code, r.grants]))
    expect(byCode.sys_admin.dispatch).toContain('issue')
    expect(byCode.grid_dispatcher.dispatch).toContain('issue')
    expect(byCode.vpp_operator.dispatch || []).not.toContain('issue')
    expect(byCode.regulator.asset).toContain('export')
    expect(byCode.energy_subject.asset).toContain('write')
    expect(byCode.sys_admin.algo).toContain('execute')
    expect(byCode.vpp_operator.algo || []).not.toContain('execute')
  })
  it('subject 申请 → admin 审批 → grants 可见 → check allowed', async () => {
    const assets = expectWrapped(await api('GET', '/assets?size=1', A()))
    const assetId = String(assets.items[0].id)
    const a = expectWrapped(await api('POST', '/permissions/apply', { token: tokens.subject, body: { resourceType: 'asset', resourceId: assetId, action: 'read', reason: 'qa 联合建模', expireAt: '2027-01-01T00:00:00+08:00' } }))
    appId = a.id
    expect(a.status).toBe('pending')
    expect(a.applicantDid).toMatch(DID_RE)
    const apps = expectPage(expectWrapped(await api('GET', '/permissions/applications?status=pending', A())))
    expect(apps.items.some(x => x.id === appId)).toBe(true)
    apps.items.forEach(x => expect(['pending', 'approved', 'rejected', 'expired']).toContain(x.status))
    const ap = expectWrapped(await api('POST', `/permissions/applications/${appId}/approve`, { ...A(), body: { comment: 'ok' } }))
    expect(ap.status).toBe('approved')
    const grants = expectWrapped(await api('GET', `/permissions/grants?did=${encodeURIComponent(a.applicantDid)}`, A()))
    const gl = grants.items || grants
    expect(gl.some(g => String(g.resourceId) === assetId && g.action === 'read')).toBe(true)
    const chk = expectWrapped(await api('POST', '/permissions/check', { ...A(), body: { did: a.applicantDid, resourceType: 'asset', resourceId: assetId, action: 'read' } }))
    expect(chk.allowed).toBe(true)
    const deny = expectWrapped(await api('POST', '/permissions/check', { ...A(), body: { did: a.applicantDid, resourceType: 'dispatch', resourceId: 'x', action: 'issue' } }))
    expect(deny.allowed).toBe(false)
    expect(typeof deny.reason).toBe('string')
    const g = gl.find(x => String(x.resourceId) === assetId && x.action === 'read')
    expectWrapped(await api('POST', `/permissions/grants/${g.id}/revoke`, { ...A(), body: { reason: 'qa' } }))
  })
  it('reject 流程', async () => {
    const a = expectWrapped(await api('POST', '/permissions/apply', { token: tokens.subject, body: { resourceType: 'asset', resourceId: '1', action: 'export', reason: 'x' } }))
    const r = expectWrapped(await api('POST', `/permissions/applications/${a.id}/reject`, { ...A(), body: { reason: 'no' } }))
    expect(r.status).toBe('rejected')
  })
})

describe('2.10 调度 + 越权', () => {
  let taskId
  it('创建 → run（DQN 策略 + 解释） → vpp issue 得 1003 → high 审计日志 + R01 告警 → admin issue 成功 → ack', async () => {
    const c = expectWrapped(await api('POST', '/dispatch/tasks', { ...A(), body: { name: 'qa-dispatch', nodeIds: ['Node-A', 'Node-B', 'Node-C', 'Node-D'], timeWindow: '2026-08-21T15:00~16:00+08:00' } }))
    taskId = c.id
    expect(TASK_STATUS).toContain(c.status)
    const run = expectWrapped(await api('POST', `/dispatch/tasks/${taskId}/run`, A()))
    expect(run.status).toBe('success')
    expect(run.strategy.actions.length).toBeGreaterThan(0)
    run.strategy.actions.forEach(a => { expect(['charge', 'idle', 'discharge']).toContain(a.action); expect(a.powerKw).toBeLessThanOrEqual(30) })
    expect(typeof run.strategy.totalReward).toBe('number')
    expect(typeof run.explanation).toBe('string')
    expect(['live', 'cache', 'rule']).toContain(run.explanationSource)
    expect(run.evidenceId).toMatch(/^ev-/)

    const traceId = 'tr-20260821-feedface'
    const deny = await api('POST', `/dispatch/tasks/${taskId}/issue`, { token: tokens.vpp, traceId, body: { signature: 'abc' } })
    expectWrapped(deny, 1003)
    const logs = expectPage(expectWrapped(await api('GET', `/audit/logs?riskLevel=high&size=200`, A())))
    const hit = logs.items.find(l => l.traceId === traceId)
    expect(hit, 'vpp 越权应写 high 审计日志且 traceId 沿用').toBeTruthy()
    expect(hit.result).toBe('denied')
    expect(hit.riskLevel).toBe('high')
    expect(hit.action).toBe('dispatch:issue')
    const alerts = expectWrapped(await api('GET', '/audit/alerts?status=open', A()))
    const al = (alerts.items || alerts)
    expect(al.some(x => x.ruleCode === 'R01_UNAUTHORIZED')).toBe(true)

    const trace = expectWrapped(await api('GET', `/audit/trace/${traceId}`, A()))
    expect(trace.traceId).toBe(traceId)
    expect(Array.isArray(trace.steps)).toBe(true)
    expect(trace.steps.length).toBeGreaterThan(0)
    expect(trace.steps[0].seq).toBe(1)
    expect(trace.summary).toBeTruthy()

    const ok = expectWrapped(await api('POST', `/dispatch/tasks/${taskId}/issue`, { ...A(), body: { signature: 'abcdef0123' } }))
    expect(ok.issued).toBe(true)
    expect(ok.commandId).toMatch(/^cmd-/)
    expect(ok.signerDid).toMatch(DID_RE)
    expect(Array.isArray(ok.targets)).toBe(true)
    const ack = expectWrapped(await api('POST', `/dispatch/tasks/${taskId}/ack`, { ...A(), body: { nodeId: ok.targets[0] || 'Node-C', result: 'success' } }))
    expect(ack).toBeTruthy()
    const list = expectPage(expectWrapped(await api('GET', '/dispatch/tasks', A())))
    expect(list.items.some(t => t.id === taskId)).toBe(true)
    const one = expectWrapped(await api('GET', `/dispatch/tasks/${taskId}`, A()))
    expect(one.id).toBe(taskId)
  }, 30000)
  it('ack 告警', async () => {
    const alerts = expectWrapped(await api('GET', '/audit/alerts?status=open', A()))
    const al = alerts.items || alerts
    if (!al.length) return
    const a = expectWrapped(await api('POST', `/audit/alerts/${al[0].id}/ack`, A()))
    expect(a.status || 'acked').toBe('acked')
  })
})

describe('2.6 存证与篡改', () => {
  it('写存证 → verify intact:true → tamper → verify intact:false → chain status brokenAt 非空；certificate；trace', async () => {
    const before = expectWrapped(await api('GET', '/evidence/chain/status', A()))
    expect(before.intact).toBe(true)
    expect(before.brokenAt).toBeNull()
    expect(before.height).toBeGreaterThan(0)
    expect(Object.keys(before.byCategory).sort()).toEqual(CATEGORY.slice().sort())

    const w = expectWrapped(await api('POST', '/evidence', { ...A(), body: { category: 'data', refId: 'qa-1', payload: { pvOutput: 1.5 }, actorDid: 'did:vpp:user:0x00000000000000000000000000000001' } }))
    expect(w.evidenceId).toMatch(/^ev-/)
    expect(w.hash).toMatch(HASH_RE)
    expect(typeof w.blockHeight).toBe('number')
    expect(w.txId).toBeTruthy()
    expect(w.prevHash).toBeTruthy()
    expect(w.timestamp).toMatch(ISO_RE)

    const v1 = expectWrapped(await api('POST', '/evidence/verify', { ...A(), body: { evidenceId: w.evidenceId } }))
    expect(v1.intact).toBe(true)

    const t = expectWrapped(await api('POST', '/evidence/demo/tamper', { ...A(), body: { evidenceId: w.evidenceId, newValue: { pvOutput: 999.9 } } }))
    expect(t.tampered).toBe(true)
    const v2 = expectWrapped(await api('POST', '/evidence/verify', { ...A(), body: { evidenceId: w.evidenceId } }))
    expect(v2.intact).toBe(false)
    expect(v2.localHash).not.toBe(v2.chainHash)
    expect(typeof v2.message).toBe('string')
    const after = expectWrapped(await api('GET', '/evidence/chain/status', A()))
    expect(after.intact).toBe(false)
    expect(after.brokenAt).toBe(w.blockHeight)

    const one = expectWrapped(await api('GET', `/evidence/${w.evidenceId}`, A()))
    expect(one.evidenceId).toBe(w.evidenceId)
    expect(CATEGORY).toContain(one.category)
    const cert = expectWrapped(await api('GET', `/evidence/${w.evidenceId}/certificate`, A()))
    expect(JSON.stringify(cert)).toContain(w.evidenceId)
    const page = expectPage(expectWrapped(await api('GET', '/evidence?category=data&size=10', A())))
    page.items.forEach(e => expect(e.category).toBe('data'))
    const tr = expectWrapped(await api('GET', `/evidence/trace/tr-20260821-feedface`, A()))
    expect(tr).toBeTruthy()
  })
  it('tamper 仅 sys_admin → vpp 得 1003', async () => {
    expectWrapped(await api('POST', '/evidence/demo/tamper', { token: tokens.vpp, body: { evidenceId: 'ev-000001', newValue: {} } }), 1003)
  })
})

describe('2.7 审计', () => {
  it('日志分页与枚举；stats；report 含 narrative；export 为文件流', async () => {
    const logs = expectPage(expectWrapped(await api('GET', '/audit/logs?page=1&size=20', A())))
    expect(logs.total).toBeGreaterThan(0)
    logs.items.forEach(l => { expect(RISK).toContain(l.riskLevel); expect(['success', 'failed', 'denied']).toContain(l.result); expect(l.traceId).toMatch(TRACE_RE); expect(l.at).toMatch(ISO_RE) })
    const st = expectWrapped(await api('GET', '/audit/stats', A()))
    for (const k of ['todayLogs', 'highRiskLogs', 'openAlerts', 'onChainLogs']) expect(typeof st[k]).toBe('number')
    expect(Array.isArray(st.byModule) && Array.isArray(st.byRisk) && Array.isArray(st.trend)).toBe(true)
    const today = new Date().toISOString().slice(0, 10)
    const rep = expectWrapped(await api('GET', `/audit/report?period=day&date=${today}`, A()))
    expect(rep.period).toBe('day')
    expect(typeof rep.narrative).toBe('string')
    expect(rep.narrative.length).toBeGreaterThan(10)
    expect(['live', 'cache', 'rule']).toContain(rep.narrativeSource)
    expect(rep.identityOps && rep.permissionOps && rep.evidence).toBeTruthy()
    expect(Array.isArray(rep.riskEvents)).toBe(true)
    const res = await api('GET', '/audit/logs/export?riskLevel=high', { ...A(), raw: true })
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type') || '').toMatch(/csv|octet-stream|text/)
    const text = await res.text()
    expect(text.startsWith('{')).toBe(false)
  })
})

describe('2.8 节点', () => {
  it('节点列表对齐种子；metrics；online 无合法签名 → 1004', async () => {
    const d = expectPage(expectWrapped(await api('GET', '/nodes', A())))
    expect(d.total).toBe(4)
    const c = d.items.find(n => n.id === 'Node-C')
    expect(c.name).toBe('虚拟电厂节点C')
    expect(c.model).toBe('VPP-3000')
    expect(c.did).toMatch(/^did:vpp:edge:/)
    expect(c.didStatus).toBe('active')
    expect(['online', 'warning', 'offline']).toContain(c.status)
    expect(c.metrics).toMatchObject({ load: expect.any(Number), soc: expect.any(Number), pvOutput: expect.any(Number), storageOutput: expect.any(Number) })
    const one = expectWrapped(await api('GET', '/nodes/Node-A', A()))
    expect(one.id).toBe('Node-A')
    const m = expectWrapped(await api('GET', '/nodes/Node-A/metrics?interval=day', A()))
    const arr = m.items || m.points || m
    expect(Array.isArray(arr)).toBe(true)
    expect(arr.length).toBeGreaterThanOrEqual(7)
    expectWrapped(await api('POST', '/nodes/Node-A/online', { ...A(), body: { did: 'did:vpp:edge:0x00000000000000000000000000000000', nonce: 'n', signature: 'bad' } }), 1004)
  })
})

describe('2.9 联邦学习（mock 真算）', () => {
  it('创建 → 启动 → 轮询至 success，rounds 长度 = rounds，loss 数值，压缩率 ≈ 90，ε 递增；参数不同曲线不同', async () => {
    const mk = async (topkRatio, dpEps) => {
      const c = expectWrapped(await api('POST', '/fl/tasks', { ...A(), body: { name: 'qa-fl', nodeIds: ['Node-A', 'Node-B', 'Node-C', 'Node-D'], rounds: 5, dp: { enabled: true, epsilon: dpEps, delta: 1e-5 }, topk: { enabled: true, ratio: topkRatio } } }))
      expect(c.status).toBe('created')
      expect(c.id).toMatch(/^fl-/)
      const s = expectWrapped(await api('POST', `/fl/tasks/${c.id}/start`, A()))
      expect(['running', 'created', 'success']).toContain(s.status)
      let task
      for (let i = 0; i < 300; i++) {
        task = expectWrapped(await api('GET', `/fl/tasks/${c.id}`, A()))
        expect(TASK_STATUS).toContain(task.status)
        if (task.status === 'success' || task.status === 'failed') break
        await sleep(100)
      }
      expect(task.status).toBe('success')
      return task
    }
    const t1 = await mk(0.1, 1.0)
    expect(t1.totalRounds).toBe(5)
    expect(t1.currentRound).toBe(5)
    expect(t1.rounds.length).toBe(5)
    expect(t1.nodes.length).toBe(4)
    t1.nodes.forEach(n => expect(n.did).toMatch(/^did:vpp:edge:/))
    t1.rounds.forEach((r, i) => {
      expect(r.round).toBe(i + 1)
      expect(Number.isFinite(r.loss)).toBe(true)
      expect(r.acc).toBeGreaterThanOrEqual(0)
      expect(r.acc).toBeLessThanOrEqual(1)
      expect(Math.abs(r.compressionRatio - 90)).toBeLessThan(3)
      expect(r.gradientHash).toMatch(/^(sm3:|sha256:)?[0-9a-f]{64}$/)
      expect(r.evidenceId).toMatch(/^ev-/)
    })
    const eps = t1.rounds.map(r => r.epsilonSpent)
    for (let i = 1; i < eps.length; i++) expect(eps[i]).toBeGreaterThanOrEqual(eps[i - 1])
    expect(t1.dp.epsilonSpent).toBeGreaterThan(0)
    expect(t1.topk.compressionRatio).toBeCloseTo(90, 0)
    expect(t1.modelVersion).toBeTruthy()
    const rounds = expectWrapped(await api('GET', `/fl/tasks/${t1.id}/rounds`, A()))
    expect((rounds.items || rounds).length).toBe(5)

    const t2 = await mk(0.5, 0.5)
    expect(t2.rounds.map(r => r.loss)).not.toEqual(t1.rounds.map(r => r.loss))
    expect(Math.abs(t2.rounds[0].compressionRatio - 50)).toBeLessThan(3)

    const models = expectWrapped(await api('GET', '/fl/models', A()))
    const ml = models.items || models
    expect(ml.length).toBeGreaterThan(0)
    const pub = expectWrapped(await api('POST', `/fl/models/${encodeURIComponent(t1.modelVersion)}/publish`, A()))
    expect(pub).toBeTruthy()
    const list = expectPage(expectWrapped(await api('GET', '/fl/tasks', A())))
    expect(list.total).toBeGreaterThanOrEqual(2)
  }, 60000)
  it('cancel 与 vpp 无 algo:execute → 1003', async () => {
    const c = expectWrapped(await api('POST', '/fl/tasks', { ...A(), body: { name: 'qa-cancel', nodeIds: ['Node-A', 'Node-B'], rounds: 50, dp: { enabled: false }, topk: { enabled: false } } }))
    await api('POST', `/fl/tasks/${c.id}/start`, A())
    const x = expectWrapped(await api('POST', `/fl/tasks/${c.id}/cancel`, A()))
    expect(x.status).toBe('cancelled')
    expectWrapped(await api('POST', `/fl/tasks/${c.id}/start`, { token: tokens.vpp }), 1003)
  })
})

describe('2.11 / 2.12 AI 与风险', () => {
  it('ai/analyze 永远成功，source 枚举；history 分页', async () => {
    for (const scene of ['dispatch', 'risk', 'data', 'qa', 'audit']) {
      const d = expectWrapped(await api('POST', '/ai/analyze', { ...A(), body: { scene, context: { taskId: 'dp-1' }, question: 'qa?' } }))
      expect(typeof d.answer).toBe('string')
      expect(d.answer.length).toBeGreaterThan(0)
      expect(Array.isArray(d.reasoning)).toBe(true)
      expect(['live', 'cache', 'rule']).toContain(d.source)
      expect(typeof d.latencyMs).toBe('number')
    }
    expectPage(expectWrapped(await api('GET', '/ai/history', A())))
  })
  it('risk/assess 因子权重和 ≈ 1，level 枚举；history', async () => {
    const d = expectWrapped(await api('POST', '/risk/assess', { ...A(), body: { nodeId: 'Node-A', features: { queryFreq: 12, dataGranularity: 'minute', exposedFields: 6 } } }))
    expect(d.nodeId).toBe('Node-A')
    expect(d.riskScore).toBeGreaterThanOrEqual(0)
    expect(d.riskScore).toBeLessThanOrEqual(100)
    expect(RISK).toContain(d.level)
    expect(Math.abs(d.factors.reduce((s, f) => s + f.weight, 0) - 1)).toBeLessThan(0.01)
    expect(typeof d.suggestion).toBe('string')
    expect(d.evidenceId).toMatch(/^ev-/)
    const h = expectWrapped(await api('GET', '/risk/history?nodeId=Node-A', A()))
    expect(Array.isArray(h.versions || h.items || h)).toBe(true)
  })
})

describe('WS mock 推送', () => {
  it('越权 issue 触发 audit_alert；存证写入触发 evidence_written', async () => {
    const { wsMock } = await import('@/mocks/wsMock.js')
    const got = []
    const off = (wsMock.subscribe || wsMock.on)(m => got.push(m))
    await api('POST', '/evidence', { ...A(), body: { category: 'data', refId: 'ws-1', payload: { a: 1 } } })
    const c = expectWrapped(await api('POST', '/dispatch/tasks', { ...A(), body: { name: 'ws', nodeIds: ['Node-A'] } }))
    await api('POST', `/dispatch/tasks/${c.id}/run`, A())
    await api('POST', `/dispatch/tasks/${c.id}/issue`, { token: tokens.vpp, body: { signature: 'x' } })
    await sleep(50)
    off && off()
    const types = got.map(m => m.type)
    expect(types).toContain('evidence_written')
    expect(types).toContain('audit_alert')
    const alert = got.find(m => m.type === 'audit_alert')
    expect(alert.payload.ruleCode).toBe('R01_UNAUTHORIZED')
    got.forEach(m => { expect(m).toHaveProperty('ts'); expect(m).toHaveProperty('payload') })
  }, 30000)
})
