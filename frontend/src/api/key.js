/** 密钥管理（契约 §2.3） */
import request from './request'

export function listKeys(params = {}) {
  return request.get('/keys', { params })
}
export function createKey(data) {
  return request.post('/keys', data)
}
export function freezeKey(id) {
  return request.post(`/keys/${id}/freeze`)
}
export function revokeKey(id) {
  return request.post(`/keys/${id}/revoke`)
}
export function keyHistory(id) {
  return request.get(`/keys/${id}/history`)
}
