/**
 * handler 公共工具：响应包装、鉴权、分页、审计落库、存证、告警、WS 推送。
 */
import { HttpResponse } from 'msw'
import { db, chain, permissionsOfRoles, nextId } from './db.js'
import { toIso8 } from '../utils/format.js'
import { sha256Hex } from '../utils/sha256.js'
import { wsMock } from './wsMock.js'
import { genTraceId } from '../utils/traceId.js'

export const BASE = '*/api/v1'

const HTTP_BY_CODE = { 0: 200, 1001: 400, 1002: 401, 1003: 403, 1004: 403, 1005: 404, 1006: 409, 1007: 429, 2001: 502, 2002: 502, 5000: 500 }

export class MockError extends Error {
  constructor(code, message, data = null) {
    super(message)
    this.code = code
    this.data = data
  }
}

export const now = () => toIso8(new Date())
export const delay = ms => new Promise(r => setTimeout(r, ms))

export function ok(data, traceId, extraHeaders = {}) {
  return HttpResponse.json({ code: 0, message: 'ok', data, traceId }, { status: 200, headers: { 'X-Trace-Id': traceId || '', ...extraHeaders } })
}

export function fail(code, message, traceId, data = null) {
  return HttpResponse.json({ code, message, data, traceId }, { status: HTTP_BY_CODE[code] || 500, headers: { 'X-Trace-Id': traceId || '' } })
}

export function getTraceId(request) {
  return request.headers.get('X-Trace-Id') || request.headers.get('x-trace-id') || genTraceId()
}

/* ---------- token ---------- */
function b64url(str) {
  const b = typeof btoa === 'function' ? btoa(unescape(encodeURIComponent(str))) : Buffer.from(str, 'utf8').toString('base64')
  return b.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
function b64urlDecode(str) {
  const s = str.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((str.length + 3) % 4)
  return typeof atob === 'function' ? decodeURIComponent(escape(atob(s))) : Buffer.from(s, 'base64').toString('utf8')
}

/** 生成 mock JWT（HS256 形式的三段式，签名为 sha256 摘要，仅演示） */
export function issueToken(user, expiresIn = 28800) {
  const header = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const iat = Math.floor(Date.now() / 1000)
  const payload = b64url(JSON.stringify({ sub: user.id, username: user.username, roles: user.roles, did: user.did, iat, exp: iat + expiresIn }))
  const sig = sha256Hex(`${header}.${payload}.mock-secret`).slice(0, 43)
  const token = `${header}.${payload}.${sig}`
  db.sessions.set(token, { userId: user.id, issuedAt: iat, expiresAt: iat + expiresIn })
  return token
}

export function parseToken(token) {
  if (!token) return null
  try {
    const [, payload] = token.split('.')
    const data = JSON.parse(b64urlDecode(payload))
    if (!data || data.exp * 1000 < Date.now()) return null
    return data
  } catch {
    return null
  }
}

/** 从请求解析当前用户；未登录返回 null */
export function currentUser(request) {
  const auth = request.headers.get('Authorization') || request.headers.get('authorization') || ''
  const token = auth.replace(/^Bearer\s+/i, '').trim()
  if (!token) return null
  const session = db.sessions.get(token)
  const payload = parseToken(token)
  if (!payload) return null
  if (!session) {
    // 刷新页面后 sessions（内存）丢失，但 token 仍合法：按 payload 恢复
    const u = db.users.find(x => x.id === payload.sub && x.username === payload.username)
    if (u) db.sessions.set(token, { userId: u.id, issuedAt: payload.iat, expiresAt: payload.exp })
    return u || null
  }
  return db.users.find(x => x.id === session.userId) || null
}

export function requireAuth(request) {
  const user = currentUser(request)
  if (!user) throw new MockError(1002, '未登录或 Token 已失效')
  return user
}

export function permissionsOf(user) {
  return permissionsOfRoles(user.roles)
}

/** 权限校验；拒绝时落 high 审计日志 + 计入 R01 规则 */
export function requirePerm(user, perm, { request, traceId, resourceType, resourceId, detail, alertImmediately = false } = {}) {
  if (permissionsOf(user).includes(perm)) return true
  const message = `角色 ${user.roles.join(',')} 无 ${perm} 权限`
  writeAudit({
    traceId, user, module: 'permission', action: perm, resourceType: resourceType || perm.split(':')[0], resourceId,
    result: 'denied', riskLevel: 'high', detail: detail || `越权尝试 ${perm}`, ip: clientIp(request)
  })
  hitR01(user, traceId, perm, alertImmediately)
  throw new MockError(1003, message)
}

/** R01：5 分钟内拒绝 ≥3 次告警；高危操作（dispatch:issue）首次即告警（演示需要） */
function hitR01(user, traceId, perm, immediately) {
  const key = user.did
  const list = (db.denyCounter.get(key) || []).filter(t => Date.now() - t < 5 * 60 * 1000)
  list.push(Date.now())
  db.denyCounter.set(key, list)
  if (immediately || list.length >= 3) {
    raiseAlert({ ruleCode: 'R01_UNAUTHORIZED', riskLevel: 'high', message: `${user.realName}（${user.roles[0]}）越权尝试 ${perm}，5 分钟内累计 ${list.length} 次`, actorDid: user.did, traceId })
    if (list.length >= 3) db.denyCounter.set(key, [])
  }
}

export function clientIp(request) {
  return (request && (request.headers.get('X-Forwarded-For') || request.headers.get('x-real-ip'))) || '127.0.0.1'
}

/* ---------- 查询 / 分页 ---------- */
export function query(request) {
  const url = new URL(request.url)
  const q = {}
  url.searchParams.forEach((v, k) => { q[k] = v })
  return q
}

export function paginate(items, q) {
  const page = Math.max(1, parseInt(q.page || '1', 10) || 1)
  const size = Math.min(200, Math.max(1, parseInt(q.size || '20', 10) || 20))
  const start = (page - 1) * size
  return { items: items.slice(start, start + size), total: items.length, page, size }
}

export async function body(request) {
  try {
    return await request.clone().json()
  } catch {
    return {}
  }
}

/** 时间范围过滤（from/to 为 ISO 或 YYYY-MM-DD） */
export function inRange(ts, from, to) {
  if (!ts) return false
  const t = new Date(ts).getTime()
  if (from && t < new Date(from.length === 10 ? from + 'T00:00:00+08:00' : from).getTime()) return false
  if (to && t > new Date(to.length === 10 ? to + 'T23:59:59+08:00' : to).getTime()) return false
  return true
}

/* ---------- 审计 / 存证 / 告警 ---------- */
export function writeAudit({ traceId, user, module, action, resourceType = null, resourceId = null, result = 'success', riskLevel = 'low', detail = '', ip = '127.0.0.1', evidenceId = null }) {
  const at = toIso8(new Date(), true)
  const rec = {
    id: nextId('audit'), traceId, actorDid: user?.did || null, actorName: user?.realName || '系统', module, action, resourceType,
    resourceId: resourceId == null ? null : String(resourceId), result, riskLevel, detail, ip, at, evidenceId,
    hash: 'sm3:' + sha256Hex(`${traceId}|${action}|${at}|${detail}`)
  }
  db.auditLogs.push(rec)
  const level = result === 'denied' || riskLevel === 'high' || riskLevel === 'critical' ? 'error' : result === 'failed' ? 'warn' : 'info'
  wsMock.emitLog(level, module, `${rec.actorName} ${action} ${result === 'success' ? '成功' : result === 'denied' ? '被拒绝' : result}${detail ? '：' + detail : ''}`, traceId)
  return rec
}

/** 写存证并推送 evidence_written */
export function writeEvidence({ category, refId, payload, actorDid, traceId }) {
  const rec = chain.append({ category, refId, payload, actorDid, traceId, createdAt: now() })
  wsMock.emit('evidence_written', { evidenceId: rec.evidence_id, category, blockHeight: rec.block_height }, traceId)
  return rec
}

export function raiseAlert({ ruleCode, riskLevel = 'high', message, actorDid = null, traceId = null }) {
  const alert = { id: nextId('alert'), ruleCode, riskLevel, message, actorDid, status: 'open', traceId, createdAt: now(), ackedAt: null, ackedBy: null }
  db.alerts.unshift(alert)
  wsMock.emit('audit_alert', { alertId: alert.id, ruleCode, riskLevel, message, actorDid }, traceId)
  return alert
}

/** 包装 handler：统一 traceId 注入与错误转换 */
export function handle(fn) {
  return async info => {
    const traceId = getTraceId(info.request)
    try {
      return await fn({ ...info, traceId })
    } catch (err) {
      if (err instanceof MockError) return fail(err.code, err.message, traceId, err.data)
      console.error('[mock] handler error', err)
      return fail(5000, err?.message || '服务器内部错误', traceId)
    }
  }
}

/** 公共 DTO */
export function userDto(u, withPermissions = false) {
  const dto = { id: u.id, username: u.username, realName: u.realName, roles: u.roles, did: u.did, orgName: u.orgName, status: u.status, createdAt: u.createdAt }
  if (withPermissions) dto.permissions = permissionsOfRoles(u.roles)
  return dto
}
