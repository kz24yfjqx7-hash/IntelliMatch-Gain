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
/**
 * 后续步骤复用任务创建时的 traceId（契约 1.2：客户端可用 X-Trace-Id 指定，backend 沿用），
 * 这样 `/audit/trace/{traceId}` 能把「创建 → 启动 → 每轮上链 → 完成」串成一条完整链路，
 * 而不是每个请求各自一条只有 1 步的孤链。传 undefined 时退回后端自行生成。
 */
const withTrace = traceId => (traceId ? { headers: { 'X-Trace-Id': traceId } } : undefined)

export function startFlTask(id, traceId) {
  return request.post(`/fl/tasks/${id}/start`, null, withTrace(traceId))
}
export function cancelFlTask(id, traceId) {
  return request.post(`/fl/tasks/${id}/cancel`, null, withTrace(traceId))
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
