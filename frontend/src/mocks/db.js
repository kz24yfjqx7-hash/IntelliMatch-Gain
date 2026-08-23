/**
 * mock 内存数据库 + 种子数据（照 contract/DB-SCHEMA.md）。
 * 仅在 mock 模式下使用；浏览器与 Node 通用（无浏览器专有 API）。
 */
import { LocalHashChain } from './chain.js'
import { sha256Hex } from '../utils/sha256.js'
import { toIso8 } from '../utils/format.js'
import { wsMock } from './wsMock.js'

/* ---------- 确定性随机（保证每次刷新种子一致） ---------- */
let seed = 20260821
function rand() {
  seed |= 0; seed = (seed + 0x6D2B79F5) | 0
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296
}
const pick = arr => arr[Math.floor(rand() * arr.length)]
const hex = n => { let s = ''; for (let i = 0; i < n; i++) s += '0123456789abcdef'[Math.floor(rand() * 16)]; return s }
const pad = (n, l = 6) => String(n).padStart(l, '0')

const NOW = Date.now()
const DAY = 86400000
const ago = (days, hours = 0, mins = 0) => toIso8(new Date(NOW - days * DAY - hours * 3600000 - mins * 60000))
/** 当天 YYYYMMDD，用于种子 traceId */
function traceAt(days) {
  const d = new Date(NOW - days * DAY + 8 * 3600000)
  return `tr-${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1, 2)}${pad(d.getUTCDate(), 2)}-${hex(8)}`
}

/* ---------- DID / 密钥工具 ---------- */
export function genKeyPair() {
  const privateKey = hex(64)
  const publicKey = '04' + sha256Hex('pub:' + privateKey) + sha256Hex('pub2:' + privateKey)
  return { publicKey, privateKey }
}
export function didFromPublicKey(subjectType, publicKey) {
  return `did:vpp:${subjectType}:0x${sha256Hex(publicKey).slice(0, 32)}`
}
export function buildDidDocument(did, controller, publicKey, created) {
  return {
    '@context': 'https://w3id.org/did/v1',
    id: did,
    controller,
    verificationMethod: [{ id: `${did}#key-1`, type: 'SM2VerificationKey2023', controller: did, publicKeyHex: publicKey }],
    authentication: [`${did}#key-1`],
    created,
    updated: created
  }
}

/* ---------- 角色权限矩阵（DB-SCHEMA） ---------- */
export const RESOURCES = ['asset', 'model', 'dispatch', 'evidence', 'algo']
export const ACTIONS = ['read', 'write', 'execute', 'issue', 'export']

const roles = [
  { code: 'sys_admin', name: '系统管理员', builtin: true, grants: { asset: ['read', 'write', 'export'], model: ['read'], dispatch: ['read', 'issue'], evidence: ['read'], algo: ['execute'], user: ['manage'] } },
  { code: 'grid_dispatcher', name: '电网调度员', builtin: true, grants: { asset: ['read', 'export'], model: ['read'], dispatch: ['read', 'issue'], evidence: ['read'], algo: ['execute'] } },
  { code: 'vpp_operator', name: '虚拟电厂运营商', builtin: true, grants: { asset: ['read', 'write'], model: ['read'], dispatch: ['read'], evidence: ['read'] } },
  { code: 'energy_subject', name: '能源主体', builtin: true, scope: 'own', grants: { asset: ['read', 'write'], evidence: ['read'] } },
  { code: 'regulator', name: '监管方', builtin: true, grants: { asset: ['read', 'export'], model: ['read'], dispatch: ['read'], evidence: ['read'] } },
  { code: 'edge_node', name: '边缘节点', builtin: true, scope: 'own', grants: { asset: ['read', 'write'], model: ['read'], dispatch: ['read'] } }
]

/** 角色 → 扁平权限数组 */
export function permissionsOfRoles(roleCodes) {
  const set = new Set()
  for (const code of roleCodes) {
    const r = roles.find(x => x.code === code)
    if (!r) continue
    for (const [res, acts] of Object.entries(r.grants)) acts.forEach(a => set.add(`${res}:${a}`))
  }
  return Array.from(set)
}

/* ---------- 链与各表 ---------- */
export const chain = new LocalHashChain()

const orgDidPair = genKeyPair()
const ORG_DID = didFromPublicKey('org', orgDidPair.publicKey)

const dids = []
const keys = []
const keyRotations = []
let keyId = 0

/** 创建 DID + 密钥 + 身份存证（种子与运行时共用） */
export function createDid({ subjectType, subjectName, orgName = '平台运营方', metadata = {}, controller = ORG_DID, createdAt, traceId = null, actorDid = null }) {
  const kp = genKeyPair()
  const did = didFromPublicKey(subjectType, kp.publicKey)
  const created = createdAt || toIso8(new Date())
  const doc = buildDidDocument(did, controller, kp.publicKey, created)
  const rec = {
    id: dids.length + 1,
    did,
    subjectType,
    subjectName,
    orgName,
    controllerDid: controller,
    didDocument: doc,
    status: 'active',
    metadata,
    createdAt: created,
    updatedAt: created
  }
  dids.push(rec)
  const key = {
    id: ++keyId,
    did,
    algorithm: 'SM2',
    publicKey: kp.publicKey,
    status: 'active',
    version: 1,
    boundAt: created,
    expireAt: toIso8(new Date(new Date(created).getTime() + 365 * DAY))
  }
  keys.push(key)
  const ev = chain.append({
    category: 'identity',
    refId: did,
    actorDid: actorDid || controller,
    traceId,
    createdAt: created,
    payload: { op: 'did:register', did, subjectType, subjectName, publicKey: kp.publicKey, created }
  })
  return { rec, key, ev, privateKey: kp.privateKey }
}

// 组织 DID（控制者）
dids.push({
  id: 1, did: ORG_DID, subjectType: 'org', subjectName: '平台运营方', orgName: '平台运营方', controllerDid: ORG_DID,
  didDocument: buildDidDocument(ORG_DID, ORG_DID, orgDidPair.publicKey, ago(30)), status: 'active', metadata: { root: true }, createdAt: ago(30), updatedAt: ago(30)
})
keys.push({ id: ++keyId, did: ORG_DID, algorithm: 'SM2', publicKey: orgDidPair.publicKey, status: 'active', version: 1, boundAt: ago(30), expireAt: ago(-335) })
chain.append({ category: 'identity', refId: ORG_DID, actorDid: ORG_DID, traceId: traceAt(30), createdAt: ago(30), payload: { op: 'did:register', did: ORG_DID, subjectType: 'org', subjectName: '平台运营方' } })

/* ---------- 用户 ---------- */
const USER_SEED = [
  ['admin', 'admin123', 'sys_admin', '系统管理员', '平台运营方'],
  ['grid', 'grid123', 'grid_dispatcher', '电网调度员', '省电力调度中心'],
  ['vpp', 'vpp123', 'vpp_operator', '虚拟电厂运营商', 'XX虚拟电厂运营公司'],
  ['subject', 'subject123', 'energy_subject', '能源主体', 'XX工业园区'],
  ['regulator', 'reg123', 'regulator', '监管方', '能源监管局'],
  ['edge', 'edge123', 'edge_node', '边缘节点', 'XX园区边缘站']
]
const users = USER_SEED.map(([username, password, role, realName, orgName], i) => {
  const { rec } = createDid({ subjectType: 'user', subjectName: realName, orgName, createdAt: ago(29, i), traceId: traceAt(29), metadata: { username } })
  return { id: i + 1, username, password, realName, roles: [role], did: rec.did, orgName, status: 'active', createdAt: ago(29, i), updatedAt: ago(29, i) }
})

/* ---------- 节点 ---------- */
const NODE_SEED = [
  ['Node-A', '虚拟电厂节点A', 'online', 'VPP-2000', 45.3, -12.0, 120, 65],
  ['Node-B', '虚拟电厂节点B', 'online', 'VPP-2000', 32.1, 8.5, 85, 78],
  ['Node-C', '虚拟电厂节点C', 'warning', 'VPP-3000', 28.7, -25.3, 150, 42],
  ['Node-D', '虚拟电厂节点D', 'online', 'VPP-2000', 38.9, 5.2, 95, 82]
]
const nodes = NODE_SEED.map(([id, name, status, model, pvOutput, storageOutput, load, soc], i) => {
  const { rec } = createDid({ subjectType: 'edge', subjectName: name, orgName: 'XX园区边缘站', createdAt: ago(28, i), traceId: traceAt(28), metadata: { nodeId: id, model } })
  return { id, name, status, model, did: rec.did, didStatus: 'active', metrics: { pvOutput, storageOutput, load, soc }, lastSeenAt: toIso8(new Date()), location: `园区${'ABCD'[i]}区` }
})

/* ---------- 设备 DID ---------- */
const DEVICE_SEED = [
  ['光伏逆变器-A01', 'VPP-2000', 'A区'], ['储能PCS-A02', 'PCS-500', 'A区'], ['风机控制器-B01', 'WT-3MW', 'B区'],
  ['智能电表-C01', 'DTSD-341', 'C区'], ['储能BMS-C02', 'BMS-1000', 'C区'], ['光伏逆变器-D01', 'VPP-2000', 'D区']
]
const deviceDids = DEVICE_SEED.map(([name, model, location], i) => createDid({ subjectType: 'device', subjectName: name, orgName: 'XX园区', createdAt: ago(27 - i, 2), traceId: traceAt(27 - i), metadata: { model, location } }).rec)
// 一个冻结、一个注销的示例
deviceDids[4].status = 'frozen'; deviceDids[4].updatedAt = ago(3)
keys.find(k => k.did === deviceDids[4].did).status = 'frozen'
chain.append({ category: 'identity', refId: deviceDids[4].did, actorDid: users[0].did, traceId: traceAt(3), createdAt: ago(3), payload: { op: 'did:freeze', did: deviceDids[4].did, reason: '设备离线超 24 小时' } })
// 一次密钥轮换示例
{
  const d = deviceDids[0]
  const old = keys.find(k => k.did === d.did)
  old.status = 'revoked'
  const kp = genKeyPair()
  keys.push({ id: ++keyId, did: d.did, algorithm: 'SM2', publicKey: kp.publicKey, status: 'active', version: 2, boundAt: ago(10), expireAt: ago(-355) })
  keyRotations.push({ id: 1, keyId: old.id, did: d.did, fromVersion: 1, toVersion: 2, reason: '定期轮换', operator: users[0].did, at: ago(10) })
  d.didDocument.verificationMethod[0].publicKeyHex = kp.publicKey
  d.didDocument.updated = ago(10)
  chain.append({ category: 'identity', refId: d.did, actorDid: users[0].did, traceId: traceAt(10), createdAt: ago(10), payload: { op: 'did:rotate-key', did: d.did, version: 2, publicKey: kp.publicKey } })
}

/* ---------- 资产（80 条，5 类型 4 等级） ---------- */
const DATA_TYPES = ['pv', 'wind', 'storage', 'load', 'dispatch']
const TYPE_NAMES = { pv: '光伏出力', wind: '风电出力', storage: '储能状态', load: '负荷曲线', dispatch: '调度指令' }
const LEVEL_W = ['L1', 'L2', 'L2', 'L3', 'L3', 'L4']
const assets = []
const assetSourceDids = [...nodes.map(n => n.did), ...deviceDids.map(d => d.did)]
for (let i = 0; i < 80; i++) {
  const dataType = DATA_TYPES[i % 5]
  const level = i < 20 ? ['L1', 'L2', 'L3', 'L4'][i % 4] : pick(LEVEL_W)
  const days = Math.floor(i / 3)
  const createdAt = ago(days, (i * 7) % 24, (i * 13) % 60)
  const sourceDid = assetSourceDids[i % assetSourceDids.length]
  const nodeIdx = i % 4
  const payload = {
    dataType,
    nodeId: nodes[nodeIdx].id,
    ts: createdAt,
    value: Number((20 + rand() * 120).toFixed(1)),
    unit: dataType === 'storage' ? '%' : 'kW',
    samples: 60 + Math.floor(rand() * 1380)
  }
  const id = 1001 + i
  const ev = chain.append({ category: 'data', refId: id, actorDid: sourceDid, traceId: traceAt(days), createdAt, payload })
  const dateTag = createdAt.slice(0, 10).replace(/-/g, '')
  assets.push({
    id,
    name: `${nodes[nodeIdx].name.replace('虚拟电厂', '')}${TYPE_NAMES[dataType]}-${dateTag}`,
    dataType,
    level,
    sourceDid,
    ownerDid: sourceDid,
    hash: ev.payload_hash,
    chainTxId: ev.tx_id,
    evidenceId: ev.evidence_id,
    authStatus: i % 3 === 0 ? 'authorized' : 'unauthorized',
    description: pick(['分钟级采集', '15 分钟聚合', '小时级统计', '日结算数据']),
    payload,
    traceId: ev.trace_id,
    createdAt,
    updatedAt: createdAt
  })
}

/* ---------- 节点 30 天历史指标（小时级） ---------- */
const nodeMetrics = {}
for (const n of nodes) {
  const list = []
  const base = n.metrics
  for (let h = 30 * 24; h >= 1; h--) {
    const t = new Date(NOW - h * 3600000)
    const hour = t.getHours()
    const solar = Math.max(0, Math.sin(((hour - 6) / 12) * Math.PI))
    list.push({
      ts: toIso8(t),
      pvOutput: Number((base.pvOutput * solar * (0.8 + rand() * 0.4)).toFixed(1)),
      storageOutput: Number((base.storageOutput + (rand() - 0.5) * 10).toFixed(1)),
      load: Number((base.load * (0.7 + 0.3 * Math.sin(((hour - 8) / 12) * Math.PI) + rand() * 0.15)).toFixed(1)),
      soc: Number(Math.min(95, Math.max(20, base.soc + (rand() - 0.5) * 20)).toFixed(1))
    })
  }
  nodeMetrics[n.id] = list
}

/* ---------- 权限申请 / 授权 ---------- */
const applications = []
const grants = []
const permChangeLogs = []
const APP_SEED = [
  [users[3], 'asset', '1001', 'read', '联合建模需要读取节点A光伏数据', 'approved', 12],
  [users[2], 'asset', '1004', 'read', '虚拟电厂负荷预测', 'approved', 11],
  [users[2], 'model', 'v11', 'read', '获取最新负荷预测模型', 'approved', 9],
  [users[3], 'asset', '1010', 'export', '导出园区储能数据用于内部报表', 'rejected', 8],
  [users[5], 'asset', '1003', 'read', '边缘节点本地校验', 'approved', 6],
  [users[2], 'dispatch', 'dp-000001', 'issue', '运营商希望直接下发调度', 'rejected', 5],
  [users[3], 'asset', '1016', 'read', '碳排放核算需要负荷曲线', 'pending', 1],
  [users[4], 'evidence', '*', 'export', '监管抽查导出存证', 'pending', 0]
]
APP_SEED.forEach(([u, resourceType, resourceId, action, reason, status, days], i) => {
  const createdAt = ago(days, 3)
  const ev = chain.append({ category: 'permission', refId: `app-${i + 1}`, actorDid: u.did, traceId: traceAt(days), createdAt, payload: { op: 'permission:apply', resourceType, resourceId, action, applicantDid: u.did } })
  const app = {
    id: i + 1, applicantDid: u.did, applicantName: u.realName, resourceType, resourceId, action, reason, status,
    expireAt: ago(-30), createdAt, updatedAt: createdAt, evidenceId: ev.evidence_id, traceId: ev.trace_id,
    reviewerDid: null, reviewComment: null, reviewedAt: null
  }
  if (status !== 'pending') {
    app.reviewerDid = users[0].did
    app.reviewedAt = ago(days, 1)
    app.reviewComment = status === 'approved' ? '符合最小必要原则，同意' : '超出业务范围，驳回'
    const ev2 = chain.append({ category: 'permission', refId: `app-${i + 1}`, actorDid: users[0].did, traceId: traceAt(days), createdAt: app.reviewedAt, payload: { op: `permission:${status === 'approved' ? 'approve' : 'reject'}`, applicationId: app.id, resourceType, resourceId, action } })
    permChangeLogs.push({ id: permChangeLogs.length + 1, applicationId: app.id, did: u.did, change: status, operator: users[0].did, at: app.reviewedAt, evidenceId: ev2.evidence_id })
    if (status === 'approved') {
      grants.push({
        id: grants.length + 1, applicationId: app.id, did: u.did, granteeName: u.realName, resourceType, resourceId, action,
        grantedBy: users[0].did, grantedAt: app.reviewedAt, expireAt: app.expireAt, status: 'active', evidenceId: ev2.evidence_id, traceId: ev.trace_id
      })
      const asset = assets.find(a => String(a.id) === resourceId)
      if (asset) asset.authStatus = 'authorized'
    }
  }
  applications.push(app)
})
// 一条已回收的授权
{
  const g = grants[1]
  g.status = 'revoked'; g.revokedAt = ago(2); g.revokedBy = users[0].did
  chain.append({ category: 'permission', refId: `grant-${g.id}`, actorDid: users[0].did, traceId: traceAt(2), createdAt: g.revokedAt, payload: { op: 'permission:revoke', grantId: g.id } })
}

/* ---------- FL 任务 / 模型 ---------- */
const flTasks = []
const flModels = []
function seedFlTask(idx, days, rounds, eps) {
  const id = `fl-${pad(idx)}`
  const traceId = traceAt(days)
  const createdAt = ago(days, 5)
  const rlist = []
  let loss = 0.52 + rand() * 0.1
  for (let r = 1; r <= rounds; r++) {
    loss = Math.max(0.05, loss * (0.82 + rand() * 0.08))
    const gradHash = 'sm3:' + sha256Hex(`${id}-${r}-${loss}`)
    const ev = chain.append({ category: 'algo', refId: id, actorDid: users[1].did, traceId, createdAt: ago(days, 5, -r), payload: { op: 'fl:round', taskId: id, round: r, loss: Number(loss.toFixed(4)), gradientHash: gradHash } })
    rlist.push({ round: r, loss: Number(loss.toFixed(4)), acc: Number(Math.min(0.97, 0.55 + (1 - loss) * 0.45).toFixed(3)), compressionRatio: 90, epsilonSpent: Number((eps * r / rounds).toFixed(3)), gradientHash: gradHash, evidenceId: ev.evidence_id, at: ev.created_at })
  }
  const version = `v${10 + idx}`
  flTasks.push({
    id, name: `负荷预测联合建模-第${idx}轮`, status: 'success', currentRound: rounds, totalRounds: rounds,
    nodeIds: nodes.map(n => n.id),
    nodes: nodes.map((n, i) => ({ nodeId: n.id, did: n.did, joined: true, samples: [480, 320, 410, 360][i] })),
    dp: { enabled: true, epsilon: eps, delta: 1e-5, epsilonSpent: eps },
    topk: { enabled: true, ratio: 0.1, compressionRatio: 90 },
    rounds: rlist, modelVersion: version, anomaly: null, traceId, createdBy: users[1].did,
    createdAt, startedAt: ago(days, 5), finishedAt: ago(days, 5, -rounds), updatedAt: ago(days, 5, -rounds)
  })
  flModels.push({ version, taskId: id, loss: rlist[rlist.length - 1].loss, acc: rlist[rlist.length - 1].acc, rounds, status: idx === 1 ? 'published' : 'draft', createdAt: ago(days, 5, -rounds), publishedAt: idx === 1 ? ago(days, 4) : null })
}
seedFlTask(1, 14, 10, 1.0)
seedFlTask(2, 6, 8, 0.8)

/* ---------- 调度任务 ---------- */
const dispatchTasks = []
function seedDispatch(idx, days) {
  const id = `dp-${pad(idx)}`
  const traceId = traceAt(days)
  const createdAt = ago(days, 2)
  const ev = chain.append({ category: 'algo', refId: id, actorDid: users[1].did, traceId, createdAt, payload: { op: 'dispatch:run', taskId: id, target: 'Node-C', action: 'discharge', powerKw: 24 } })
  const ev2 = chain.append({ category: 'audit', refId: id, actorDid: users[1].did, traceId, createdAt: ago(days, 2, -1), payload: { op: 'dispatch:issue', taskId: id, signerDid: users[1].did, commandId: `cmd-${pad(idx)}` } })
  dispatchTasks.push({
    id, name: `峰时储能调度-${idx}`, status: 'success', nodeIds: nodes.map(n => n.id), timeWindow: `${createdAt.slice(0, 10)}T15:00~16:00+08:00`,
    strategy: {
      actions: [
        { nodeId: 'Node-C', action: 'discharge', powerKw: 24.0, qValue: 8.42, reason: '负荷最高且SOC充足' },
        { nodeId: 'Node-B', action: 'charge', powerKw: 12.0, qValue: 6.1, reason: '光伏富余且SOC偏低' },
        { nodeId: 'Node-A', action: 'idle', powerKw: 0, qValue: 5.0, reason: '功率平衡' },
        { nodeId: 'Node-D', action: 'idle', powerKw: 0, qValue: 4.8, reason: '功率平衡' }
      ],
      totalReward: 15.7,
      timeWindow: `${createdAt.slice(0, 10)}T15:00~16:00+08:00`
    },
    qTable: [
      { nodeId: 'Node-A', charge: 3.2, idle: 5.0, discharge: 4.1 }, { nodeId: 'Node-B', charge: 6.1, idle: 4.2, discharge: 2.9 },
      { nodeId: 'Node-C', charge: 3.1, idle: 5.2, discharge: 8.42 }, { nodeId: 'Node-D', charge: 4.0, idle: 4.8, discharge: 4.3 }
    ],
    explanation: '节点C当前负荷150kW为全网最高，SOC 42% 仍高于安全下限，优先放电 24kW 削峰；节点B光伏富余，安排充电 12kW 消纳。',
    explanationSource: 'cache',
    evidenceId: ev.evidence_id, issueEvidenceId: ev2.evidence_id, commandId: `cmd-${pad(idx)}`, signerDid: users[1].did, targets: ['Node-C', 'Node-B'],
    ack: { nodeId: 'Node-C', status: 'success', actualPowerKw: 23.6, at: ago(days, 2, -5) },
    traceId, createdBy: users[1].did, createdAt, issuedAt: ago(days, 2, -1), ackedAt: ago(days, 2, -5), updatedAt: ago(days, 2, -5)
  })
}
seedDispatch(1, 9)
seedDispatch(2, 3)

/* ---------- 审计日志 / 告警 ---------- */
const auditLogs = []
let auditId = 9000
function seedAudit({ traceId, user, module, action, resourceType = null, resourceId = null, result = 'success', riskLevel = 'low', detail = '', at, evidenceId = null }) {
  const rec = {
    id: ++auditId, traceId, actorDid: user?.did || null, actorName: user?.realName || '系统', module, action, resourceType, resourceId,
    result, riskLevel, detail, ip: `192.168.1.${40 + Math.floor(rand() * 60)}`, at, evidenceId,
    hash: 'sm3:' + sha256Hex(`${traceId}|${action}|${at}|${detail}`)
  }
  auditLogs.push(rec)
  return rec
}
// 一条完整链路（供 /audit/trace 首次打开有内容）
const DEMO_TRACE = traceAt(1)
seedAudit({ traceId: DEMO_TRACE, user: users[1], module: 'auth', action: 'login', result: 'success', at: ago(1, 4, 10), detail: '电网调度员登录' })
seedAudit({ traceId: DEMO_TRACE, user: users[1], module: 'did', action: 'did:verify', resourceType: 'did', resourceId: users[1].did, at: ago(1, 4, 9), detail: 'SM2 验签通过' })
seedAudit({ traceId: DEMO_TRACE, user: users[1], module: 'permission', action: 'permission:check', resourceType: 'algo', resourceId: 'fl', result: 'success', at: ago(1, 4, 8), detail: 'algo:execute 校验通过' })
seedAudit({ traceId: DEMO_TRACE, user: users[1], module: 'algo', action: 'fl:train', resourceType: 'algo', resourceId: 'fl-000002', at: ago(1, 4, 7), detail: '启动联邦训练 8 轮' })
seedAudit({ traceId: DEMO_TRACE, user: users[1], module: 'evidence', action: 'evidence:write', resourceType: 'evidence', resourceId: flTasks[1].rounds[0].evidenceId, at: ago(1, 4, 6), detail: '梯度哈希上链', evidenceId: flTasks[1].rounds[0].evidenceId })
// 其余随机日志
const ACTION_POOL = [
  ['auth', 'login', 'low'], ['did', 'did:register', 'low'], ['did', 'did:verify', 'low'], ['asset', 'asset:register', 'low'], ['asset', 'asset:read', 'low'],
  ['permission', 'permission:apply', 'low'], ['permission', 'permission:approve', 'medium'], ['evidence', 'evidence:verify', 'low'],
  ['algo', 'fl:train', 'low'], ['algo', 'dispatch:run', 'medium'], ['audit', 'audit:export', 'medium'], ['key', 'key:rotate', 'medium']
]
for (let i = 0; i < 70; i++) {
  const days = Math.floor(rand() * 7)
  const [module, action, riskLevel] = pick(ACTION_POOL)
  const u = pick(users)
  seedAudit({ traceId: traceAt(days), user: u, module, action, resourceType: module === 'asset' ? 'asset' : null, resourceId: module === 'asset' ? String(1001 + Math.floor(rand() * 80)) : null, riskLevel, at: ago(days, Math.floor(rand() * 20), Math.floor(rand() * 60)), detail: `${u.realName} 执行 ${action}` })
}
// 高风险样例
const DENY_TRACE = traceAt(2)
seedAudit({ traceId: DENY_TRACE, user: users[2], module: 'permission', action: 'dispatch:issue', resourceType: 'dispatch', resourceId: 'dp-000001', result: 'denied', riskLevel: 'high', at: ago(2, 6), detail: '越权尝试下发调度指令' })
seedAudit({ traceId: traceAt(4), user: users[3], module: 'asset', action: 'asset:export', resourceType: 'asset', resourceId: '*', result: 'denied', riskLevel: 'high', at: ago(4, 2), detail: '无导出权限的批量导出尝试' })
seedAudit({ traceId: traceAt(3), user: null, module: 'did', action: 'did:verify', resourceType: 'did', resourceId: deviceDids[4].did, result: 'failed', riskLevel: 'high', at: ago(3, 1), detail: '已冻结 DID 尝试接入' })
auditLogs.sort((a, b) => (a.at < b.at ? -1 : 1))

const alerts = [
  { id: 1, ruleCode: 'R01_UNAUTHORIZED', riskLevel: 'high', message: 'vpp_operator 越权尝试 dispatch:issue', actorDid: users[2].did, status: 'acked', traceId: DENY_TRACE, createdAt: ago(2, 6), ackedAt: ago(2, 5), ackedBy: users[0].did },
  { id: 2, ruleCode: 'R02_ABNORMAL_DID', riskLevel: 'high', message: `已冻结 DID ${deviceDids[4].did.slice(0, 28)}... 尝试接入`, actorDid: deviceDids[4].did, status: 'open', traceId: traceAt(3), createdAt: ago(3, 1), ackedAt: null, ackedBy: null },
  { id: 3, ruleCode: 'R04_BULK_EXPORT', riskLevel: 'medium', message: 'energy_subject 10 分钟内尝试导出 3 次', actorDid: users[3].did, status: 'open', traceId: traceAt(4), createdAt: ago(4, 2), ackedAt: null, ackedBy: null }
]

/* ---------- AI 历史 / 风险历史 ---------- */
const aiHistory = [
  { id: 1, scene: 'dispatch', question: '为什么选择节点C放电？', answer: '节点C当前负荷150kW为全网最高，SOC 42% 高于安全下限 20%，放电 24kW 可削峰并保持安全边界。', source: 'cache', latencyMs: 312, context: { taskId: 'dp-000001' }, actorDid: users[1].did, traceId: traceAt(9), createdAt: ago(9, 1) },
  { id: 2, scene: 'risk', question: '节点A当前隐私风险如何？', answer: '节点A 5 分钟内 12 次查询，频率偏高，建议将差分隐私 ε 由 1.0 降至 0.5。', source: 'cache', latencyMs: 288, context: { nodeId: 'Node-A' }, actorDid: users[4].did, traceId: traceAt(5), createdAt: ago(5, 3) },
  { id: 3, scene: 'audit', question: '今日审计概况', answer: '今日共记录 68 条审计日志，其中高风险 2 条，均为越权访问类，已生成告警并确认。', source: 'cache', latencyMs: 401, context: { period: 'day' }, actorDid: users[0].did, traceId: traceAt(1), createdAt: ago(1, 2) }
]
const riskHistory = []
for (let d = 13; d >= 0; d--) {
  for (const n of nodes) {
    const score = Number(Math.min(95, Math.max(15, 40 + (n.metrics.load - 100) * 0.3 + (rand() - 0.5) * 30)).toFixed(1))
    riskHistory.push({ id: riskHistory.length + 1, nodeId: n.id, riskScore: score, level: score >= 80 ? 'critical' : score >= 60 ? 'high' : score >= 40 ? 'medium' : 'low', at: ago(d, 9), traceId: traceAt(d) })
  }
}

/* ---------- 会话 / 计数器 ---------- */
const sessions = new Map() // token -> { userId, issuedAt, expiresAt }
const revokedTokens = new Set() // 已登出的 token（在过期前一律拒绝）
const counters = {
  asset: 1080, application: applications.length, grant: grants.length, permChange: permChangeLogs.length, key: keyId, keyRotation: keyRotations.length,
  fl: 2, model: 12, dispatch: 2, command: 2, alert: alerts.length, audit: auditId, ai: aiHistory.length, risk: riskHistory.length, user: users.length, did: dids.length,
  notice: 0
}

/* ---------- 站内消息（铃铛）---------- */
// 离线演示模式下从空开始：申请/审批一走通就会有消息，比预置几条假消息更能说明链路
const notices = []

export const db = {
  users, roles, dids, keys, keyRotations, nodes, nodeMetrics, assets, applications, grants, permChangeLogs,
  auditLogs, alerts, notices, flTasks, flModels, dispatchTasks, aiHistory, riskHistory, sessions, revokedTokens, counters,
  ORG_DID, DEMO_TRACE, DENY_TRACE,
  /** 运行期 FL 作业句柄（taskId -> {cancel}） */
  flJobs: new Map(),
  /** 权限拒绝计数（R01 规则）：did -> [timestamps] */
  denyCounter: new Map()
}

// 把节点列表注入 WS 模拟通道，供 5s 一次的 node_status 推送
wsMock.setNodeProvider(() => nodes)

export function nextId(key) {
  // 密钥 id 与 createDid() 共用同一计数器，避免运行时签发 DID 后与 POST /keys 产生 id 冲突
  if (key === 'key') { counters.key = ++keyId; return keyId }
  counters[key] = (counters[key] || 0) + 1
  return counters[key]
}

export default db
