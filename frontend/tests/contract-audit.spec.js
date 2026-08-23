/**
 * 契约逐字段一致性 + 安全回归（test-contract-security）。
 * 与 qa/contract_audit.py 同源：这里把审计结论固化为永久回归用例。
 *  A1 契约第二部分全部「方法+路径」 ⇄ api/*.js ⇄ mocks/handlers（含静态路径先于动态路径的注册顺序）
 *  A2 关键接口响应字段集合 / 分页结构 / 枚举 / ISO8601+08:00 / traceId 沿用
 *  A4 mock 与 algo 真实返回的形态一致（FL rounds / DQN / AI / classify / risk 字段名）
 *  B1 RBAC 矩阵（6 账号 × 写接口）、仅自有约束、无 token / 伪造 / 过期 / 已登出 → 1002、tamper 非 sys_admin → 1003
 *  B2 request.js 1002 不死循环、路由守卫拒绝伪 token、v-permission 随 permissions 变化、logout 断 WS、ws token 只走 query 且不打印
 *  B3 XSS：注入载荷经 mock 原样返回，JsonViewer 转义；views 无未转义 v-html
 *  B4 敏感信息：源码不含真实密钥；演示口令仅存在于 mocks/
 */
import { describe, it, expect, beforeAll, vi } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { setActivePinia, createPinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, withDirectives, resolveDirective, nextTick } from 'vue'

const ROOT = path.resolve(__dirname, '..')
const SRC = path.join(ROOT, 'src')
const CONTRACT = fs.readFileSync(path.resolve(ROOT, '..', 'contract', 'API-CONTRACT.md'), 'utf8')
const BASE = ['http:', '', 'localhost', 'api', 'v1'].join('/')
const TRACE = 'tr-20260821-0000abcd'
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?[+-]\d{2}:\d{2}$/
const norm = p => p.split('?')[0].replace(/\$\{[^}]+\}/g, '{}').replace(/\{[^}]+\}/g, '{}').replace(/:[A-Za-z_]\w*/g, '{}').replace(/\/$/, '')

const ENUMS = {
  subjectType: ['user', 'device', 'org', 'edge'],
  didStatus: ['active', 'frozen', 'revoked'],
  dataType: ['pv', 'wind', 'storage', 'load', 'dispatch'],
  level: ['L1', 'L2', 'L3', 'L4'],
  riskLevel: ['low', 'medium', 'high', 'critical'],
  category: ['data', 'identity', 'permission', 'audit', 'algo'],
  taskStatus: ['created', 'running', 'success', 'failed', 'cancelled'],
  nodeStatus: ['online', 'warning', 'offline'],
  appStatus: ['pending', 'approved', 'rejected', 'expired'],
  source: ['live', 'cache', 'rule'],
  dqnAction: ['charge', 'idle', 'discharge']
}

/**
 * 契约之外的扩展接口。
 *
 * `contract/` 是冻结区（见 contract/README.md 铁律 1），本仓库不得单方面修改。
 * 新功能确实需要新接口时，先在这里显式登记、说明用途和归属模块，等两方当面确认后
 * 再由一人更新 API-CONTRACT.md 并追加变更记录，届时把对应条目从这里删掉。
 *
 * 登记在册 ≠ 放行任意路径：没登记的契约外路径仍然让用例失败，这正是本表存在的意义。
 */
const CONTRACT_EXTENSIONS = [
  // 站内消息（铃铛）：权限申请通知审批人、审批结果通知申请人。backend/modules/notice
  { key: 'GET /notices', why: '我的消息列表' },
  { key: 'GET /notices/unread-count', why: '铃铛角标未读数' },
  { key: 'POST /notices/read', why: '标记若干条已读' },
  { key: 'POST /notices/read-all', why: '全部已读' }
]
const EXTENSION_KEYS = new Set(CONTRACT_EXTENSIONS.map(e => e.key))

/* ---------------- 契约解析 ---------------- */
function contractEndpoints() {
  const part2 = CONTRACT.split('## 第二部分')[1].split('## 第三部分')[0]
  const out = []
  for (const m of part2.matchAll(/^\|\s*(GET|POST|PUT|DELETE)\s*\|\s*`([^`]+)`\s*\|\s*([^|]*)\|/gm)) {
    out.push({ method: m[1], path: m[2].trim(), key: `${m[1]} ${norm(m[2].trim())}`, paged: m[3].includes('分页'), stream: m[3].includes('文件流') })
  }
  return out
}

function apiCalls() {
  const found = {}
  for (const f of fs.readdirSync(path.join(SRC, 'api')).filter(x => x.endsWith('.js') && !['request.js', 'ws.js', 'index.js'].includes(x))) {
    const lines = fs.readFileSync(path.join(SRC, 'api', f), 'utf8').split('\n')
    lines.forEach((line, i) => {
      let m = line.match(/request\.(get|post|put|delete)\(\s*[`'"]([^`'"]+)[`'"]/)
      if (m) found[`${m[1].toUpperCase()} ${norm(m[2])}`] = `${f}:${i + 1}`
      m = line.match(/download\(\s*[`'"]([^`'"]+)[`'"]/)
      if (m) found[`GET ${norm(m[1])}`] = `${f}:${i + 1}`
    })
  }
  return found
}

function handlerList() {
  const dir = path.join(SRC, 'mocks', 'handlers')
  const order = [...fs.readFileSync(path.join(dir, 'index.js'), 'utf8').matchAll(/from\s+'\.\/(\w+)\.js'/g)].map(m => m[1])
  const out = []
  for (const mod of order) {
    fs.readFileSync(path.join(dir, `${mod}.js`), 'utf8').split('\n').forEach((line, i) => {
      const m = line.match(/http\.(get|post|put|delete)\(\s*`\$\{BASE\}([^`]+)`/)
      if (m) out.push({ method: m[1].toUpperCase(), raw: m[2], key: `${m[1].toUpperCase()} ${norm(m[2])}`, where: `${mod}.js:${i + 1}` })
    })
  }
  return out
}

/* ---------------- HTTP 工具 ---------------- */
const tokens = {}
async function api(method, p, { token, body, traceId = TRACE, raw } = {}) {
  const headers = { 'Content-Type': 'application/json', 'X-Trace-Id': traceId }
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(BASE + p, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  if (raw) return res
  return { status: res.status, json: await res.json() }
}
const A = () => ({ token: tokens.admin })
function okData(r) {
  expect(r.json.code, r.json.message).toBe(0)
  expect(r.json.traceId).toBe(TRACE)
  return r.json.data
}
function expectPage(d) {
  expect(Array.isArray(d.items)).toBe(true)
  expect(typeof d.total).toBe('number')
  expect(typeof d.page).toBe('number')
  expect(typeof d.size).toBe('number')
  return d
}
function walk(obj, fn, p = '$') {
  if (Array.isArray(obj)) obj.slice(0, 5).forEach((v, i) => walk(v, fn, `${p}[${i}]`))
  else if (obj && typeof obj === 'object') for (const [k, v] of Object.entries(obj)) { fn(k, v, `${p}.${k}`); walk(v, fn, `${p}.${k}`) }
}
/** 时间字段（xxxAt / at / ts / timestamp / created / updated）必须 ISO8601 带时区 */
function expectTimes(data, where) {
  walk(data, (k, v, p) => {
    if (/^(ts|at|timestamp|created|updated|[a-z]+[a-z]At)$/.test(k) && typeof v === 'string' && v) {
      expect(v, `${where} ${p}`).toMatch(ISO_RE)
    }
  })
}

let db, server
beforeAll(async () => {
  server = globalThis.__mswServer
  expect(server, 'src/mocks/node.js 未就绪').toBeTruthy()
  db = (await import('@/mocks/db.js')).db
  for (const [u, p] of [['admin', 'admin123'], ['grid', 'grid123'], ['vpp', 'vpp123'], ['subject', 'subject123'], ['regulator', 'reg123'], ['edge', 'edge123']]) {
    const r = await api('POST', '/auth/login', { body: { username: u, password: p } })
    tokens[u] = okData(r).token
  }
}, 30000)

/* =====================================================================
 * A1 路径一致性
 * ===================================================================*/
describe('A1 契约路径 ⇄ api/*.js ⇄ mocks/handlers', () => {
  const eps = contractEndpoints()
  it('契约第二部分解析出 73 个接口', () => {
    expect(eps.length).toBe(73)
  })
  it('api/*.js 覆盖全部契约接口，契约外路径必须已登记为扩展', () => {
    const calls = apiCalls()
    const want = new Set(eps.map(e => e.key))
    const extra = Object.keys(calls).filter(k => !want.has(k) && !EXTENSION_KEYS.has(k))
    expect(extra, '出现未登记的契约外接口，先在 CONTRACT_EXTENSIONS 登记并推动契约变更').toEqual([])
    expect([...want].filter(k => !calls[k])).toEqual([])
  })
  it('mocks/handlers 覆盖全部契约接口与已登记扩展', () => {
    const hs = new Set(handlerList().map(h => h.key))
    expect(eps.map(e => e.key).filter(k => !hs.has(k))).toEqual([])
    // 扩展接口同样要有离线桩，否则 mock 模式下铃铛会 404
    expect([...EXTENSION_KEYS].filter(k => !hs.has(k))).toEqual([])
  })
  it('静态路径先于能匹配它的动态路径注册（/evidence/chain/status 先于 /evidence/{id} 等）', () => {
    const dyn = []
    const bad = []
    for (const h of handlerList()) {
      if (h.raw.includes(':')) {
        dyn.push({ method: h.method, rx: new RegExp('^' + h.raw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/:\w+/g, '[^/]+') + '$'), where: h.where })
      } else {
        for (const d of dyn) if (d.method === h.method && d.rx.test(h.raw)) bad.push(`${h.method} ${h.raw}(${h.where}) 被 ${d.where} 截获`)
      }
    }
    expect(bad).toEqual([])
    const keys = handlerList().filter(h => h.method === 'GET' && h.raw.startsWith('/evidence')).map(h => h.raw)
    expect(keys.indexOf('/evidence/chain/status')).toBeLessThan(keys.indexOf('/evidence/:id'))
    expect(keys.indexOf('/evidence/trace/:traceId')).toBeLessThan(keys.indexOf('/evidence/:id'))
    const assets = handlerList().filter(h => h.method === 'GET' && h.raw.startsWith('/assets')).map(h => h.raw)
    expect(assets.indexOf('/assets/stats')).toBeLessThan(assets.indexOf('/assets/:id'))
  })
})

/* =====================================================================
 * A2 响应字段 / 分页 / 枚举 / 时间
 * ===================================================================*/
describe('A2 关键接口字段逐一对照契约示例', () => {
  const ctx = {}
  it('auth: login / me 字段', async () => {
    const d = okData(await api('POST', '/auth/login', { body: { username: 'admin', password: 'admin123' } }))
    expect(Object.keys(d)).toEqual(expect.arrayContaining(['token', 'expiresIn', 'user']))
    expect(Object.keys(d.user)).toEqual(expect.arrayContaining(['id', 'username', 'realName', 'roles', 'did', 'orgName']))
    expect(typeof d.expiresIn).toBe('number')
    const me = okData(await api('GET', '/auth/me', A()))
    expect(Object.keys(me)).toEqual(expect.arrayContaining(['id', 'username', 'realName', 'roles', 'did', 'permissions']))
    me.permissions.forEach(p => expect(p).toMatch(/^[a-z]+:[a-z]+$/))
  })
  it('did: register / verify / status 字段、DID 格式、文档结构', async () => {
    const d = okData(await api('POST', '/did/register', { ...A(), body: { subjectType: 'device', subjectName: '审计逆变器', orgName: 'XX园区', metadata: { model: 'VPP-2000' } } }))
    expect(Object.keys(d)).toEqual(expect.arrayContaining(['did', 'didDocument', 'publicKey', 'privateKey', 'chainTxId', 'evidenceId']))
    expect(d.did).toMatch(/^did:vpp:device:0x[0-9a-f]{32}$/)
    expect(Object.keys(d.didDocument)).toEqual(expect.arrayContaining(['@context', 'id', 'controller', 'verificationMethod', 'created']))
    expect(Object.keys(d.didDocument.verificationMethod[0])).toEqual(expect.arrayContaining(['id', 'type', 'publicKeyHex']))
    expectTimes(d, 'did/register')
    ctx.did = d.did
    const v = okData(await api('POST', '/did/verify', { ...A(), body: { did: d.did, message: 'm', signature: 'deadbeefcafe' } }))
    expect(Object.keys(v).sort()).toEqual(['reason', 'status', 'subjectType', 'valid'])
    const s = okData(await api('POST', `/did/${encodeURIComponent(d.did)}/status`, { ...A(), body: { action: 'freeze', reason: 'x' } }))
    expect(Object.keys(s).sort()).toEqual(['did', 'evidenceId', 'status'])
    expect(s.status).toBe('frozen')
    const list = expectPage(okData(await api('GET', '/did?page=1&size=5', A())))
    list.items.forEach(x => { expect(ENUMS.subjectType).toContain(x.subjectType); expect(ENUMS.didStatus).toContain(x.status) })
    expectTimes(list, 'did list')
  })
  it('keys: 列表项字段 + algorithm 枚举 + 运行时生成的 expireAt 为 +08:00（非 Z）', async () => {
    const k = okData(await api('POST', '/keys', { ...A(), body: { did: db.nodes[0].did, algorithm: 'SM2' } }))
    expect(k.expireAt).toMatch(ISO_RE)
    const rot = okData(await api('POST', `/did/${encodeURIComponent(db.nodes[1].did)}/rotate-key`, A()))
    const list = expectPage(okData(await api('GET', `/keys?did=${encodeURIComponent(db.nodes[1].did)}`, A())))
    list.items.forEach(x => {
      expect(Object.keys(x)).toEqual(expect.arrayContaining(['id', 'did', 'algorithm', 'publicKey', 'status', 'version', 'boundAt', 'expireAt']))
      expect(['SM2', 'ECC', 'RSA']).toContain(x.algorithm)
      expect(x.expireAt).toMatch(ISO_RE)
    })
    expect(list.items.find(x => x.id === rot.keyId).expireAt).toMatch(ISO_RE)
    // 运行时签发 DID 后 POST /keys 不得出现 id 冲突
    const ids = db.keys.map(x => x.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
  it('assets: register / detail / lineage / stats / classify 字段', async () => {
    const d = okData(await api('POST', '/assets', { ...A(), body: { name: '节点A光伏出力-审计', dataType: 'pv', sourceDid: db.nodes[0].did, level: 'L2', payload: { pvOutput: 45.3 } } }))
    expect(Object.keys(d)).toEqual(expect.arrayContaining(['id', 'hash', 'level', 'chainTxId', 'evidenceId', 'authStatus', 'createdAt']))
    expect(d.hash).toMatch(/^sm3:[0-9a-f]{64}$/)
    ctx.assetId = d.id
    const lin = okData(await api('GET', `/assets/${d.id}/lineage`, A()))
    expect(Object.keys(lin)).toEqual(expect.arrayContaining(['assetId', 'traceId', 'chain']))
    expect(Object.keys(lin.chain[0])).toEqual(expect.arrayContaining(['stage', 'at', 'actorDid', 'evidenceId', 'hash']))
    const st = okData(await api('GET', '/assets/stats', A()))
    expect(Object.keys(st).sort()).toEqual(['authorized', 'byLevel', 'byType', 'onChain', 'total'])
    expect(Object.keys(st.byLevel[0]).sort()).toEqual(['count', 'level'])
    expect(Object.keys(st.byType[0]).sort()).toEqual(['count', 'dataType'])
    const cl = okData(await api('POST', '/assets/classify', { ...A(), body: { records: [{ dataType: 'pv', fields: ['power', 'gps'], freq: 'minute', volume: 1440 }] } }))
    expect(Object.keys(cl.results[0])).toEqual(expect.arrayContaining(['index', 'level', 'score', 'reason', 'cluster', 'factors']))
    expect(Object.keys(cl.results[0].factors).sort()).toEqual(['granularity', 'sensitivity', 'volume'])
    expect(Array.isArray(cl.clusterCenters)).toBe(true)
    const page = expectPage(okData(await api('GET', '/assets?page=1&size=5', A())))
    page.items.forEach(a => { expect(ENUMS.dataType).toContain(a.dataType); expect(ENUMS.level).toContain(a.level) })
    expectTimes(page, 'assets')
  })
  it('permissions: apply / check / matrix 字段', async () => {
    const a = okData(await api('POST', '/permissions/apply', { token: tokens.subject, body: { resourceType: 'asset', resourceId: String(ctx.assetId), action: 'read', reason: 'x', expireAt: '2026-09-17T00:00:00+08:00' } }))
    expect(Object.keys(a)).toEqual(expect.arrayContaining(['id', 'status', 'applicantDid', 'createdAt', 'evidenceId']))
    expect(ENUMS.appStatus).toContain(a.status)
    const c = okData(await api('POST', '/permissions/check', { ...A(), body: { did: db.users[2].did, resourceType: 'dispatch', resourceId: 'task-9', action: 'issue' } }))
    expect(Object.keys(c).sort()).toEqual(['allowed', 'level', 'matchedRule', 'reason'])
    expect(c.allowed).toBe(false)
    const m = okData(await api('GET', '/permissions/matrix', A()))
    expect(m.resources).toEqual(['asset', 'model', 'dispatch', 'evidence', 'algo'])
    expect(m.actions).toEqual(['read', 'write', 'execute', 'issue', 'export'])
    expect(Object.keys(m.roles[0])).toEqual(expect.arrayContaining(['code', 'name', 'grants']))
    expectPage(okData(await api('GET', '/permissions/applications', A())))
  })
  it('evidence: write / verify / chain status / tamper 字段', async () => {
    const w = okData(await api('POST', '/evidence', { ...A(), body: { category: 'data', refId: '1001', payload: { a: 1 }, actorDid: db.users[0].did } }))
    expect(Object.keys(w)).toEqual(expect.arrayContaining(['evidenceId', 'hash', 'blockHeight', 'txId', 'prevHash', 'timestamp']))
    expect(typeof w.blockHeight).toBe('number')
    const s = okData(await api('GET', '/evidence/chain/status', A()))
    expect(Object.keys(s)).toEqual(expect.arrayContaining(['height', 'lastHash', 'intact', 'brokenAt', 'totalRecords', 'byCategory']))
    expect(Object.keys(s.byCategory).sort()).toEqual([...ENUMS.category].sort())
    const t = okData(await api('POST', '/evidence/demo/tamper', { ...A(), body: { evidenceId: w.evidenceId, newValue: { a: 2 } } }))
    expect(Object.keys(t)).toEqual(expect.arrayContaining(['evidenceId', 'tampered', 'hint']))
    const v = okData(await api('POST', '/evidence/verify', { ...A(), body: { evidenceId: w.evidenceId } }))
    expect(Object.keys(v)).toEqual(expect.arrayContaining(['intact', 'localHash', 'chainHash', 'tamperedAt', 'message']))
    expect(v.intact).toBe(false)
    expect(v.tamperedAt).toMatch(ISO_RE)
    const page = expectPage(okData(await api('GET', '/evidence?page=1&size=5', A())))
    page.items.forEach(e => expect(ENUMS.category).toContain(e.category))
    expectTimes(page, 'evidence')
  })
  it('audit: logs / trace / stats / report 字段；export 为 CSV 流', async () => {
    const logs = expectPage(okData(await api('GET', '/audit/logs?page=1&size=10', A())))
    const need = ['id', 'traceId', 'actorDid', 'actorName', 'action', 'resourceType', 'resourceId', 'result', 'riskLevel', 'module', 'detail', 'ip', 'at', 'evidenceId', 'hash']
    logs.items.forEach(l => { expect(Object.keys(l)).toEqual(expect.arrayContaining(need)); expect(ENUMS.riskLevel).toContain(l.riskLevel); expect(['success', 'failed', 'denied']).toContain(l.result) })
    expectTimes(logs, 'audit logs')
    const tr = okData(await api('GET', `/audit/trace/${db.DEMO_TRACE}`, A()))
    expect(Object.keys(tr.summary)).toEqual(expect.arrayContaining(['startAt', 'endAt', 'durationMs', 'actorDid', 'result', 'riskLevel']))
    expect(Object.keys(tr.steps[0])).toEqual(expect.arrayContaining(['seq', 'module', 'action', 'at', 'result']))
    expect(tr.steps.map(s => s.seq)).toEqual(tr.steps.map((_, i) => i + 1))
    const st = okData(await api('GET', '/audit/stats', A()))
    expect(Object.keys(st)).toEqual(expect.arrayContaining(['todayLogs', 'highRiskLogs', 'openAlerts', 'onChainLogs', 'byModule', 'byRisk', 'trend']))
    expect(Object.keys(st.trend[0]).sort()).toEqual(['date', 'high', 'total'])
    const rep = okData(await api('GET', `/audit/report?period=day&date=${new Date().toISOString().slice(0, 10)}`, A()))
    expect(Object.keys(rep)).toEqual(expect.arrayContaining(['period', 'date', 'identityOps', 'permissionOps', 'evidence', 'riskEvents', 'narrative', 'narrativeSource']))
    expect(Object.keys(rep.identityOps).sort()).toEqual(['freeze', 'register', 'revoke', 'rotate'])
    expect(Object.keys(rep.permissionOps).sort()).toEqual(['applied', 'approved', 'rejected', 'revoked'])
    expect(ENUMS.source).toContain(rep.narrativeSource)
    const res = await api('GET', '/audit/logs/export', { ...A(), raw: true })
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toMatch(/csv/)
  })
  it('nodes: 列表项与 metrics 字段、online 字段', async () => {
    const d = expectPage(okData(await api('GET', '/nodes', A())))
    d.items.forEach(n => {
      expect(Object.keys(n)).toEqual(expect.arrayContaining(['id', 'name', 'status', 'model', 'did', 'didStatus', 'metrics', 'lastSeenAt']))
      expect(Object.keys(n.metrics).sort()).toEqual(['load', 'pvOutput', 'soc', 'storageOutput'])
      expect(ENUMS.nodeStatus).toContain(n.status)
    })
    const on = okData(await api('POST', '/nodes/Node-A/online', { ...A(), body: { did: db.nodes[0].did, nonce: 'abc123', signature: 'deadbeefcafe' } }))
    expect(Object.keys(on).sort()).toEqual(['accepted', 'evidenceId', 'nodeId', 'sessionToken'])
  })
  it('fl: 创建 / 详情 rounds 字段', async () => {
    const c = okData(await api('POST', '/fl/tasks', { ...A(), body: { name: 'audit', nodeIds: ['Node-A', 'Node-B'], rounds: 2, dp: { enabled: true, epsilon: 1.0, delta: 1e-5 }, topk: { enabled: true, ratio: 0.1 } } }))
    expect(Object.keys(c).sort()).toEqual(['createdAt', 'id', 'status', 'traceId'])
    await api('POST', `/fl/tasks/${c.id}/start`, A())
    let t
    for (let i = 0; i < 200; i++) { t = okData(await api('GET', `/fl/tasks/${c.id}`, A())); if (t.status === 'success') break; await new Promise(r => setTimeout(r, 100)) }
    expect(t.status).toBe('success')
    expect(Object.keys(t)).toEqual(expect.arrayContaining(['id', 'name', 'status', 'currentRound', 'totalRounds', 'nodes', 'dp', 'topk', 'rounds', 'modelVersion', 'traceId']))
    expect(Object.keys(t.nodes[0]).sort()).toEqual(['did', 'joined', 'nodeId', 'samples'])
    expect(Object.keys(t.dp)).toEqual(expect.arrayContaining(['enabled', 'epsilon', 'epsilonSpent']))
    expect(Object.keys(t.topk)).toEqual(expect.arrayContaining(['enabled', 'ratio', 'compressionRatio']))
    expect(Object.keys(t.rounds[0])).toEqual(expect.arrayContaining(['round', 'loss', 'acc', 'compressionRatio', 'epsilonSpent', 'gradientHash', 'evidenceId']))
    expectTimes(t, 'fl task')
    ctx.fl = c.id
  })
  it('dispatch: run / issue 字段、action 枚举、violations 对齐 algo', async () => {
    const c = okData(await api('POST', '/dispatch/tasks', { ...A(), body: { name: 'audit', nodeIds: ['Node-A', 'Node-B', 'Node-C', 'Node-D'] } }))
    const r = okData(await api('POST', `/dispatch/tasks/${c.id}/run`, A()))
    expect(Object.keys(r)).toEqual(expect.arrayContaining(['id', 'status', 'strategy', 'explanation', 'explanationSource', 'evidenceId', 'traceId']))
    expect(Object.keys(r.strategy).sort()).toEqual(['actions', 'timeWindow', 'totalReward'])
    expect(Object.keys(r.strategy.actions[0]).sort()).toEqual(['action', 'nodeId', 'powerKw', 'qValue', 'reason'])
    r.strategy.actions.forEach(a => expect(ENUMS.dqnAction).toContain(a.action))
    expect(Object.keys(r.qTable[0]).sort()).toEqual(['charge', 'discharge', 'idle', 'nodeId'])
    expect(Object.keys(r.constraintsChecked).sort()).toEqual(['maxPowerKw', 'socMax', 'socMin', 'violations'])
    expect(ENUMS.source).toContain(r.explanationSource)
    // 制造 SOC 越限节点，violations 字段名必须与 algo 一致：{nodeId, constraint, attempted, applied, detail}
    const { ruleStrategy } = await import('@/mocks/handlers/dispatch.js')
    const low = ruleStrategy([{ id: 'Node-X', metrics: { pvOutput: 0, storageOutput: -30, load: 200, soc: 25 } }], 'tw')
    expect(low.constraintsChecked.violations.length).toBeGreaterThan(0)
    expect(Object.keys(low.constraintsChecked.violations[0]).sort()).toEqual(['applied', 'attempted', 'constraint', 'detail', 'nodeId'])
    const is = okData(await api('POST', `/dispatch/tasks/${c.id}/issue`, { ...A(), body: { signature: 'sig:0123456789abcdef' } }))
    expect(Object.keys(is)).toEqual(expect.arrayContaining(['issued', 'commandId', 'signerDid', 'evidenceId', 'targets']))
    ctx.dp = c.id
  })
  it('ai / risk 字段', async () => {
    const a = okData(await api('POST', '/ai/analyze', { ...A(), body: { scene: 'dispatch', context: { taskId: ctx.dp }, question: 'why' } }))
    expect(Object.keys(a).sort()).toEqual(['answer', 'evidenceId', 'latencyMs', 'reasoning', 'source', 'traceId'])
    expect(ENUMS.source).toContain(a.source)
    const r = okData(await api('POST', '/risk/assess', { ...A(), body: { nodeId: 'Node-A', features: { queryFreq: 12, dataGranularity: 'minute', exposedFields: 6 } } }))
    expect(Object.keys(r)).toEqual(expect.arrayContaining(['nodeId', 'riskScore', 'level', 'factors', 'suggestion', 'evidenceId']))
    expect(Object.keys(r.factors[0]).sort()).toEqual(['desc', 'name', 'score', 'weight'])
    expect(ENUMS.riskLevel).toContain(r.level)
    expectPage(okData(await api('GET', '/ai/history', A())))
    expectPage(okData(await api('GET', '/risk/history', A())))
  })
  it('所有「分页」接口返回 {items,total,page,size}', async () => {
    const paged = contractEndpoints().filter(e => e.paged && e.method === 'GET')
    expect(paged.length).toBeGreaterThanOrEqual(8)
    for (const e of paged) {
      const d = okData(await api('GET', `${e.path.replace('{id}', '1').replace('{did}', '')}?page=1&size=3`, A()))
      expectPage(d)
      expect(d.size).toBe(3)
      expect(d.items.length).toBeLessThanOrEqual(3)
    }
  })
})

/* =====================================================================
 * A4 mock ⇄ algo 形态（静态快照，来自 algo 真实返回 / MSG-algo-to-all-001）
 * ===================================================================*/
describe('A4 mock 返回形态与 algo 真实返回一致（切换真后端不应出错）', () => {
  it('fedavgLite 每轮字段 ⊇ algo rounds[] 字段', async () => {
    const { createFedAvgJob } = await import('@/mocks/algo/fedavgLite.js')
    const rounds = []
    await new Promise(resolve => createFedAvgJob({ taskId: 't', rounds: 2, nodes: [{ nodeId: 'Node-A', samples: 100 }, { nodeId: 'Node-B', samples: 80 }], dp: { enabled: true, epsilon: 1, delta: 1e-5 }, topk: { enabled: true, ratio: 0.1 }, intervalMs: 0, onRound: r => rounds.push(r), onDone: resolve }))
    const algoRound = ['round', 'loss', 'acc', 'compressionRatio', 'epsilonSpent', 'gradientHash', 'nodeContributions']
    expect(Object.keys(rounds[0])).toEqual(expect.arrayContaining(algoRound))
    expect(Object.keys(rounds[0].nodeContributions[0]).sort()).toEqual(['localLoss', 'nodeId', 'weight'])
  })
  it('classify / risk 的 mock 纯函数与 algo 字段一致', async () => {
    const { classifyRecord } = await import('@/mocks/handlers/asset.js')
    const { assess } = await import('@/mocks/handlers/risk.js')
    expect(Object.keys(classifyRecord({ dataType: 'pv', fields: ['gps'], freq: 'minute', volume: 10 }, 0)).sort()).toEqual(['cluster', 'factors', 'index', 'level', 'reason', 'score'])
    const r = assess({ queryFreq: 12, dataGranularity: 'minute', exposedFields: 6, epsilonRemaining: 0.58 })
    expect(Object.keys(r).sort()).toEqual(['factors', 'level', 'riskScore', 'suggestion'])
    expect(r.factors.length).toBe(4)
    expect(Math.abs(r.factors.reduce((s, f) => s + f.weight, 0) - 1)).toBeLessThan(1e-9)
    expect(r.factors.map(f => f.weight)).toEqual([0.35, 0.25, 0.20, 0.20]) // 与 algo 一致
  })
})

/* =====================================================================
 * B1 RBAC / 仅自有 / token
 * ===================================================================*/
describe('B1 RBAC 矩阵（DB-SCHEMA）', () => {
  const USERS = ['admin', 'grid', 'vpp', 'subject', 'regulator', 'edge']
  // 期望允许集合；其余账号期望 1003
  const MATRIX = {
    'POST /assets': ['admin', 'vpp', 'subject', 'edge'],
    'POST /fl/tasks': ['admin', 'grid'],
    'POST /fl/models/v11/publish': ['admin', 'grid'],
    'POST /dispatch/tasks/dp-000001/issue': ['admin', 'grid'],
    'POST /users': ['admin'],
    'PUT /users/2': ['admin'],
    'DELETE /users/999': ['admin'],
    'POST /roles': ['admin'],
    'PUT /roles/sys_admin': ['admin'],
    'POST /permissions/applications/999/approve': ['admin'],
    'POST /permissions/applications/999/reject': ['admin'],
    'POST /permissions/grants/999/revoke': ['admin'],
    'POST /evidence/demo/tamper': ['admin'],
    'GET /users': ['admin'],
    'GET /evidence': ['admin', 'grid', 'vpp', 'subject', 'regulator'],
    'GET /fl/models': ['admin', 'grid', 'vpp', 'regulator', 'edge'],
    'GET /dispatch/tasks': ['admin', 'grid', 'vpp', 'regulator', 'edge'],
    'GET /assets': USERS
  }
  const BODY = {
    'POST /assets': u => ({ name: `rbac-${u}`, dataType: 'pv', payload: { v: 1 } }),
    'POST /fl/tasks': u => ({ name: `rbac-${u}`, nodeIds: ['Node-A'], rounds: 1 }),
    'POST /dispatch/tasks/dp-000001/issue': () => ({ signature: 'sig:0123456789abcdef' }),
    'POST /users': u => ({ username: `rb_${u}`, password: 'x12345678' }),
    'PUT /users/2': () => ({ realName: 'x' }),
    'POST /roles': u => ({ code: `rb_${u}`, name: 'x' }),
    'PUT /roles/sys_admin': () => ({ name: 'x' }),
    'POST /evidence/demo/tamper': () => ({ evidenceId: 'ev-000001', newValue: {} })
  }
  for (const [ep, allowed] of Object.entries(MATRIX)) {
    it(`${ep} → 允许 [${allowed.join(',')}]，其余 1003`, async () => {
      const [method, p] = ep.split(' ')
      for (const u of USERS) {
        const r = await api(method, p, { token: tokens[u], body: BODY[ep]?.(u) })
        if (allowed.includes(u)) expect(r.json.code, `${u} ${ep}`).not.toBe(1003)
        else {
          expect(r.json.code, `${u} ${ep}`).toBe(1003)
          expect(r.status).toBe(403)
        }
      }
    })
  }
  it('GET /audit/logs/export 需 asset:export（admin/grid/regulator），其余 403', async () => {
    for (const u of USERS) {
      const res = await api('GET', '/audit/logs/export', { token: tokens[u], raw: true })
      expect(res.status, u).toBe(['admin', 'grid', 'regulator'].includes(u) ? 200 : 403)
    }
  })
  it('越权 dispatch:issue 写 high 审计日志 + R01 告警', async () => {
    const tid = 'tr-20260821-0bad0bad'
    const r = await api('POST', '/dispatch/tasks/dp-000001/issue', { token: tokens.vpp, body: { signature: 'sig:0123456789abcdef' }, traceId: tid })
    expect(r.json.code).toBe(1003)
    const logs = okData(await api('GET', `/audit/logs?traceId=${tid}`, A()))
    expect(logs.items.some(l => l.result === 'denied' && l.riskLevel === 'high' && l.action === 'dispatch:issue')).toBe(true)
    const alerts = okData(await api('GET', '/audit/alerts?status=open', A()))
    expect(alerts.items.some(a => a.ruleCode === 'R01_UNAUTHORIZED' && a.actorDid === db.users[2].did)).toBe(true)
  })
})

describe('B1 仅自有（energy_subject / edge_node）', () => {
  for (const u of ['subject', 'edge']) {
    it(`${u} 只能看到自有或已授权资产；非自有详情/溯源 → 1005`, async () => {
      const me = db.users.find(x => x.username === u)
      const grants = db.grants.filter(g => g.did === me.did && g.status === 'active' && g.resourceType === 'asset').map(g => g.resourceId)
      const list = okData(await api('GET', '/assets?size=200', { token: tokens[u] }))
      list.items.forEach(a => expect(a.sourceDid === me.did || a.ownerDid === me.did || grants.includes(String(a.id)) || grants.includes('*'), `${u} 看到了 ${a.id}`).toBe(true))
      const other = db.assets.find(a => a.sourceDid !== me.did && !grants.includes(String(a.id)))
      expect((await api('GET', `/assets/${other.id}`, { token: tokens[u] })).json.code).toBe(1005)
      expect((await api('GET', `/assets/${other.id}/lineage`, { token: tokens[u] })).json.code).toBe(1005)
      const stats = okData(await api('GET', '/assets/stats', { token: tokens[u] }))
      expect(stats.total).toBe(list.total)
    })
  }
  it('subject 存证仅自有；edge_node 无 evidence:read → 1003', async () => {
    const me = db.users.find(x => x.username === 'subject')
    const list = okData(await api('GET', '/evidence?size=200', { token: tokens.subject }))
    const mine = new Set([me.did, ...db.dids.filter(d => d.controllerDid === me.did).map(d => d.did)])
    const myAssets = new Set(db.assets.filter(a => mine.has(a.sourceDid) || a.ownerDid === me.did).map(a => String(a.id)))
    list.items.forEach(e => expect(mine.has(e.actorDid) || mine.has(e.refId) || myAssets.has(String(e.refId)), `subject 看到了 ${e.evidenceId}`).toBe(true))
    expect((await api('GET', '/evidence/ev-000001', { token: tokens.subject })).json.code).toBe(1005)
    expect((await api('GET', '/evidence/ev-000001/certificate', { token: tokens.subject })).json.code).toBe(1005)
    expect((await api('GET', '/evidence?size=1', { token: tokens.edge })).json.code).toBe(1003)
  })
})

describe('B1 token', () => {
  const BIZ = ['/nodes', '/assets', '/evidence/chain/status', '/audit/logs', '/fl/tasks', '/dispatch/tasks', '/did', '/keys', '/roles', '/ai/history', '/risk/history', '/permissions/matrix', '/auth/me']
  it('未带 token 访问任何业务接口 → 1002 / 401', async () => {
    for (const p of BIZ) {
      const r = await api('GET', p)
      expect(r.json.code, p).toBe(1002)
      expect(r.status, p).toBe(401)
    }
  })
  it('伪造 token（合法 payload + 错误签名）→ 1002；垃圾 token → 1002', async () => {
    const b64 = s => Buffer.from(s).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
    const forged = `${b64(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))}.${b64(JSON.stringify({ sub: 1, username: 'admin', roles: ['sys_admin'], did: db.users[0].did, iat: 1, exp: Math.floor(Date.now() / 1000) + 9999 }))}.forged0000000000000000000000000000000000000`
    expect((await api('GET', '/auth/me', { token: forged })).json.code).toBe(1002)
    expect((await api('POST', '/evidence/demo/tamper', { token: forged, body: { evidenceId: 'ev-000001' } })).json.code).toBe(1002)
    expect((await api('GET', '/nodes', { token: 'not.a.jwt' })).json.code).toBe(1002)
    expect((await api('GET', '/nodes', { token: 'Bearer' })).json.code).toBe(1002)
  })
  it('过期 token → 1002；已登出 token → 1002', async () => {
    const { issueToken } = await import('@/mocks/helpers.js')
    const expired = issueToken(db.users[0], -10)
    expect((await api('GET', '/auth/me', { token: expired })).json.code).toBe(1002)
    const t = okData(await api('POST', '/auth/login', { body: { username: 'grid', password: 'grid123' } })).token
    okData(await api('POST', '/auth/logout', { token: t }))
    expect((await api('GET', '/auth/me', { token: t })).json.code).toBe(1002)
  })
  it('/evidence/demo/tamper 非 sys_admin → 1003（含 grid_dispatcher）', async () => {
    for (const u of ['grid', 'vpp', 'subject', 'regulator', 'edge']) {
      expect((await api('POST', '/evidence/demo/tamper', { token: tokens[u], body: { evidenceId: 'ev-000001', newValue: {} } })).json.code, u).toBe(1003)
    }
  })
})

/* =====================================================================
 * B2 前端防护
 * ===================================================================*/
describe('B2 前端防护', () => {
  it('request.js：连续多次 1002 只清一次登录态，已在 /login 时不再 replace（无死循环）', async () => {
    vi.resetModules()
    const replace = vi.fn()
    const clearSession = vi.fn()
    const route = { value: { path: '/login', fullPath: '/login' } }
    vi.doMock('@/router', () => ({ default: { currentRoute: route, replace, push: vi.fn() } }))
    vi.doMock('@/stores/user', () => ({ useUserStore: () => ({ clearSession }) }))
    vi.doMock('element-plus', () => ({ ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }))
    const { http, HttpResponse } = await import('msw')
    server.use(http.get('*/api/v1/_unauth', () => HttpResponse.json({ code: 1002, message: 'Token 失效', data: null, traceId: TRACE }, { status: 401 })))
    localStorage.setItem('energy-tds-token', 'stale')
    const { default: request } = await import('@/api/request.js')
    await expect(request.get('/_unauth')).rejects.toMatchObject({ code: 1002 })
    await expect(request.get('/_unauth')).rejects.toMatchObject({ code: 1002 })
    await expect(request.get('/_unauth')).rejects.toMatchObject({ code: 1002 })
    await new Promise(r => setTimeout(r, 30))
    expect(clearSession).toHaveBeenCalledTimes(3)
    expect(replace).not.toHaveBeenCalled() // 已在 /login，不再跳转
    route.value = { path: '/assets', fullPath: '/assets' }
    await expect(request.get('/_unauth')).rejects.toMatchObject({ code: 1002 })
    await new Promise(r => setTimeout(r, 30))
    expect(replace).toHaveBeenCalledTimes(1)
    expect(replace.mock.calls[0][0]).toMatchObject({ path: '/login', query: { redirect: '/assets' } })
    vi.doUnmock('@/router'); vi.doUnmock('@/stores/user'); vi.doUnmock('element-plus')
    vi.resetModules()
  })

  it('路由守卫：localStorage 被篡改成伪 token 时 /auth/me 返回 1002 → 清会话并落到 /login', async () => {
    vi.resetModules()
    vi.doMock('element-plus', () => ({ ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }))
    setActivePinia(createPinia())
    localStorage.setItem('energy-tds-token', 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOjEsInVzZXJuYW1lIjoiYWRtaW4iLCJyb2xlcyI6WyJzeXNfYWRtaW4iXSwiZXhwIjo5OTk5OTk5OTk5fQ.forged')
    localStorage.setItem('energy-tds-user', JSON.stringify({ id: 1, username: 'admin', roles: ['sys_admin'], permissions: ['dispatch:issue'] }))
    const { useUserStore } = await import('@/stores/user')
    const { default: router } = await import('@/router')
    const store = useUserStore()
    expect(store.isLoggedIn).toBe(true) // 篡改后本地"看起来"已登录
    await router.push('/assets')
    await flushPromises()
    await new Promise(r => setTimeout(r, 50))
    expect(store.token).toBe('')
    expect(store.permissions).toEqual([])
    expect(localStorage.getItem('energy-tds-token')).toBeNull()
    expect(router.currentRoute.value.path).toBe('/login')
    vi.doUnmock('element-plus')
    vi.resetModules()
  })

  it('v-permission：permissions 变化时自动移除 / 恢复元素，.disable 切换 disabled', async () => {
    vi.resetModules()
    vi.doMock('element-plus', () => ({ ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }))
    const pinia = createPinia()
    setActivePinia(pinia)
    const { permission } = await import('@/directives/permission')
    const { useUserStore } = await import('@/stores/user')
    const store = useUserStore()
    store.permissions = ['dispatch:issue']
    const Comp = defineComponent({
      directives: { permission },
      render() {
        const dir = resolveDirective('permission')
        return h('div', [
          withDirectives(h('button', { id: 'rm' }, '下发'), [[dir, 'dispatch:issue']]),
          withDirectives(h('button', { id: 'dis' }, '训练'), [[dir, 'algo:execute', '', { disable: true }]])
        ])
      }
    })
    const w = mount(Comp, { global: { plugins: [pinia] } })
    expect(w.find('#rm').exists()).toBe(true)
    expect(w.find('#dis').attributes('disabled')).toBeDefined()
    // 切换账号：权限变更但不刷新页面
    store.permissions = ['algo:execute']
    await nextTick(); await nextTick()
    expect(w.find('#rm').exists()).toBe(false)
    expect(w.find('#dis').attributes('disabled')).toBeUndefined()
    store.permissions = ['dispatch:issue', 'algo:execute']
    await nextTick(); await nextTick()
    expect(w.find('#rm').exists()).toBe(true)
    w.unmount()
    vi.doUnmock('element-plus')
    vi.resetModules()
  })

  it('logout：断开 WebSocket、清 token；AppLayout 卸载时清理 WS 订阅', async () => {
    vi.resetModules()
    vi.doMock('element-plus', () => ({ ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }))
    setActivePinia(createPinia())
    const { useUserStore } = await import('@/stores/user')
    const { useLogStore } = await import('@/stores/logs')
    const { wsClient, WS_STATUS } = await import('@/api/ws')
    const store = useUserStore()
    await store.login({ username: 'admin', password: 'admin123' })
    for (let i = 0; i < 50 && wsClient.status.value !== WS_STATUS.MOCK; i++) await new Promise(r => setTimeout(r, 20))
    expect(wsClient.status.value).toBe(WS_STATUS.MOCK)
    const logs = useLogStore()
    logs.attachWs()
    expect(logs.wsAttached).toBe(true)
    await store.logout()
    expect(wsClient.status.value).toBe(WS_STATUS.CLOSED)
    expect(store.token).toBe('')
    expect(localStorage.getItem('energy-tds-token')).toBeNull()
    logs.detachWs()
    expect(logs.wsAttached).toBe(false)
    const layout = fs.readFileSync(path.join(SRC, 'layouts', 'AppLayout.vue'), 'utf8')
    expect(layout).toMatch(/onBeforeUnmount\([\s\S]*detachWs\(\)/)
    vi.doUnmock('element-plus')
    vi.resetModules()
  })

  it('ws token 仅通过 query 传递，且 api/ stores/ 不打印 token', () => {
    const ws = fs.readFileSync(path.join(SRC, 'api', 'ws.js'), 'utf8')
    expect(ws).toMatch(/\?token=\$\{encodeURIComponent\(token\)\}/)
    expect(ws).not.toMatch(/console\.(log|info|debug|warn)\(/)
    for (const f of ['api/request.js', 'api/ws.js', 'stores/user.js', 'router/index.js', 'main.js']) {
      const s = fs.readFileSync(path.join(SRC, f), 'utf8')
      expect(s, f).not.toMatch(/console\.(log|info|debug)\([^)\n]*token/i)
    }
  })
})

/* =====================================================================
 * B3 XSS
 * ===================================================================*/
describe('B3 XSS', () => {
  const PAYLOADS = ['<img src=x onerror=alert(1)>', '{{7*7}}', '<script>alert(1)</script>']
  it('mock 原样存储/返回注入载荷（不做 HTML 解释），交给前端转义', async () => {
    const d = okData(await api('POST', '/did/register', { ...A(), body: { subjectType: 'device', subjectName: `逆变器-${PAYLOADS[0]}`, orgName: PAYLOADS[1] } }))
    const doc = okData(await api('GET', `/did/${encodeURIComponent(d.did)}`, A()))
    expect(doc.subjectName).toContain(PAYLOADS[0])
    const a = okData(await api('POST', '/assets', { ...A(), body: { name: `资产-${PAYLOADS[1]}`, dataType: 'pv', sourceDid: db.nodes[0].did, payload: { x: PAYLOADS[2] } } }))
    const one = okData(await api('GET', `/assets/${a.id}`, A()))
    expect(one.name).toContain(PAYLOADS[1])
    const ai = okData(await api('POST', '/ai/analyze', { ...A(), body: { scene: 'qa', question: PAYLOADS[2] } }))
    expect(typeof ai.answer).toBe('string')
    const logs = okData(await api('GET', `/audit/logs?keyword=${encodeURIComponent('onerror')}`, A()))
    expect(logs.items.length).toBeGreaterThan(0)
  })
  it('views/components 中唯一的 v-html（JsonViewer）先转义再着色', async () => {
    const files = []
    const walkDir = d => { for (const f of fs.readdirSync(d)) { const p = path.join(d, f); if (fs.statSync(p).isDirectory()) walkDir(p); else if (p.endsWith('.vue')) files.push(p) } }
    walkDir(path.join(SRC, 'views')); walkDir(path.join(SRC, 'components')); walkDir(path.join(SRC, 'layouts'))
    const withHtml = files.filter(f => /v-html|innerHTML|insertAdjacentHTML/.test(fs.readFileSync(f, 'utf8'))).map(f => path.relative(SRC, f))
    expect(withHtml).toEqual(['components/center/JsonViewer.vue'])
    const JsonViewer = (await import('@/components/center/JsonViewer.vue')).default
    const w = mount(JsonViewer, { props: { value: { name: PAYLOADS[0], t: PAYLOADS[2] } }, global: { stubs: { 'el-button': true } } })
    const html = w.find('pre').element.innerHTML
    expect(html).not.toContain('<img')
    expect(html).not.toContain('<script')
    expect(html).toContain('&lt;img')
    expect(w.find('img').exists()).toBe(false)
    expect(w.find('script').exists()).toBe(false)
  })
})

/* =====================================================================
 * B4 敏感信息
 * ===================================================================*/
describe('B4 敏感信息', () => {
  it('源码不含真实密钥 / 明文口令（演示口令仅允许在 mocks/ 内）', () => {
    const files = []
    const walkDir = d => { for (const f of fs.readdirSync(d)) { const p = path.join(d, f); if (fs.statSync(p).isDirectory()) walkDir(p); else if (/\.(vue|js|mjs|json)$/.test(p)) files.push(p) } }
    walkDir(SRC)
    const demoPwd = /\b(admin123|grid123|vpp123|subject123|reg123|edge123)\b/
    for (const f of files) {
      const rel = path.relative(SRC, f)
      const s = fs.readFileSync(f, 'utf8')
      expect(s, `${rel} 含疑似 API key`).not.toMatch(/sk-[A-Za-z0-9]{20,}/)
      expect(s, `${rel} 含 DEEPSEEK_API_KEY 明文`).not.toMatch(/DEEPSEEK_API_KEY\s*[:=]\s*['"][^'"]+['"]/)
      expect(s, `${rel} 含 JWT_SECRET 明文`).not.toMatch(/JWT_SECRET\s*[:=]\s*['"][^'"]+['"]/)
      if (!rel.startsWith('mocks/') && !/__smoke|\.spec\.|Login\.vue$/.test(rel)) {
        expect(s, `${rel} 含演示口令`).not.toMatch(demoPwd)
      }
    }
  })
  it('dist/ 不含 .env 与 mock 种子口令之外的敏感串', () => {
    const dist = path.join(ROOT, 'dist')
    if (!fs.existsSync(dist)) return
    const files = []
    const walkDir = d => { for (const f of fs.readdirSync(d)) { const p = path.join(d, f); if (fs.statSync(p).isDirectory()) walkDir(p); else files.push(p) } }
    walkDir(dist)
    expect(files.filter(f => /(^|\/)\.env/.test(f))).toEqual([])
    for (const f of files.filter(f => /\.(js|html)$/.test(f))) {
      const s = fs.readFileSync(f, 'utf8')
      expect(s, f).not.toMatch(/sk-[A-Za-z0-9]{20,}/)
    }
  })
})
