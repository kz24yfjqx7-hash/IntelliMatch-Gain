/** 节点与拓扑（契约 §2.8） */
import request from './request'

export function listNodes(params = {}) {
  return request.get('/nodes', { params })
}
export function getNode(id) {
  return request.get(`/nodes/${id}`)
}
export function getNodeMetrics(id, params = {}) {
  return request.get(`/nodes/${id}/metrics`, { params })
}
export function nodeOnline(id, data) {
  return request.post(`/nodes/${id}/online`, data)
}
