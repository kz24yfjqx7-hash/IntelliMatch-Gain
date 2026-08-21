/** 联邦学习（契约 §2.9） */
import request from './request'

export function createFlTask(data) {
  return request.post('/fl/tasks', data)
}
export function listFlTasks(params = {}) {
  return request.get('/fl/tasks', { params })
}
export function getFlTask(id) {
  return request.get(`/fl/tasks/${id}`)
}
export function startFlTask(id) {
  return request.post(`/fl/tasks/${id}/start`)
}
export function cancelFlTask(id) {
  return request.post(`/fl/tasks/${id}/cancel`)
}
export function getFlRounds(id) {
  return request.get(`/fl/tasks/${id}/rounds`)
}
export function listFlModels(params = {}) {
  return request.get('/fl/models', { params })
}
export function publishFlModel(version) {
  return request.post(`/fl/models/${version}/publish`)
}
