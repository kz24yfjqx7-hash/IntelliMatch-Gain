/**
 * traceId 生成：格式 tr-YYYYMMDD-<8hex>，与契约 §1.2 一致。
 * 前端每个请求生成一个 traceId 放在 X-Trace-Id 头里，后端沿用，便于任务级全链路追踪。
 */

function randomHex(len) {
  const chars = '0123456789abcdef'
  let out = ''
  const cryptoObj = typeof globalThis !== 'undefined' ? globalThis.crypto : null
  if (cryptoObj && cryptoObj.getRandomValues) {
    const arr = new Uint8Array(len)
    cryptoObj.getRandomValues(arr)
    for (let i = 0; i < len; i++) out += chars[arr[i] & 15]
    return out
  }
  for (let i = 0; i < len; i++) out += chars[Math.floor(Math.random() * 16)]
  return out
}

/** 生成 YYYYMMDD（按本地时区） */
export function dateStamp(date = new Date()) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}${m}${d}`
}

export function genTraceId(date = new Date()) {
  return `tr-${dateStamp(date)}-${randomHex(8)}`
}

/** 校验是否为合法 traceId */
export function isTraceId(value) {
  return typeof value === 'string' && /^tr-\d{8}-[0-9a-f]{8}$/.test(value)
}

export { randomHex }
export default genTraceId
