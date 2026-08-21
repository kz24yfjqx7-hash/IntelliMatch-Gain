/** DID 身份管理（契约 §2.2） */
import request from './request'

export function registerDid(data) {
  return request.post('/did/register', data)
}
export function listDids(params = {}) {
  return request.get('/did', { params })
}
export function getDidDocument(did) {
  return request.get(`/did/${encodeURIComponent(did)}`)
}
export function changeDidStatus(did, { action, reason }) {
  return request.post(`/did/${encodeURIComponent(did)}/status`, { action, reason })
}
export function rotateDidKey(did) {
  return request.post(`/did/${encodeURIComponent(did)}/rotate-key`)
}
export function verifyDid({ did, message, signature }) {
  return request.post('/did/verify', { did, message, signature })
}
export function resolveDids(dids) {
  return request.post('/did/resolve', { dids })
}
