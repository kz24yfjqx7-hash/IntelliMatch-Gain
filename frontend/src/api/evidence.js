/** 区块链可信存证（契约 §2.6） */
import request from './request'

export function writeEvidence(data) {
  return request.post('/evidence', data)
}
export function listEvidence(params = {}) {
  return request.get('/evidence', { params })
}
export function getEvidence(id) {
  return request.get(`/evidence/${id}`)
}
export function verifyEvidence({ evidenceId, payload }) {
  const body = { evidenceId }
  if (payload !== undefined) body.payload = payload
  return request.post('/evidence/verify', body)
}
export function getChainStatus() {
  return request.get('/evidence/chain/status')
}
export function traceEvidence(traceId) {
  return request.get(`/evidence/trace/${traceId}`)
}
export function tamperEvidence({ evidenceId, newValue }) {
  return request.post('/evidence/demo/tamper', { evidenceId, newValue })
}
export function getCertificate(id) {
  return request.get(`/evidence/${id}/certificate`)
}
