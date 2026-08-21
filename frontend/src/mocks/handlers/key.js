/** 密钥管理（契约 §2.3） */
import { http } from 'msw'
import { db, genKeyPair, nextId } from '../db.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, writeAudit, writeEvidence, MockError, now } from '../helpers.js'
import { toIso8 } from '../../utils/format.js'

const DAY = 86400000

export const keyHandlers = [
  http.get(`${BASE}/keys`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    const q = query(request)
    let list = db.keys.slice().reverse()
    if (q.did) list = list.filter(k => k.did === q.did)
    if (q.status) list = list.filter(k => k.status === q.status)
    if (q.algorithm) list = list.filter(k => k.algorithm === q.algorithm)
    return ok(paginate(list, q), traceId)
  })),

  http.post(`${BASE}/keys`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const data = await body(request)
    const did = data.did
    const d = db.dids.find(x => x.did === did)
    if (!d) throw new MockError(1005, 'DID 不存在')
    const algorithm = ['SM2', 'ECC', 'RSA'].includes(data.algorithm) ? data.algorithm : 'SM2'
    const version = db.keys.filter(k => k.did === did).reduce((m, k) => Math.max(m, k.version), 0) + 1
    const kp = genKeyPair()
    const key = { id: nextId('key'), did, algorithm, publicKey: kp.publicKey, status: 'active', version, boundAt: now(), expireAt: toIso8(new Date(Date.now() + (Number(data.validDays) || 365) * DAY)) }
    db.keys.push(key)
    const ev = writeEvidence({ category: 'identity', refId: did, actorDid: user.did, traceId, payload: { op: 'key:create', did, keyId: key.id, algorithm, version, publicKey: kp.publicKey } })
    writeAudit({ traceId, user, module: 'key', action: 'key:create', resourceType: 'did', resourceId: did, detail: `生成并绑定 ${algorithm} 密钥 v${version}`, evidenceId: ev.evidence_id })
    return ok({ ...key, privateKey: kp.privateKey, evidenceId: ev.evidence_id }, traceId)
  })),

  http.post(`${BASE}/keys/:id/freeze`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    const key = db.keys.find(k => k.id === Number(params.id))
    if (!key) throw new MockError(1005, '密钥不存在')
    if (key.status !== 'active') throw new MockError(1006, `密钥当前状态为 ${key.status}`)
    key.status = 'frozen'
    const ev = writeEvidence({ category: 'identity', refId: key.did, actorDid: user.did, traceId, payload: { op: 'key:freeze', keyId: key.id, did: key.did } })
    writeAudit({ traceId, user, module: 'key', action: 'key:freeze', resourceType: 'did', resourceId: key.did, riskLevel: 'medium', detail: `冻结密钥 #${key.id}`, evidenceId: ev.evidence_id })
    return ok({ id: key.id, status: key.status, evidenceId: ev.evidence_id }, traceId)
  })),

  http.post(`${BASE}/keys/:id/revoke`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    const key = db.keys.find(k => k.id === Number(params.id))
    if (!key) throw new MockError(1005, '密钥不存在')
    if (key.status === 'revoked') throw new MockError(1006, '密钥已注销')
    key.status = 'revoked'
    const ev = writeEvidence({ category: 'identity', refId: key.did, actorDid: user.did, traceId, payload: { op: 'key:revoke', keyId: key.id, did: key.did } })
    writeAudit({ traceId, user, module: 'key', action: 'key:revoke', resourceType: 'did', resourceId: key.did, riskLevel: 'medium', detail: `注销密钥 #${key.id}`, evidenceId: ev.evidence_id })
    return ok({ id: key.id, status: key.status, evidenceId: ev.evidence_id }, traceId)
  })),

  http.get(`${BASE}/keys/:id/history`, handle(async ({ request, params, traceId }) => {
    requireAuth(request)
    const key = db.keys.find(k => k.id === Number(params.id))
    if (!key) throw new MockError(1005, '密钥不存在')
    const versions = db.keys.filter(k => k.did === key.did).sort((a, b) => a.version - b.version)
    const rotations = db.keyRotations.filter(r => r.did === key.did)
    return ok({ keyId: key.id, did: key.did, items: rotations, total: rotations.length, versions, rotations }, traceId)
  }))
]
