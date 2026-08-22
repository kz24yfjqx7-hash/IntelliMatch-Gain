/** 智能调度（契约 §2.10） */
import request from './request'

export function createDispatchTask(data) {
  return request.post('/dispatch/tasks', data)
}
/**
 * 归一化调度任务（兼容真后端与 mock）：
 *  - 真后端 status 停在 success，用 issued/ackStatus(none|partial|all) 表达下发/回执阶段；mock 直接给 issued/acked
 *  - 真后端没有 targets 字段，由策略中非 idle 动作的节点推导
 */
export function normalizeDispatchTask(t) {
  if (!t || typeof t !== 'object') return t
  const out = { ...t }
  if (out.status === 'success' && out.issued) out.status = out.ackStatus === 'all' ? 'acked' : 'issued'
  if (!Array.isArray(out.targets) || !out.targets.length) {
    out.targets = (out.strategy?.actions || []).filter(a => a && a.action !== 'idle').map(a => a.nodeId)
  }
  // 已回执节点集合：真后端 ackDetail=[{nodeId,accepted,...}]；mock 为 ack 对象/ackHistory
  if (!Array.isArray(out.ackedNodes)) {
    const det = Array.isArray(out.ackDetail) ? out.ackDetail : []
    out.ackedNodes = det.filter(a => a && a.accepted !== false && a.nodeId).map(a => a.nodeId)
    if (out.status === 'acked' && !out.ackedNodes.length) out.ackedNodes = [...out.targets]
  }
  return out
}
export async function listDispatchTasks(params = {}) {
  const data = await request.get('/dispatch/tasks', { params })
  if (data && Array.isArray(data.items)) data.items = data.items.map(normalizeDispatchTask)
  return data
}
export async function getDispatchTask(id) {
  return normalizeDispatchTask(await request.get(`/dispatch/tasks/${id}`))
}
export function runDispatchTask(id) {
  return request.post(`/dispatch/tasks/${id}/run`)
}
export function issueDispatchTask(id, { signature }) {
  return request.post(`/dispatch/tasks/${id}/issue`, { signature })
}
export function ackDispatchTask(id, data = {}) {
  // 真后端回执体为 {nodeId, accepted, detail}；mock 接受任意扩展字段，这里两种都带上
  const accepted = data.accepted ?? (data.status ? data.status === 'success' : true)
  const detail = data.detail ?? (data.actualPowerKw != null ? `实际功率 ${data.actualPowerKw} kW` : undefined)
  return request.post(`/dispatch/tasks/${id}/ack`, { ...data, accepted, ...(detail !== undefined ? { detail } : {}) })
}
