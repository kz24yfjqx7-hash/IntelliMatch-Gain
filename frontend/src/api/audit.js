/** 安全审计中心（契约 §2.7） */
import request, { download } from './request'

export function listAuditLogs(params = {}) {
  return request.get('/audit/logs', { params })
}
export function getAuditTrace(traceId) {
  return request.get(`/audit/trace/${traceId}`)
}
export function listAlerts(params = {}) {
  return request.get('/audit/alerts', { params })
}
/** 确认告警。id 为数字主键（列表项的 `id`）；WS 推送只带字符串 `alertId`，由 store 换算后再调用 */
export function ackAlert(id) {
  return request.post(`/audit/alerts/${id}/ack`)
}
export function getAuditReport({ period = 'day', date } = {}) {
  return request.get('/audit/report', { params: { period, date } })
}
export function getAuditStats() {
  return request.get('/audit/stats')
}
/** 导出 CSV，返回 Blob */
export function exportAuditLogs(params = {}) {
  return download('/audit/logs/export', params)
}
