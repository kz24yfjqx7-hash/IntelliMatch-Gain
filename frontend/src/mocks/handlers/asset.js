/** 能源数据资产（契约 §2.4） */
import { http } from 'msw'
import { db, chain, nextId } from '../db.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, requirePerm, permissionsOf, writeAudit, writeEvidence, MockError, now } from '../helpers.js'

const TYPE_NAMES = { pv: '光伏出力', wind: '风电出力', storage: '储能状态', load: '负荷曲线', dispatch: '调度指令' }

/** 规则 + 简化聚类的分类分级（与 algo-service /classify 的返回结构一致） */
export function classifyRecord(rec, index) {
  const fields = Array.isArray(rec.fields) ? rec.fields : []
  const sensitiveFields = ['gps', 'location', 'owner', 'id', 'price', 'contract', 'user', 'phone', 'address']
  const sens = Math.min(1, 0.2 + fields.filter(f => sensitiveFields.some(s => String(f).toLowerCase().includes(s))).length * 0.3 + (rec.dataType === 'dispatch' ? 0.3 : rec.dataType === 'load' ? 0.15 : 0))
  const gran = { second: 1, minute: 0.7, '15min': 0.5, hour: 0.4, day: 0.2 }[rec.freq] ?? 0.5
  const vol = Math.min(1, Math.log10(Math.max(1, Number(rec.volume) || 1)) / 5)
  const score = Number((0.5 * sens + 0.3 * gran + 0.2 * vol).toFixed(3))
  // 阈值与 algo-service 一致：L1 <0.30 ≤ L2 <0.50 ≤ L3 <0.70 ≤ L4
  const level = score >= 0.70 ? 'L4' : score >= 0.50 ? 'L3' : score >= 0.30 ? 'L2' : 'L1'
  const cluster = score >= 0.6 ? 2 : score >= 0.35 ? 1 : 0
  const reasons = []
  if (sens >= 0.5) reasons.push('包含敏感字段')
  if (gran >= 0.7) reasons.push(`采集粒度为${rec.freq === 'second' ? '秒' : '分钟'}级`)
  if (vol >= 0.6) reasons.push('数据量大')
  if (!reasons.length) reasons.push('公开统计类数据')
  return { index, level, score, reason: reasons.join('且'), cluster, factors: { sensitivity: Number(sens.toFixed(2)), granularity: gran, volume: Number(vol.toFixed(2)) } }
}

export function assetDto(a) {
  return { id: a.id, name: a.name, dataType: a.dataType, level: a.level, sourceDid: a.sourceDid, ownerDid: a.ownerDid, hash: a.hash, chainTxId: a.chainTxId, evidenceId: a.evidenceId, authStatus: a.authStatus, description: a.description, traceId: a.traceId, createdAt: a.createdAt, updatedAt: a.updatedAt }
}

/** energy_subject / edge_node 仅可见自有资产（sourceDid 属于本人或本人控制的 DID） */
function visibleAssets(user) {
  const role = db.roles.find(r => user.roles.includes(r.code) && r.scope === 'own')
  const onlyOwnRoles = user.roles.every(code => db.roles.find(r => r.code === code)?.scope === 'own')
  if (!role || !onlyOwnRoles) return db.assets
  const myDids = new Set([user.did, ...db.dids.filter(d => d.controllerDid === user.did).map(d => d.did)])
  const granted = new Set(db.grants.filter(g => g.did === user.did && g.status === 'active' && g.resourceType === 'asset').map(g => g.resourceId))
  return db.assets.filter(a => myDids.has(a.sourceDid) || granted.has(String(a.id)) || granted.has('*'))
}

export const assetHandlers = [
  http.get(`${BASE}/assets/stats`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'asset:read', { request, traceId })
    const list = db.assets
    const byLevel = ['L1', 'L2', 'L3', 'L4'].map(level => ({ level, count: list.filter(a => a.level === level).length }))
    const byType = Object.keys(TYPE_NAMES).map(dataType => ({ dataType, count: list.filter(a => a.dataType === dataType).length }))
    return ok({ byLevel, byType, total: list.length, authorized: list.filter(a => a.authStatus === 'authorized').length, onChain: list.filter(a => a.evidenceId).length }, traceId)
  })),

  http.post(`${BASE}/assets/classify`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const { records = [] } = await body(request)
    if (!Array.isArray(records) || !records.length) throw new MockError(1001, 'records 不能为空')
    await new Promise(r => setTimeout(r, 200))
    const results = records.map(classifyRecord)
    writeAudit({ traceId, user, module: 'asset', action: 'asset:classify', resourceType: 'asset', detail: `自动分类分级 ${records.length} 条` })
    return ok({ results, clusterCenters: [[0.2, 0.3, 0.3], [0.5, 0.55, 0.5], [0.8, 0.7, 0.6]], algorithm: 'kmeans(k=3)+rule' }, traceId)
  })),

  http.post(`${BASE}/assets`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'asset:write', { request, traceId, resourceType: 'asset' })
    const data = await body(request)
    if (!data.name || !data.dataType) throw new MockError(1001, 'name/dataType 必填')
    if (!TYPE_NAMES[data.dataType]) throw new MockError(1001, 'dataType 非法')
    const sourceDid = data.sourceDid || user.did
    const src = db.dids.find(d => d.did === sourceDid)
    if (!src) throw new MockError(1004, '数据源 DID 不存在')
    if (src.status !== 'active') throw new MockError(1004, `数据源 DID 状态为 ${src.status}`)
    // 自动分级（未指定 level 时）
    let level = data.level
    if (!['L1', 'L2', 'L3', 'L4'].includes(level)) {
      level = classifyRecord({ dataType: data.dataType, fields: Object.keys(data.payload || {}), freq: data.freq || 'minute', volume: data.volume || 1440 }, 0).level
    }
    const id = nextId('asset')
    const payload = data.payload || {}
    const ev = writeEvidence({ category: 'data', refId: id, actorDid: user.did, traceId, payload })
    const asset = {
      id, name: data.name, dataType: data.dataType, level, sourceDid, ownerDid: user.did, hash: ev.payload_hash, chainTxId: ev.tx_id, evidenceId: ev.evidence_id,
      authStatus: 'unauthorized', description: data.description || '', payload, traceId, createdAt: ev.created_at, updatedAt: ev.created_at
    }
    db.assets.push(asset)
    writeAudit({ traceId, user, module: 'asset', action: 'asset:register', resourceType: 'asset', resourceId: id, detail: `登记资产 ${asset.name}（${level}）并上链`, evidenceId: ev.evidence_id })
    return ok({ id, hash: asset.hash, level, chainTxId: asset.chainTxId, evidenceId: asset.evidenceId, authStatus: asset.authStatus, createdAt: asset.createdAt, traceId }, traceId)
  })),

  http.get(`${BASE}/assets`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'asset:read', { request, traceId })
    const q = query(request)
    let list = visibleAssets(user).slice().reverse()
    if (q.dataType) list = list.filter(a => a.dataType === q.dataType)
    if (q.level) list = list.filter(a => a.level === q.level)
    if (q.sourceDid) list = list.filter(a => a.sourceDid === q.sourceDid)
    if (q.authStatus) list = list.filter(a => a.authStatus === q.authStatus)
    if (q.keyword) list = list.filter(a => a.name.includes(q.keyword) || String(a.id).includes(q.keyword) || a.hash.includes(q.keyword))
    const page = paginate(list, q)
    return ok({ ...page, items: page.items.map(assetDto) }, traceId)
  })),

  http.get(`${BASE}/assets/:id`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'asset:read', { request, traceId })
    const asset = visibleAssets(user).find(a => a.id === Number(params.id))
    if (!asset) throw new MockError(1005, '资产不存在或无权查看')
    writeAudit({ traceId, user, module: 'asset', action: 'asset:read', resourceType: 'asset', resourceId: asset.id, detail: `查看资产 ${asset.name}` })
    return ok({ ...assetDto(asset), payload: asset.payload, permissions: permissionsOf(user).filter(p => p.startsWith('asset:')) }, traceId)
  })),

  http.get(`${BASE}/assets/:id/lineage`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'asset:read', { request, traceId })
    const asset = db.assets.find(a => a.id === Number(params.id))
    if (!asset) throw new MockError(1005, '资产不存在')
    const rid = String(asset.id)
    const lineage = []
    const reg = chain.get(asset.evidenceId)
    lineage.push({ stage: 'register', at: asset.createdAt, actorDid: asset.sourceDid, evidenceId: asset.evidenceId, hash: reg?.payload_hash || asset.hash, detail: '数据登记上链' })
    for (const g of db.grants.filter(g => g.resourceType === 'asset' && (g.resourceId === rid))) {
      lineage.push({ stage: 'authorize', at: g.grantedAt, actorDid: g.grantedBy, evidenceId: g.evidenceId, detail: `授权 ${g.did.slice(0, 26)}… ${g.action}` })
    }
    for (const l of db.auditLogs.filter(l => l.resourceType === 'asset' && l.resourceId === rid && l.action === 'asset:read')) {
      lineage.push({ stage: 'access', at: l.at, actorDid: l.actorDid, evidenceId: l.evidenceId, detail: '数据访问' })
    }
    // 仅列出登记之后发生的联邦计算（避免溯源时间倒置）
    for (const t of db.flTasks.filter(t => t.status === 'success' && (t.finishedAt || t.updatedAt) > asset.createdAt).slice(-1)) {
      if (asset.dataType === 'load' || asset.dataType === 'pv') lineage.push({ stage: 'compute', at: t.finishedAt || t.updatedAt, actorDid: t.createdBy, evidenceId: t.rounds?.[t.rounds.length - 1]?.evidenceId || null, detail: `参与联邦计算 ${t.id}` })
    }
    lineage.sort((a, b) => (a.at < b.at ? -1 : 1))
    return ok({ assetId: asset.id, traceId: asset.traceId, chain: lineage }, traceId)
  }))
]
