/** 认证与用户（契约 §2.1） */
import request from './request'

export function login({ username, password }) {
  return request.post('/auth/login', { username, password })
}
export function logout(config) {
  return request.post('/auth/logout', null, config)
}
export function getMe() {
  return request.get('/auth/me')
}
export function listUsers(params = {}) {
  return request.get('/users', { params })
}
export function createUser(data) {
  return request.post('/users', data)
}
export function updateUser(id, data) {
  return request.put(`/users/${id}`, data)
}
export function deleteUser(id) {
  return request.delete(`/users/${id}`)
}
