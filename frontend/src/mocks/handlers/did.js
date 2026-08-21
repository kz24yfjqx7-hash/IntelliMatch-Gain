/** DID 身份管理（契约 §2.2） */
import { http } from 'msw'
import { db, createDid, genKeyPair, nextId } from '../db.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, writeAudit, writeEvidence, raiseAlert, MockError, now, clientIp } from '../helpers.js'
import { toIso8 } from '../../utils/format.js'

const DAY = 86400000

export function didDto(d) {
  return {
    id: d.id, did: d.did, subjectType: d.subjectType, subjectName: d.subjectName, orgName: d.orgName, controllerDid: d.controllerDid,
    status: d.status, metadata: d.metadata, createdAt: d.createdAt, updatedAt: d.updatedAt,
    keyVersion: db.keys.filter(k => k.did === d.did).length
  }
}

/**
 * mock 验签规则（真实环境为 SM2）：
 *  - signature 为空 / 'invalid' / 'bad' 开头 / 长度 < 8 → 无效
 *  - DID 不存在 → 无效；DID 冻结/注销 → 无效并触发 R02 告警
 */
export function mockVerifySignature(did, message, signature) {
  const rec = db.dids.find(d => d.did === did)
  if (!rec) return { valid: false, reason: 'DID 不存在', rec: null }
  if (rec.status !== 'active') return { valid: false, reason: `DID 状态为 ${rec.status}，拒绝接入`, rec, abnormal: true }
  const sig = String(signature || '')
  if (!sig || sig.length < 8 || /^(invalid|bad)/i.test(sig)) return { valid: false, reason: '签名无效或与公钥不匹配', rec }
  return { valid: true, reason: null, rec }
}

export const didHandlers = [
  http.post(`${BASE}/did/register`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const data = await body(request)
    const subjectType = data.subjectType
    if (!['user', 'device', 'org', 'edge'].includes(subjectType)) throw new MockError(1001, 'subjectType 非法')
    if (!data.subjectName) throw new MockError(1001, 'subjectName 必填')
    if (db.dids.find(d => d.subjectType === subjectType && d.subjectName === data.subjectName && d.status === 'active')) {
      throw new MockError(1006, `主体 ${data.subjectName} 已注册 DID`)
    }
    const { rec, ev, privateKey } = createDid({ subjectType, subjectName: data.subjectName, orgName: data.orgName || user.orgName, metadata: data.metadata || {}, controller: user.did, traceId, actorDid: user.did })
    // 运行时创建的存证也推 WS
    const { wsMock } = await import('../wsMock.js')
    wsMock.emit('evidence_written', { evidenceId: ev.evidence_id, category: 'identity', blockHeight: ev.block_height }, traceId)
    writeAudit({ traceId, user, module: 'did', action: 'did:register', resourceType: 'did', resourceId: rec.did, detail: `签发 ${subjectType} DID：${data.subjectName}`, evidenceId: ev.evidence_id, ip: clientIp(request) })
    // 设备/边缘 DID 自动绑定到节点（metadata.nodeId）
    if (data.metadata?.nodeId) {
      const node = db.nodes.find(n => n.id === data.metadata.nodeId)
      if (node) { node.did = rec.did; node.didStatus = 'active' }
    }
    return ok({
      did: rec.did, didDocument: rec.didDocument, publicKey: rec.didDocument.verificationMethod[0].publicKeyHex, privateKey,
      chainTxId: ev.tx_id, evidenceId: ev.evidence_id, createdAt: rec.createdAt
    }, traceId)
  })),

  http.get(`${BASE}/did`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    const q = query(request)
    let list = db.dids.slice().reverse()
    if (q.subjectType) list = list.filter(d => d.subjectType === q.subjectType)
    if (q.status) list = list.filter(d => d.status === q.status)
    if (q.keyword) list = list.filter(d => d.did.includes(q.keyword) || d.subjectName.includes(q.keyword) || (d.orgName || '').includes(q.keyword))
    const page = paginate(list, q)
    return ok({ ...page, items: page.items.map(didDto) }, traceId)
  })),

  http.post(`${BASE}/did/verify`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const { did, message, signature } = await body(request)
    if (!did) throw new MockError(1001, 'did 必填')
    const r = mockVerifySignature(did, message, signature)
    writeAudit({ traceId, user, module: 'did', action: 'did:verify', resourceType: 'did', resourceId: did, result: r.valid ? 'success' : 'failed', riskLevel: r.abnormal ? 'high' : r.valid ? 'low' : 'medium', detail: r.valid ? 'SM2 验签通过' : `验签失败：${r.reason}` })
    if (r.abnormal) raiseAlert({ ruleCode: 'R02_ABNORMAL_DID', riskLevel: 'high', message: `已${r.rec.status === 'frozen' ? '冻结' : '注销'} DID ${did.slice(0, 30)}… 尝试验签接入`, actorDid: did, traceId })
    return ok({ valid: r.valid, subjectType: r.rec?.subjectType || null, status: r.rec?.status || null, reason: r.reason }, traceId)
  })),

  http.post(`${BASE}/did/resolve`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    const { dids = [] } = await body(request)
    const items = (Array.isArray(dids) ? dids : []).map(did => {
      const d = db.dids.find(x => x.did === did)
      return d ? { did, found: true, subjectType: d.subjectType, subjectName: d.subjectName, status: d.status, didDocument: d.didDocument } : { did, found: false }
    })
    return ok({ items, total: items.length }, traceId)
  })),

  http.get(`${BASE}/did/:did`, handle(async ({ request, params, traceId }) => {
    requireAuth(request)
    const did = decodeURIComponent(params.did)
    const d = db.dids.find(x => x.did === did)
    if (!d) throw new MockError(1005, 'DID 不存在')
    return ok({ ...didDto(d), didDocument: d.didDocument, keys: db.keys.filter(k => k.did === did) }, traceId)
  })),

  http.post(`${BASE}/did/:did/status`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    const did = decodeURIComponent(params.did)
    const d = db.dids.find(x => x.did === did)
    if (!d) throw new MockError(1005, 'DID 不存在')
    const { action, reason = '' } = await body(request)
    const map = { freeze: 'frozen', unfreeze: 'active', revoke: 'revoked' }
    if (!map[action]) throw new MockError(1001, 'action 须为 freeze|unfreeze|revoke')
    if (d.status === 'revoked') throw new MockError(1006, '已注销的 DID 不可变更状态')
    if (action === 'unfreeze' && d.status !== 'frozen') throw new MockError(1006, '仅冻结状态可解冻')
    d.status = map[action]
    d.updatedAt = now()
    db.keys.filter(k => k.did === did && k.status !== 'revoked').forEach(k => { k.status = action === 'unfreeze' ? 'active' : map[action] })
    const node = db.nodes.find(n => n.did === did)
    if (node) node.didStatus = d.status
    const ev = writeEvidence({ category: 'identity', refId: did, actorDid: user.did, traceId, payload: { op: `did:${action}`, did, reason, status: d.status } })
    writeAudit({ traceId, user, module: 'did', action: `did:${action}`, resourceType: 'did', resourceId: did, riskLevel: action === 'revoke' ? 'medium' : 'low', detail: `${d.subjectName} → ${d.status}${reason ? '：' + reason : ''}`, evidenceId: ev.evidence_id })
    return ok({ did, status: d.status, evidenceId: ev.evidence_id }, traceId)
  })),

  http.post(`${BASE}/did/:did/rotate-key`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    const did = decodeURIComponent(params.did)
    const d = db.dids.find(x => x.did === did)
    if (!d) throw new MockError(1005, 'DID 不存在')
    if (d.status !== 'active') throw new MockError(1006, '非 active 状态的 DID 不能轮换密钥')
    const old = db.keys.filter(k => k.did === did && k.status === 'active').sort((a, b) => b.version - a.version)[0]
    const version = (db.keys.filter(k => k.did === did).reduce((m, k) => Math.max(m, k.version), 0)) + 1
    const kp = genKeyPair()
    if (old) old.status = 'revoked'
    const key = { id: nextId('key'), did, algorithm: 'SM2', publicKey: kp.publicKey, status: 'active', version, boundAt: now(), expireAt: toIso8(new Date(Date.now() + 365 * DAY)) }
    db.keys.push(key)
    db.keyRotations.push({ id: nextId('keyRotation'), keyId: old?.id || null, did, fromVersion: old?.version || null, toVersion: version, reason: '手动轮换', operator: user.did, at: now() })
    d.didDocument.verificationMethod[0].publicKeyHex = kp.publicKey
    d.didDocument.updated = now()
    d.updatedAt = now()
    const ev = writeEvidence({ category: 'identity', refId: did, actorDid: user.did, traceId, payload: { op: 'did:rotate-key', did, version, publicKey: kp.publicKey } })
    writeAudit({ traceId, user, module: 'key', action: 'key:rotate', resourceType: 'did', resourceId: did, riskLevel: 'medium', detail: `密钥轮换至 v${version}`, evidenceId: ev.evidence_id })
    return ok({ did, keyId: key.id, version, publicKey: kp.publicKey, privateKey: kp.privateKey, evidenceId: ev.evidence_id, didDocument: d.didDocument }, traceId)
  }))
]
