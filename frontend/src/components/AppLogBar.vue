<template>
  <div class="log-bar">
    <div class="log-header">
      <span class="log-icon">💻</span>
      <span class="log-title">实时日志</span>
      <span class="ws-light" :class="wsStatus" :title="`WebSocket：${wsStatusLabel}`"></span>
      <span class="ws-label">{{ wsStatusLabel }}</span>
    </div>
    <div class="log-content">
      <transition-group name="log" tag="div" class="log-list">
        <div
          v-for="log in logStore.recentLogs"
          :key="log.id"
          class="log-item"
          :class="getLogClass(log.level)"
        >
          <span class="log-time">{{ log.timestamp }}</span>
          <span class="log-source">[{{ log.source }}]</span>
          <span class="log-message">{{ log.content }}</span>
          <span v-if="log.traceId" class="log-trace">{{ log.traceId }}</span>
        </div>
      </transition-group>
      <div v-if="logStore.recentLogs.length === 0" class="log-empty">
        等待系统操作...
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useLogStore } from '@/stores/logs'
import { wsClient, WS_STATUS } from '@/api/ws'

const logStore = useLogStore()
const wsStatus = computed(() => wsClient.status.value)
const labels = {
  [WS_STATUS.IDLE]: '未连接',
  [WS_STATUS.CONNECTING]: '连接中',
  [WS_STATUS.CONNECTED]: '实时在线',
  [WS_STATUS.RECONNECTING]: '重连中',
  [WS_STATUS.CLOSED]: '已断开',
  [WS_STATUS.MOCK]: '离线模拟'
}
const wsStatusLabel = computed(() => labels[wsStatus.value] || wsStatus.value)

function getLogClass(level) {
  return {
    'log-info': level === 'INFO',
    'log-warn': level === 'WARN',
    'log-error': level === 'ERROR'
  }
}
</script>

<style scoped>
.log-bar { height: var(--logbar-height); background: #000; border-top: 1px solid var(--color-primary); display: flex; flex-shrink: 0; }
.log-header {
  width: 180px; background: rgba(10, 16, 24, 0.8); backdrop-filter: blur(10px);
  display: flex; align-items: center; justify-content: center; gap: 6px; border-right: 1px solid rgba(0, 180, 216, 0.15);
}
.log-icon { font-size: 20px; }
.log-title { color: var(--color-log); font-size: 14px; font-weight: 600; }
.ws-light { width: 8px; height: 8px; border-radius: 50%; background: #555; margin-left: 6px; }
.ws-light.connected, .ws-light.mock { background: var(--color-success); box-shadow: 0 0 6px var(--color-success); }
.ws-light.mock { background: var(--color-warning); box-shadow: 0 0 6px var(--color-warning); }
.ws-light.connecting, .ws-light.reconnecting { background: var(--color-warning); animation: pulse 1s infinite; }
.ws-light.closed { background: var(--color-danger); }
.ws-label { font-size: 10px; color: var(--color-text-secondary); }
.log-content { flex: 1; padding: 8px 16px; overflow: hidden; display: flex; flex-direction: column; justify-content: center; }
.log-list { display: flex; flex-direction: column; gap: 4px; }
.log-item { font-family: 'Consolas', 'Monaco', monospace; font-size: 12px; color: var(--color-log); display: flex; align-items: center; gap: 8px; animation: slideIn 0.3s ease-out; }
.log-time { color: #666; }
.log-source { color: var(--color-primary); }
.log-message { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.log-trace { color: #555; font-size: 11px; }
.log-warn .log-message { color: var(--color-warning); }
.log-error .log-message { color: var(--color-danger); }
.log-empty { color: #444; font-family: 'Consolas', 'Monaco', monospace; font-size: 12px; }
.log-enter-active, .log-leave-active { transition: all 0.3s ease; }
.log-enter-from { opacity: 0; transform: translateY(-10px); }
.log-leave-to { opacity: 0; transform: translateY(10px); }
</style>
