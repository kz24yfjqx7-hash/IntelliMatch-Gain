/** 风险评估（契约 §2.12） */
import request from './request'

export function assessRisk({ nodeId, features }) {
  return request.post('/risk/assess', { nodeId, features })
}
export function riskHistory(params = {}) {
  return request.get('/risk/history', { params })
}
