/**
 * 日志流 store。
 * - 保留原 addLog/addTaskLog/getLogsByXxx/clearLogs 签名（旧页面直接依赖）
 * - 新增 attachWs()：把 WebSocket 的 log / audit_alert / evidence_written 消息写入日志流
 * - 新增 alerts 列表、unackedAlertCount、ackAlert()、fetchAlerts()
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { wsClient, WS_TYPES } from '@/api/ws'
import * as auditApi from '@/api/audit'

const MAX_LOGS = 500

export const useLogStore = defineStore('logs', () => {
  const logs = ref([])
  const alerts = ref([])
  const wsAttached = ref(false)
  let detachFns = []

  const recentLogs = computed(() => logs.value.slice(-3).reverse())
  const unackedAlertCount = computed(() => alerts.value.filter(a => a.status !== 'acked').length)
  const openAlerts = computed(() => alerts.value.filter(a => a.status !== 'acked'))

  function addLog(content, level = 'INFO', source = 'SYSTEM', options = {}) {
    const log = {
      id: options.id || `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      timestamp: new Date().toLocaleString('zh-CN'),
      level,
      source,
      content,
      result: options.result || (level === 'ERROR' ? '失败' : '成功'),
      taskId: options.taskId || null,
      taskStatus: options.taskStatus || null,
      taskStatusLabel: options.taskStatusLabel || null,
      payload: options.payload || null,
      traceId: options.traceId || null
    }
    logs.value.push(log)
    if (logs.value.length > MAX_LOGS) logs.value.splice(0, logs.value.length - MAX_LOGS)
    return log
  }

  function addTaskLog(task, content, level = 'INFO', source = 'SYSTEM', options = {}) {
    addLog(content, level, source, {
      taskId: task?.id || options.taskId,
      taskStatus: task?.status || options.taskStatus,
      taskStatusLabel: options.taskStatusLabel,
      ...options
    })
  }

  function getLogsByLevel(level) {
    return logs.value.filter(log => log.level === level)
  }
  function getLogsBySource(source) {
    return logs.value.filter(log => log.source === source)
  }
  function getLogsByTaskId(taskId) {
    return logs.value.filter(log => log.taskId === taskId)
  }
  function clearLogs() {
    logs.value = []
  }

  /** WS level(info/warn/error) → 本地 INFO/WARN/ERROR */
  function normalizeLevel(level) {
    const l = String(level || 'info').toUpperCase()
    return ['INFO', 'WARN', 'ERROR'].includes(l) ? l : 'INFO'
  }

  /** 推入一条告警（去重） */
  function pushAlert(alert) {
    if (!alert) return
    const idx = alerts.value.findIndex(a => a.id === alert.id)
    if (idx >= 0) alerts.value.splice(idx, 1, { ...alerts.value[idx], ...alert })
    else alerts.value.unshift(alert)
  }

  /** 订阅 WS 消息写入日志流（幂等） */
  function attachWs() {
    if (wsAttached.value) return
    wsAttached.value = true
    detachFns.push(wsClient.on(WS_TYPES.LOG, (p, msg) => {
      addLog(p.content, normalizeLevel(p.level), (p.module || 'WS').toUpperCase(), { traceId: p.traceId || msg.traceId })
    }))
    detachFns.push(wsClient.on(WS_TYPES.AUDIT_ALERT, (p, msg) => {
      addLog(`[${p.ruleCode}] ${p.message}`, p.riskLevel === 'critical' || p.riskLevel === 'high' ? 'ERROR' : 'WARN', 'AUDIT', { traceId: msg.traceId })
      pushAlert({
        // WS 契约 §2.13 只带字符串 alertId（al-000045），确认接口要数字主键，
        // 两个都存下来，ackAlert() 再按需换算
        id: p.alertId,
        alertId: p.alertId,
        ruleCode: p.ruleCode,
        riskLevel: p.riskLevel,
        message: p.message,
        actorDid: p.actorDid,
        status: 'open',
        at: msg.ts,
        traceId: msg.traceId
      })
    }))
    detachFns.push(wsClient.on(WS_TYPES.EVIDENCE_WRITTEN, (p, msg) => {
      addLog(`存证上链 ${p.evidenceId}（${p.category}，高度 ${p.blockHeight}）`, 'INFO', 'CHAIN', { traceId: msg.traceId })
    }))
  }

  function detachWs() {
    detachFns.forEach(fn => fn())
    detachFns = []
    wsAttached.value = false
  }

  /** 从后端拉取告警列表（默认 open） */
  async function fetchAlerts(params = {}) {
    const data = await auditApi.listAlerts(params)
    const items = data?.items || []
    if (params.status === undefined || params.status === 'open') {
      // 合并：保留本地已存在的已确认记录
      const acked = alerts.value.filter(a => a.status === 'acked')
      alerts.value = [...items, ...acked.filter(a => !items.find(i => i.id === a.id))]
    } else {
      items.forEach(pushAlert)
    }
    return items
  }

  async function ackAlert(id) {
    // 列表项的 id 是数字主键；WS 推送的告警 id 是字符串 alertId，需要先换成数字主键
    const item = alerts.value.find(a => a.id === id || a.alertId === id)
    let key = item?.id ?? id
    if (!Number.isInteger(Number(key))) {
      const wanted = item?.alertId ?? id
      try {
        const res = await auditApi.listAlerts({ status: 'open', size: 100 })
        const hit = (res?.items || []).find(a => a.alertId === wanted)
        if (hit) key = hit.id
      } catch { /* 换算失败则按原值请求，由拦截器提示 */ }
    }
    await auditApi.ackAlert(key)
    const a = alerts.value.find(x => x.id === id)
    if (a) a.status = 'acked'
    addLog(`告警 #${id} 已确认`, 'INFO', 'AUDIT')
  }

  return {
    logs, recentLogs, alerts, openAlerts, unackedAlertCount, wsAttached,
    addLog, addTaskLog, getLogsByLevel, getLogsBySource, getLogsByTaskId, clearLogs,
    attachWs, detachWs, fetchAlerts, ackAlert, pushAlert
  }
})
