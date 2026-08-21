/**
 * WebSocket 模拟通道（mock 模式下由 api/ws.js 直接订阅）。
 * - 与真实 WebSocket 客户端解耦：这里只是一个事件总线，消息格式与契约 §2.13 完全一致：
 *   {type, ts, traceId, payload}
 * - 每 5s 推送一次 node_status（指标小幅随机游走），其它事件由 handlers 触发 emit()。
 * - 不依赖浏览器专有 API，Node 下（vitest / selfcheck）同样可用。
 */
import { toIso8 } from '../utils/format.js'

const listeners = new Set()
let nodeTimer = null
let nodeProvider = null // () => nodes 数组，由 db 注入
let started = false

/** 订阅全部消息，返回取消函数 */
export function subscribe(handler) {
  listeners.add(handler)
  return () => listeners.delete(handler)
}

/** 发布一条消息（handlers 调用） */
export function emit(type, payload = {}, traceId = null) {
  const msg = { type, ts: toIso8(new Date()), traceId, payload }
  // 异步派发，避免在 handler 同步流程中抛错
  for (const fn of Array.from(listeners)) {
    try {
      Promise.resolve().then(() => fn(msg))
    } catch (e) {
      // 忽略单个监听者错误
    }
  }
  return msg
}

/** 便捷方法：日志栏消息 */
export function emitLog(level, module, content, traceId = null) {
  return emit('log', { level, module, content, traceId }, traceId)
}

/** 注入节点数据提供者（db.js 调用），用于定时 node_status 推送 */
export function setNodeProvider(fn) {
  nodeProvider = fn
}

/** 随机游走一个指标 */
function walk(value, step, min, max) {
  const next = value + (Math.random() - 0.5) * 2 * step
  return Number(Math.min(max, Math.max(min, next)).toFixed(1))
}

/** 对节点指标做一次随机游走并推送 node_status（直接修改 db 里的节点对象，使 REST 与 WS 一致） */
export function tickNodeStatus() {
  if (!nodeProvider) return
  const nodes = nodeProvider() || []
  const now = toIso8(new Date())
  for (const node of nodes) {
    if (node.status === 'offline') continue
    const m = node.metrics
    m.pvOutput = walk(m.pvOutput, 1.5, 0, 80)
    m.storageOutput = walk(m.storageOutput, 1.2, -30, 30)
    m.load = walk(m.load, 3, 40, 200)
    m.soc = walk(m.soc, 0.6, 20, 95)
    node.lastSeenAt = now
    emit('node_status', { nodeId: node.id, status: node.status, metrics: { ...m } })
  }
}

/** 启动定时推送（幂等） */
export function start(intervalMs = 5000) {
  if (started) return
  started = true
  nodeTimer = setInterval(tickNodeStatus, intervalMs)
  // Node 环境下不要因为定时器阻止进程退出
  if (nodeTimer && typeof nodeTimer.unref === 'function') nodeTimer.unref()
}

export function stop() {
  if (nodeTimer) clearInterval(nodeTimer)
  nodeTimer = null
  started = false
}

export function isStarted() {
  return started
}

export const wsMock = { subscribe, emit, emitLog, start, stop, isStarted, setNodeProvider, tickNodeStatus }
export default wsMock
