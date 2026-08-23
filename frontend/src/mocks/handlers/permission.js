/** 权限控制中心（契约 §2.5） */
import { http } from 'msw'
import { db, RESOURCES, ACTIONS, nextId, permissionsOfRoles } from '../db.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, requirePerm, writeAudit, writeEvidence, raiseAlert, MockError, now } from '../helpers.js'
import { pushNotice } from './notice.js'

const RES_LABEL = { asset: '数据资产', model: '模型', dispatch: '调度指令', evidence: '存证', algo: '算法', user: '用户' }
const ACT_LABEL = { read: '读取', write: '写入', execute: '执行', issue: '签发', export: '导出', manage: '管理' }
const resLabel = t => RES_LABEL[t] || t || '资源'
const actLabel = a => ACT_LABEL[a] || a || ''

/** 审批人 = 具备 user:manage 的角色，与后端 APPROVER_ROLES 对应 */
function approverDids(excludeDid) {
  return db.users
    .filter(u => u.status !== 'disabled' && u.did && u.did !== excludeDid
                 && permissionsOfRoles(u.roles).includes('user:manage'))
    .map(u => u.did)
}

/** 请求体里的 grants 可能是对象（旧形状）或契约数组形状，统一成 {资源: [动作]} */
function toGrantsMap(data) {
  if (data?.grantsMap && typeof data.grantsMap === 'object' && !Array.isArray(data.grantsMap)) return data.grantsMap
  const g = data?.grants
  if (Array.isArray(g)) {
    const map = {}
    for (const item of g) {
      if (!item?.resourceType || !item?.action) continue
      ;(map[item.resourceType] = map[item.resourceType] || []).push(item.action)
    }
    return map
  }
  return g && typeof g === 'object' ? g : {}
}

function roleDto(r) {
  const { scope, ...rest } = r
  return { ...rest, scope: scope || 'all', permissions: permissionsOfRoles([r.code]) }
}

/** R03：同一主体 10 分钟内权限变更 ≥5 次 */
function checkR03(did, traceId) {
  const cutoff = Date.now() - 10 * 60 * 1000
  const n = db.permChangeLogs.filter(l => l.did === did && new Date(l.at).getTime() > cutoff).length
  if (n >= 5 && n % 5 === 0) raiseAlert({ ruleCode: 'R03_PERM_CHURN', riskLevel: 'medium', message: `主体 ${did.slice(0, 30)}… 10 分钟内权限变更 ${n} 次`, actorDid: did, traceId })
}

/** 权限校验核心：角色矩阵 → 授权记录 */
export function checkAccess(user, { resourceType, resourceId, action }) {
  const perm = `${resourceType}:${action}`
  const rolePerms = permissionsOfRoles(user.roles)
  const onlyOwn = user.roles.every(code => db.roles.find(r => r.code === code)?.scope === 'own')
  if (rolePerms.includes(perm)) {
    if (!onlyOwn || action === 'write') return { allowed: true, reason: `角色 ${user.roles.join(',')} 具备 ${perm} 权限`, matchedRule: `role:${user.roles[0]}` }
    // 仅自有：检查资源归属或授权
    const asset = resourceType === 'asset' ? db.assets.find(a => String(a.id) === String(resourceId)) : null
    if (asset && (asset.sourceDid === user.did || asset.ownerDid === user.did)) return { allowed: true, reason: '资源归属当前主体', matchedRule: 'owner' }
  }
  const grant = db.grants.find(g => g.did === user.did && g.status === 'active' && g.resourceType === resourceType && g.action === action && (g.resourceId === String(resourceId) || g.resourceId === '*') && (!g.expireAt || new Date(g.expireAt) > new Date()))
  if (grant) return { allowed: true, reason: `命中授权记录 #${grant.id}`, matchedRule: `grant:${grant.id}` }
  return { allowed: false, reason: `角色 ${user.roles.join(',')} 不具备 ${perm} 权限`, matchedRule: null }
}

export const permissionHandlers = [
  http.get(`${BASE}/roles`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    return ok({ items: db.roles.map(roleDto), total: db.roles.length }, traceId)
  })),

  http.post(`${BASE}/roles`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'user:manage', { request, traceId, resourceType: 'role', detail: '非管理员新建角色' })
    const data = await body(request)
    if (!data.code || !data.name) throw new MockError(1001, 'code/name 必填')
    if (!/^[a-z_]{3,32}$/.test(data.code)) throw new MockError(1001, 'code 须为小写字母与下划线')
    if (db.roles.find(r => r.code === data.code)) throw new MockError(1006, '角色已存在')
    const grants = {}
    for (const [res, acts] of Object.entries(toGrantsMap(data))) if (RESOURCES.includes(res)) grants[res] = (acts || []).filter(a => ACTIONS.includes(a))
    const role = { code: data.code, name: data.name, builtin: false, grants, createdAt: now() }
    db.roles.push(role)
    const ev = writeEvidence({ category: 'permission', refId: `role-${role.code}`, actorDid: user.did, traceId, payload: { op: 'role:create', code: role.code, grants } })
    writeAudit({ traceId, user, module: 'permission', action: 'role:create', resourceType: 'role', resourceId: role.code, riskLevel: 'medium', detail: `新建角色 ${role.name}`, evidenceId: ev.evidence_id })
    return ok(roleDto(role), traceId)
  })),

  http.put(`${BASE}/roles/:code`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'user:manage', { request, traceId, resourceType: 'role', detail: '非管理员修改角色' })
    const role = db.roles.find(r => r.code === params.code)
    if (!role) throw new MockError(1005, '角色不存在')
    const data = await body(request)
    if (data.name) role.name = data.name
    if (data.grants || data.grantsMap) {
      const grants = {}
      for (const [res, acts] of Object.entries(toGrantsMap(data))) if (RESOURCES.includes(res) || res === 'user') grants[res] = (acts || []).filter(a => ACTIONS.includes(a) || a === 'manage')
      role.grants = grants
    }
    role.updatedAt = now()
    const ev = writeEvidence({ category: 'permission', refId: `role-${role.code}`, actorDid: user.did, traceId, payload: { op: 'role:update', code: role.code, grants: role.grants } })
    writeAudit({ traceId, user, module: 'permission', action: 'role:update', resourceType: 'role', resourceId: role.code, riskLevel: 'medium', detail: `修改角色 ${role.name} 权限`, evidenceId: ev.evidence_id })
    return ok(roleDto(role), traceId)
  })),

  http.get(`${BASE}/permissions/matrix`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    return ok({ resources: RESOURCES, actions: ACTIONS, roles: db.roles.map(r => ({ code: r.code, name: r.name, scope: r.scope || 'all', grants: r.grants })) }, traceId)
  })),

  http.post(`${BASE}/permissions/apply`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const data = await body(request)
    if (!RESOURCES.includes(data.resourceType) || !ACTIONS.includes(data.action)) throw new MockError(1001, 'resourceType/action 非法')
    if (!data.resourceId) throw new MockError(1001, 'resourceId 必填')
    const dup = db.applications.find(a => a.applicantDid === user.did && a.resourceType === data.resourceType && a.resourceId === String(data.resourceId) && a.action === data.action && a.status === 'pending')
    if (dup) throw new MockError(1006, `已有待审批的相同申请 #${dup.id}`)
    const id = nextId('application')
    const ev = writeEvidence({ category: 'permission', refId: `app-${id}`, actorDid: user.did, traceId, payload: { op: 'permission:apply', applicationId: id, resourceType: data.resourceType, resourceId: String(data.resourceId), action: data.action, applicantDid: user.did } })
    const app = {
      id, applicantDid: user.did, applicantName: user.realName, resourceType: data.resourceType, resourceId: String(data.resourceId), action: data.action, reason: data.reason || '',
      status: 'pending', expireAt: data.expireAt || null, createdAt: ev.created_at, updatedAt: ev.created_at, evidenceId: ev.evidence_id, traceId, reviewerDid: null, reviewComment: null, reviewedAt: null
    }
    db.applications.push(app)
    writeAudit({ traceId, user, module: 'permission', action: 'permission:apply', resourceType: data.resourceType, resourceId: data.resourceId, detail: `申请 ${data.resourceType}:${data.action}（${data.resourceId}）`, evidenceId: ev.evidence_id })
    pushNotice(approverDids(user.did), {
      category: 'permission_apply', level: 'warning', title: '有新的权限申请待审批',
      content: `${app.applicantName || app.applicantDid} 申请${resLabel(app.resourceType)} ${app.resourceId} 的${actLabel(app.action)}权限。理由：${app.reason || '未填写'}`,
      link: `/permission?tab=apps&id=${id}`, refType: 'permission_application', refId: id, actor: user
    })
    return ok({ id, status: 'pending', applicantDid: user.did, createdAt: app.createdAt, evidenceId: ev.evidence_id, traceId }, traceId)
  })),

  http.get(`${BASE}/permissions/applications`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const q = query(request)
    let list = db.applications.slice().reverse()
    // 非管理/监管角色只看自己的申请
    const canReview = permissionsOfRoles(user.roles).includes('user:manage') || user.roles.includes('regulator') || user.roles.includes('grid_dispatcher')
    if (!canReview) list = list.filter(a => a.applicantDid === user.did)
    if (q.status) list = list.filter(a => a.status === q.status)
    if (q.applicantDid) list = list.filter(a => a.applicantDid === q.applicantDid)
    if (q.resourceType) list = list.filter(a => a.resourceType === q.resourceType)
    return ok(paginate(list, q), traceId)
  })),

  http.post(`${BASE}/permissions/applications/:id/approve`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'user:manage', { request, traceId, resourceType: 'permission', resourceId: params.id, detail: '非管理员审批权限申请' })
    const app = db.applications.find(a => a.id === Number(params.id))
    if (!app) throw new MockError(1005, '申请不存在')
    if (app.status !== 'pending') throw new MockError(1006, `申请已处于 ${app.status} 状态`)
    const data = await body(request)
    app.status = 'approved'; app.reviewerDid = user.did; app.reviewComment = data.comment || '同意'; app.reviewedAt = now(); app.updatedAt = app.reviewedAt
    if (data.expireAt) app.expireAt = data.expireAt
    const ev = writeEvidence({ category: 'permission', refId: `app-${app.id}`, actorDid: user.did, traceId, payload: { op: 'permission:approve', applicationId: app.id, resourceType: app.resourceType, resourceId: app.resourceId, action: app.action, granteeDid: app.applicantDid } })
    const grant = { id: nextId('grant'), applicationId: app.id, did: app.applicantDid, granteeName: app.applicantName, resourceType: app.resourceType, resourceId: app.resourceId, action: app.action, grantedBy: user.did, grantedAt: app.reviewedAt, expireAt: app.expireAt, status: 'active', evidenceId: ev.evidence_id, traceId }
    db.grants.push(grant)
    db.permChangeLogs.push({ id: nextId('permChange'), applicationId: app.id, did: app.applicantDid, change: 'approved', operator: user.did, at: app.reviewedAt, evidenceId: ev.evidence_id })
    const asset = app.resourceType === 'asset' ? db.assets.find(a => String(a.id) === app.resourceId) : null
    if (asset) asset.authStatus = 'authorized'
    writeAudit({ traceId, user, module: 'permission', action: 'permission:approve', resourceType: app.resourceType, resourceId: app.resourceId, riskLevel: 'medium', detail: `审批通过申请 #${app.id}，生成授权 #${grant.id}`, evidenceId: ev.evidence_id })
    checkR03(app.applicantDid, traceId)
    pushNotice([app.applicantDid], {
      category: 'permission_result', level: 'success', title: '权限申请已通过',
      content: `${resLabel(app.resourceType)} ${app.resourceId} 的${actLabel(app.action)}权限申请已通过。审批意见：${app.reviewComment || '无'}`,
      link: '/permission?tab=grants', refType: 'permission_application', refId: app.id, actor: user
    })
    return ok({ id: app.id, status: app.status, grantId: grant.id, evidenceId: ev.evidence_id, reviewedAt: app.reviewedAt }, traceId)
  })),

  http.post(`${BASE}/permissions/applications/:id/reject`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'user:manage', { request, traceId, resourceType: 'permission', resourceId: params.id, detail: '非管理员驳回权限申请' })
    const app = db.applications.find(a => a.id === Number(params.id))
    if (!app) throw new MockError(1005, '申请不存在')
    if (app.status !== 'pending') throw new MockError(1006, `申请已处于 ${app.status} 状态`)
    const data = await body(request)
    app.status = 'rejected'; app.reviewerDid = user.did; app.reviewComment = data.comment || data.reason || '驳回'; app.reviewedAt = now(); app.updatedAt = app.reviewedAt
    const ev = writeEvidence({ category: 'permission', refId: `app-${app.id}`, actorDid: user.did, traceId, payload: { op: 'permission:reject', applicationId: app.id, comment: app.reviewComment } })
    db.permChangeLogs.push({ id: nextId('permChange'), applicationId: app.id, did: app.applicantDid, change: 'rejected', operator: user.did, at: app.reviewedAt, evidenceId: ev.evidence_id })
    writeAudit({ traceId, user, module: 'permission', action: 'permission:reject', resourceType: app.resourceType, resourceId: app.resourceId, detail: `驳回申请 #${app.id}：${app.reviewComment}`, evidenceId: ev.evidence_id })
    checkR03(app.applicantDid, traceId)
    pushNotice([app.applicantDid], {
      category: 'permission_result', level: 'warning', title: '权限申请被驳回',
      content: `${resLabel(app.resourceType)} ${app.resourceId} 的${actLabel(app.action)}权限申请被驳回。审批意见：${app.reviewComment || '无'}`,
      link: `/permission?tab=apps&id=${app.id}`, refType: 'permission_application', refId: app.id, actor: user
    })
    return ok({ id: app.id, status: app.status, evidenceId: ev.evidence_id, reviewedAt: app.reviewedAt }, traceId)
  })),

  http.get(`${BASE}/permissions/grants`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const q = query(request)
    let list = db.grants.slice().reverse()
    const canSeeAll = permissionsOfRoles(user.roles).includes('user:manage') || user.roles.includes('regulator') || user.roles.includes('grid_dispatcher')
    if (!canSeeAll) list = list.filter(g => g.did === user.did)
    if (q.did) list = list.filter(g => g.did === q.did)
    if (q.status) list = list.filter(g => g.status === q.status)
    if (q.resourceType) list = list.filter(g => g.resourceType === q.resourceType)
    return ok(paginate(list, q), traceId)
  })),

  http.post(`${BASE}/permissions/grants/:id/revoke`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'user:manage', { request, traceId, resourceType: 'permission', resourceId: params.id, detail: '非管理员回收授权' })
    const g = db.grants.find(x => x.id === Number(params.id))
    if (!g) throw new MockError(1005, '授权不存在')
    if (g.status === 'revoked') throw new MockError(1006, '授权已回收')
    const revokeReason = (await body(request))?.reason || '管理员手动回收授权'
    g.status = 'revoked'; g.revokedAt = now(); g.revokedBy = user.did; g.revokeReason = revokeReason
    const ev = writeEvidence({ category: 'permission', refId: `grant-${g.id}`, actorDid: user.did, traceId, payload: { op: 'permission:revoke', grantId: g.id, did: g.did } })
    db.permChangeLogs.push({ id: nextId('permChange'), applicationId: g.applicationId, did: g.did, change: 'revoked', operator: user.did, at: g.revokedAt, evidenceId: ev.evidence_id })
    const stillGranted = db.grants.some(x => x.status === 'active' && x.resourceType === 'asset' && x.resourceId === g.resourceId)
    const asset = g.resourceType === 'asset' ? db.assets.find(a => String(a.id) === g.resourceId) : null
    if (asset && !stillGranted) asset.authStatus = 'unauthorized'
    writeAudit({ traceId, user, module: 'permission', action: 'permission:revoke', resourceType: g.resourceType, resourceId: g.resourceId, riskLevel: 'medium', detail: `回收授权 #${g.id}`, evidenceId: ev.evidence_id })
    checkR03(g.did, traceId)
    pushNotice([g.did], {
      category: 'permission_revoke', level: 'warning', title: '你的一项授权已被回收',
      content: `${resLabel(g.resourceType)} ${g.resourceId} 的${actLabel(g.action)}权限已被回收。原因：${revokeReason}`,
      link: '/permission?tab=grants', refType: 'perm_grant', refId: g.id, actor: user
    })
    return ok({ id: g.id, status: g.status, evidenceId: ev.evidence_id }, traceId)
  })),

  http.post(`${BASE}/permissions/check`, handle(async ({ request, traceId }) => {
    const actor = requireAuth(request)
    const data = await body(request)
    if (!data.resourceType || !data.action) throw new MockError(1001, 'resourceType/action 必填')
    const target = data.did ? db.users.find(u => u.did === data.did) : actor
    if (!target) {
      return ok({ allowed: false, reason: 'DID 未绑定平台用户', matchedRule: null, level: null }, traceId)
    }
    const r = checkAccess(target, data)
    const asset = data.resourceType === 'asset' ? db.assets.find(a => String(a.id) === String(data.resourceId)) : null
    writeAudit({ traceId, user: actor, module: 'permission', action: 'permission:check', resourceType: data.resourceType, resourceId: data.resourceId, result: r.allowed ? 'success' : 'denied', riskLevel: r.allowed ? 'low' : 'medium', detail: `校验 ${target.realName} ${data.resourceType}:${data.action} → ${r.allowed ? '允许' : '拒绝'}` })
    return ok({ ...r, level: asset?.level || (data.resourceType === 'dispatch' ? 'L3' : null) }, traceId)
  }))
]
