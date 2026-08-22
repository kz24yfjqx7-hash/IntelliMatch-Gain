/**
 * WebSocket 客户端（契约 §2.13）。
 * - connect(token)：连接 `${VITE_WS_BASE||'/ws'}?token=...`；30s 心跳 ping；指数退避重连 1s→30s
 * - close code 4001（鉴权失败）不重连：清会话 + 跳 /login，与 request.js 收到 code 1002 的处理一致
 * - on(type, handler) → 返回取消函数；off(type, handler)；send(obj)
 * - VITE_USE_MOCK==='true' 时不建真实连接，直接订阅 mocks/wsMock.js 的事件总线
 * - status 为 Vue ref，可直接在组件中显示连接状态灯
 */
import { ref } from 'vue'

export const WS_TYPES = {
  NODE_STATUS: 'node_status',
  FL_PROGRESS: 'fl_progress',
  DISPATCH_PROGRESS: 'dispatch_progress',
  AUDIT_ALERT: 'audit_alert',
  LOG: 'log',
  EVIDENCE_WRITTEN: 'evidence_written',
  PONG: 'pong'
}

export const WS_STATUS = {
  IDLE: 'idle',
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  RECONNECTING: 'reconnecting',
  CLOSED: 'closed',
  MOCK: 'mock'
}

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'
const HEARTBEAT_MS = 30000
const BACKOFF_MIN = 1000
const BACKOFF_MAX = 30000
/** 契约 §2.13：鉴权失败服务端立即以 4001 关闭。重连再多次也没用，token 本身失效了 */
export const WS_CLOSE_AUTH_FAILED = 4001

/** 鉴权失败：清登录态并跳登录页。惰性 import 避免与 stores/user、router 循环依赖 */
async function forceLogout() {
  try {
    const { useUserStore } = await import('@/stores/user')
    useUserStore().clearSession()
  } catch {
    try { localStorage.removeItem('energy-tds-token') } catch { /* 忽略 */ }
  }
  try {
    const { default: router } = await import('@/router')
    if (router.currentRoute.value.path !== '/login') {
      router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
    }
  } catch { /* 路由不可用（例如单测环境）时忽略 */ }
}

export function createWsClient() {
  const handlers = new Map() // type -> Set<fn>
  const anyHandlers = new Set() // 订阅全部消息（type='*'）
  const status = ref(WS_STATUS.IDLE)
  const lastMessageAt = ref(null)
  let socket = null
  let token = ''
  let heartbeatTimer = null
  let reconnectTimer = null
  let backoff = BACKOFF_MIN
  let manualClose = false
  let mockUnsub = null

  function buildUrl() {
    const base = import.meta.env.VITE_WS_BASE || '/ws'
    if (/^wss?:\/\//.test(base)) return `${base}?token=${encodeURIComponent(token)}`
    const proto = typeof location !== 'undefined' && location.protocol === 'https:' ? 'wss' : 'ws'
    const host = typeof location !== 'undefined' ? location.host : 'localhost'
    return `${proto}://${host}${base}?token=${encodeURIComponent(token)}`
  }

  function dispatch(msg) {
    lastMessageAt.value = Date.now()
    const set = handlers.get(msg.type)
    if (set) for (const fn of Array.from(set)) {
      try { fn(msg.payload, msg) } catch (e) { console.error('[ws] handler error', e) }
    }
    for (const fn of Array.from(anyHandlers)) {
      try { fn(msg) } catch (e) { console.error('[ws] handler error', e) }
    }
  }

  function startHeartbeat() {
    stopHeartbeat()
    heartbeatTimer = setInterval(() => send({ type: 'ping' }), HEARTBEAT_MS)
  }
  function stopHeartbeat() {
    if (heartbeatTimer) clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }

  function scheduleReconnect() {
    if (manualClose) return
    status.value = WS_STATUS.RECONNECTING
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(() => {
      backoff = Math.min(backoff * 2, BACKOFF_MAX)
      openSocket()
    }, backoff)
  }

  function openSocket() {
    if (typeof WebSocket === 'undefined') return
    status.value = WS_STATUS.CONNECTING
    try {
      socket = new WebSocket(buildUrl())
    } catch (e) {
      scheduleReconnect()
      return
    }
    socket.onopen = () => {
      status.value = WS_STATUS.CONNECTED
      backoff = BACKOFF_MIN
      startHeartbeat()
    }
    socket.onmessage = evt => {
      try {
        dispatch(JSON.parse(evt.data))
      } catch {
        // 非 JSON 消息忽略
      }
    }
    socket.onerror = () => { /* 交给 onclose 处理 */ }
    socket.onclose = evt => {
      stopHeartbeat()
      socket = null
      // 4001 = 服务端明确告知鉴权失败（token 过期/被登出/伪造）。
      // 这类关闭退避重连只会用同一个坏 token 反复敲门，直接清会话跳登录。
      if (evt && evt.code === WS_CLOSE_AUTH_FAILED) {
        manualClose = true
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
        backoff = BACKOFF_MIN
        status.value = WS_STATUS.CLOSED
        console.warn('[ws] 鉴权失败（4001），清理会话并跳转登录')
        forceLogout()
        return
      }
      if (manualClose) {
        status.value = WS_STATUS.CLOSED
      } else {
        scheduleReconnect()
      }
    }
  }

  async function connectMock() {
    const { wsMock } = await import('@/mocks/wsMock')
    // 保证 mock 数据库已初始化（注入节点提供者）
    await import('@/mocks/db')
    if (mockUnsub) mockUnsub()
    mockUnsub = wsMock.subscribe(dispatch)
    wsMock.start(5000)
    status.value = WS_STATUS.MOCK
  }

  /** 建立连接（幂等；重复调用仅更新 token） */
  function connect(nextToken) {
    token = nextToken || token
    manualClose = false
    if (USE_MOCK) {
      if (status.value !== WS_STATUS.MOCK) connectMock()
      return
    }
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return
    openSocket()
  }

  function disconnect() {
    manualClose = true
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = null
    stopHeartbeat()
    if (mockUnsub) { mockUnsub(); mockUnsub = null }
    if (socket) {
      try { socket.close() } catch { /* 忽略 */ }
      socket = null
    }
    status.value = WS_STATUS.CLOSED
  }

  /** 订阅某类型消息，handler(payload, rawMessage)；type='*' 订阅全部；返回取消函数 */
  function on(type, handler) {
    if (type === '*') {
      anyHandlers.add(handler)
      return () => anyHandlers.delete(handler)
    }
    if (!handlers.has(type)) handlers.set(type, new Set())
    handlers.get(type).add(handler)
    return () => off(type, handler)
  }

  function off(type, handler) {
    if (type === '*') { anyHandlers.delete(handler); return }
    const set = handlers.get(type)
    if (set) set.delete(handler)
  }

  function send(obj) {
    if (USE_MOCK) {
      if (obj && obj.type === 'ping') dispatch({ type: 'pong', ts: new Date().toISOString(), payload: {} })
      return true
    }
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(typeof obj === 'string' ? obj : JSON.stringify(obj))
      return true
    }
    return false
  }

  return { connect, disconnect, on, off, send, status, lastMessageAt, isMock: USE_MOCK }
}

export const wsClient = createWsClient()
export default wsClient
