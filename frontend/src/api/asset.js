/** 能源数据资产（契约 §2.4） */
import request from './request'

export function registerAsset(data) {
  return request.post('/assets', data)
}
export function listAssets(params = {}) {
  return request.get('/assets', { params })
}
export function getAsset(id) {
  return request.get(`/assets/${id}`)
}
export function getAssetLineage(id) {
  return request.get(`/assets/${id}/lineage`)
}
export function classifyAssets({ records }) {
  return request.post('/assets/classify', { records })
}
export function getAssetStats() {
  return request.get('/assets/stats')
}
