/** 智能调度（契约 §2.10） */
import request from './request'

export function createDispatchTask(data) {
  return request.post('/dispatch/tasks', data)
}
export function listDispatchTasks(params = {}) {
  return request.get('/dispatch/tasks', { params })
}
export function getDispatchTask(id) {
  return request.get(`/dispatch/tasks/${id}`)
}
export function runDispatchTask(id) {
  return request.post(`/dispatch/tasks/${id}/run`)
}
export function issueDispatchTask(id, { signature }) {
  return request.post(`/dispatch/tasks/${id}/issue`, { signature })
}
export function ackDispatchTask(id, data = {}) {
  return request.post(`/dispatch/tasks/${id}/ack`, data)
}
