/**
 * WebSocket 客户端（契约 §2.13）。
 * - connect(token)：连接 `${VITE_WS_BASE||'/ws'}?token=...`；30s 心跳 ping；指数退避重连 1s→30s
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
    socket.onclose = () => {
      stopHeartbeat()
      socket = null
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
