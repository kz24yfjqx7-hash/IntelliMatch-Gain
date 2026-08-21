/** AI 智能分析（契约 §2.11） */
import request from './request'

export function aiAnalyze({ scene, context = {}, question = '' }) {
  return request.post('/ai/analyze', { scene, context, question })
}
export function aiHistory(params = {}) {
  return request.get('/ai/history', { params })
}
