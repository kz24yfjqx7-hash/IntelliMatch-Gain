/** 权限控制中心（契约 §2.5） */
import request from './request'

/**
 * 角色归一化（兼容真后端与 mock）：
 *  - 真后端 `GET /roles` 的 data 是**数组**，`grants` 是 `[{resourceType,action,scope}]`，内置标记叫 `isBuiltin`
 *  - mock 桩返回 `{items:[...]}`，`grants` 是 `{资源: [动作]}` 对象，内置标记叫 `builtin`
 * 页面统一按「数组 + grants 对象 + builtin」使用。
 */
export function normalizeRole(r) {
  if (!r || typeof r !== 'object') return r
  let grants = r.grants || {}
  if (Array.isArray(r.grants)) {
    grants = {}
    for (const g of r.grants) {
      if (!g?.resourceType || !g?.action) continue
      ;(grants[g.resourceType] = grants[g.resourceType] || []).push(g.action)
    }
  }
  // permissions：扁平 `资源:动作` 数组（mock 桩直接给，真后端要自己拼）
  const permissions = r.permissions
    || Object.entries(grants).flatMap(([res, acts]) => (acts || []).map(a => `${res}:${a}`))
  // scope：真后端在每条 grant 上带 scope，全部为 own 时角色即 own
  const scopes = Array.isArray(r.grants) ? r.grants.map(g => g.scope).filter(Boolean) : []
  const scope = r.scope || (scopes.length && scopes.every(x => x === 'own') ? 'own' : 'all')
  return { ...r, grants, permissions, builtin: r.builtin ?? r.isBuiltin ?? false, scope }
}

/** 返回角色数组（两种后端形状都归一化成数组） */
export async function listRoles() {
  const data = await request.get('/roles')
  const items = Array.isArray(data) ? data : (data?.items || [])
  return items.map(normalizeRole)
}

/** grants 对象 → 契约要求的 `[{resourceType,action,scope}]` 数组；已是数组则原样返回 */
function grantsToList(grants) {
  if (Array.isArray(grants)) return grants
  const out = []
  for (const [resourceType, actions] of Object.entries(grants || {})) {
    for (const action of actions || []) out.push({ resourceType, action, scope: 'all' })
  }
  return out
}

export function createRole(data) {
  // grantsMap 同时保留对象形式，MSW 桩按对象解析，真后端按数组解析
  return request.post('/roles', { ...data, grants: grantsToList(data?.grants), grantsMap: data?.grants })
}
export function updateRole(code, data) {
  return request.put(`/roles/${code}`, { ...data, grants: grantsToList(data?.grants), grantsMap: data?.grants })
}
export function getPermissionMatrix() {
  return request.get('/permissions/matrix')
}
export function applyPermission(data) {
  return request.post('/permissions/apply', data)
}
export function listApplications(params = {}) {
  return request.get('/permissions/applications', { params })
}
export function approveApplication(id, data = {}) {
  return request.post(`/permissions/applications/${id}/approve`, data)
}
export function rejectApplication(id, data = {}) {
  return request.post(`/permissions/applications/${id}/reject`, data)
}
export function listGrants(params = {}) {
  return request.get('/permissions/grants', { params })
}
/** 回收授权。后端要求 reason（权限变更须留痕，写入 perm_change_log 并上链） */
export function revokeGrant(id, { reason } = {}) {
  return request.post(`/permissions/grants/${id}/revoke`, { reason: reason || '管理员手动回收授权' })
}
export function checkPermission(data) {
  return request.post('/permissions/check', data)
}
