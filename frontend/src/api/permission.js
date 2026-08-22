/** 权限控制中心（契约 §2.5） */
import request from './request'

export function listRoles() {
  return request.get('/roles')
}
export function createRole(data) {
  return request.post('/roles', data)
}
export function updateRole(code, data) {
  return request.put(`/roles/${code}`, data)
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
