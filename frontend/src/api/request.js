/**
 * axios 实例：统一注入 Bearer token 与 X-Trace-Id，统一解包 {code,message,data,traceId}。
 * - code===0  → resolve data
 * - code 1002 → 清登录态并跳 /login
 * - code 1003/1004 → 越权/验签失败提示并 reject
 * - 其它非 0 → toast + reject
 * reject 的 error 对象上附带 error.code / error.message / error.traceId。
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { genTraceId } from '@/utils/traceId'

export const TOKEN_KEY = 'energy-tds-token'

const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/v1',
  timeout: 30000
})

/** 读取 token：优先从 pinia user store，其次 localStorage（避免循环依赖，用动态读取） */
function readToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

/** 清登录态并跳转登录页（惰性引用 router/store，避免循环 import） */
async function forceLogout() {
  try {
    const { useUserStore } = await import('@/stores/user')
    useUserStore().clearSession()
  } catch {
    try { localStorage.removeItem(TOKEN_KEY) } catch { /* 忽略 */ }
  }
  const { default: router } = await import('@/router')
  if (router.currentRoute.value.path !== '/login') {
    router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
  }
}

/** 无需登录态的接口（其余接口在未登录时不发请求，避免退出登录瞬间的定时刷新打出 401） */
const PUBLIC_PATHS = [/\/auth\/login$/]

request.interceptors.request.use(config => {
  const token = readToken()
  config.headers = config.headers || {}
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`
  }
  if (!token && !config.headers.Authorization && !PUBLIC_PATHS.some(r => r.test(config.url || ''))) {
    return Promise.reject(buildError('未登录', 1002, config.headers['X-Trace-Id'] || null, { silent: true }))
  }
  if (!config.headers['X-Trace-Id']) {
    config.headers['X-Trace-Id'] = genTraceId()
  }
  // 记录 traceId 以便调用方在 error / 返回值上取到
  config.traceId = config.headers['X-Trace-Id']
  return config
})

/** 构造带 code / traceId 的业务错误 */
function buildError(message, code, traceId, extra = {}) {
  const err = new Error(message || '请求失败')
  err.code = code
  err.traceId = traceId
  Object.assign(err, extra)
  return err
}

/** 按 code 做统一提示 */
function handleBizError(code, message, traceId) {
  if (code === 1002) {
    ElMessage.error(message || '登录已失效，请重新登录')
    forceLogout()
  } else if (code === 1003) {
    ElMessage.error(`越权操作：${message || '无权限'}`)
  } else if (code === 1004) {
    ElMessage.error(`DID 验签失败：${message || '签名无效'}`)
  } else {
    ElMessage.error(message || `请求失败(${code})`)
  }
}

request.interceptors.response.use(
  response => {
    const traceId = response.config?.traceId
    // 文件流（Blob）不解包
    if (response.config?.responseType === 'blob') {
      return response.data
    }
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) {
        return body.data
      }
      handleBizError(body.code, body.message, body.traceId || traceId)
      return Promise.reject(buildError(body.message, body.code, body.traceId || traceId, { data: body.data }))
    }
    // 非标准包装直接返回
    return body
  },
  error => {
    // 请求拦截器里本地拒绝的（未登录），不提示、直接透传
    if (error?.silent) return Promise.reject(error)
    const traceId = error.config?.traceId
    const body = error.response?.data
    if (body && typeof body === 'object' && 'code' in body) {
      handleBizError(body.code, body.message, body.traceId || traceId)
      return Promise.reject(buildError(body.message, body.code, body.traceId || traceId, {
        status: error.response?.status,
        data: body.data
      }))
    }
    const status = error.response?.status
    let message = error.message || '网络异常'
    if (error.code === 'ECONNABORTED') message = '请求超时'
    else if (status) message = `HTTP ${status}`
    else if (!error.response) message = '网络异常，后端不可达'
    ElMessage.error(message)
    return Promise.reject(buildError(message, status ? 5000 : -1, traceId, { status }))
  }
)

/**
 * 下载文件流（CSV 等），返回 Blob
 * @param {string} url
 * @param {object} params
 */
export function download(url, params = {}) {
  return request.get(url, { params, responseType: 'blob' })
}

/** 触发浏览器保存 Blob（页面可直接用） */
export function saveBlob(blob, filename = 'export.csv') {
  const href = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(href)
}

export default request
