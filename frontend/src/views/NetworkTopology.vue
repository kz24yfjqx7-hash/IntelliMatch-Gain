<template>
  <div class="topology-container">
    <!-- 可信数据空间六环节流程图（frontend-pages 提供组件） -->
    <TrustFlowBanner />

    <div class="page-header">
      <h2 class="page-title">🌐 全网设备状态图</h2>
      <p class="page-desc">实时监控全网边缘节点状态与 DID 身份，点击节点查看详情（数据来源 GET /nodes + WebSocket node_status）</p>
    </div>

    <div class="topology-content">
      <div class="main-area">
        <div class="topology-wrapper">
          <div ref="chartRef" class="chart-container"></div>

          <div class="node-cards">
            <div
              v-for="(node, index) in nodes"
              :key="node.id"
              class="node-card"
              :class="[node.status, { 'hover': hoveredNode === node.id }]"
              :style="getNodeStyle(index)"
              @click="selectNode(node)"
              @mouseenter="hoveredNode = node.id"
              @mouseleave="hoveredNode = null"
            >
              <div class="card-header">
                <div class="node-status-indicator" :class="node.status"></div>
                <span class="node-id">{{ node.id }}</span>
                <span class="node-badge" :class="node.status">{{ statusLabel(node.status) }}</span>
              </div>
              <div class="card-did">
                <span class="did-short" :title="node.did">{{ didShort(node.did) }}</span>
                <span class="did-badge" :class="node.didStatus || 'unknown'">{{ didStatusLabel(node.didStatus) }}</span>
              </div>
              <div class="card-body">
                <div class="data-row">
                  <span class="data-icon">⚡</span>
                  <span class="data-label">负荷</span>
                  <span class="data-value">{{ fmt(node.metrics.load) }} kW</span>
                </div>
                <div class="data-row">
                  <span class="data-icon">🔋</span>
                  <span class="data-label">SOC</span>
                  <span class="data-value" :class="{ low: node.metrics.soc < 50 }">{{ fmt(node.metrics.soc) }}%</span>
                </div>
              </div>
              <div class="card-footer">
                <span class="view-detail">详情 →</span>
              </div>
            </div>
          </div>

          <div class="center-node">
            <div class="center-icon">☁️</div>
            <div class="center-label">云端调度中心</div>
            <div class="center-sub">{{ wsLabel }}</div>
          </div>
        </div>

        <div class="stats-overview">
          <div class="stat-card">
            <div class="stat-icon">📊</div>
            <div class="stat-info">
              <div class="stat-value">{{ onlineCount }}/{{ nodes.length }}</div>
              <div class="stat-label">在线节点</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon">⚡</div>
            <div class="stat-info">
              <div class="stat-value">{{ totalLoad }} kW</div>
              <div class="stat-label">总负荷</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon">☀️</div>
            <div class="stat-info">
              <div class="stat-value">{{ totalPV }} kW</div>
              <div class="stat-label">光伏出力</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon">🪪</div>
            <div class="stat-info">
              <div class="stat-value">{{ activeDidCount }}</div>
              <div class="stat-label">DID 活跃</div>
            </div>
          </div>
          <div class="stat-card warning">
            <div class="stat-icon">⚠️</div>
            <div class="stat-info">
              <div class="stat-value">{{ warningCount }}</div>
              <div class="stat-label">告警节点</div>
            </div>
          </div>
        </div>
      </div>

      <div class="side-panel">
        <div class="panel-section">
          <div class="section-title">
            <span class="title-icon">📈</span>
            <span>实时负荷分布</span>
            <span class="live-tag" :class="{ on: lastPushAt }">{{ lastPushAt ? `更新 ${lastPushAt}` : '等待推送' }}</span>
          </div>
          <div ref="loadChartRef" class="mini-chart"></div>
        </div>

        <div class="panel-section">
          <div class="section-title">
            <span class="title-icon">🔋</span>
            <span>储能状态</span>
          </div>
          <div ref="storageChartRef" class="mini-chart"></div>
        </div>

        <div class="panel-section">
          <div class="section-title">
            <span class="title-icon">📋</span>
            <span>节点列表</span>
          </div>
          <div class="node-list">
            <div
              v-for="node in nodes"
              :key="node.id"
              class="node-item"
              :class="node.status"
              @click="selectNode(node)"
            >
              <div class="node-icon">{{ node.status === 'online' ? '🟢' : node.status === 'warning' ? '🟡' : '🔴' }}</div>
              <div class="node-info">
                <div class="node-name">{{ node.name }} <span class="did-badge mini" :class="node.didStatus || 'unknown'">{{ didStatusLabel(node.didStatus) }}</span></div>
                <div class="node-meta">负荷: {{ fmt(node.metrics.load) }}kW | SOC: {{ fmt(node.metrics.soc) }}%</div>
              </div>
              <div class="node-arrow">→</div>
            </div>
          </div>
        </div>
      </div>

      <el-drawer
        v-model="drawerVisible"
        :title="selectedNode?.name"
        direction="rtl"
        size="460px"
      >
        <div v-if="selectedNode" class="node-detail">
          <div class="detail-header">
            <div class="node-avatar" :class="selectedNode.status">
              <span class="avatar-icon">🏭</span>
            </div>
            <div class="node-basic">
              <div class="node-id">{{ selectedNode.id }}</div>
              <div class="node-status" :class="selectedNode.status">
                {{ selectedNode.status === 'online' ? '● 在线运行' : selectedNode.status === 'warning' ? '● 告警状态' : '● 离线' }}
              </div>
            </div>
          </div>

          <div class="detail-section">
            <h4 class="section-title">🪪 DID 身份</h4>
            <div class="info-list">
              <div class="info-item">
                <span class="info-label">DID</span>
                <span class="info-value mono" :title="selectedNode.did">{{ selectedNode.did || '--' }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">身份状态</span>
                <span class="did-badge" :class="selectedNode.didStatus || 'unknown'">{{ didStatusLabel(selectedNode.didStatus) }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">最后通信</span>
                <span class="info-value">{{ fmtDateTime(selectedNode.lastSeenAt) }}</span>
              </div>
            </div>
          </div>

          <div class="detail-section">
            <h4 class="section-title">📊 实时数据（metrics）</h4>
            <div class="data-grid">
              <div class="data-card">
                <div class="data-icon">⚡</div>
                <div class="data-content">
                  <div class="data-value">{{ fmt(selectedNode.metrics.load) }} kW</div>
                  <div class="data-label">当前负荷</div>
                </div>
              </div>
              <div class="data-card">
                <div class="data-icon">☀️</div>
                <div class="data-content">
                  <div class="data-value">{{ fmt(selectedNode.metrics.pvOutput) }} kW</div>
                  <div class="data-label">光伏出力</div>
                </div>
              </div>
              <div class="data-card">
                <div class="data-icon">🔋</div>
                <div class="data-content">
                  <div class="data-value">{{ fmt(selectedNode.metrics.storageOutput) }} kW</div>
                  <div class="data-label">储能功率</div>
                </div>
              </div>
              <div class="data-card">
                <div class="data-icon">📈</div>
                <div class="data-content">
                  <div class="data-value">{{ fmt(selectedNode.metrics.soc) }}%</div>
                  <div class="data-label">储能SOC</div>
                </div>
              </div>
            </div>
          </div>

          <div class="detail-section">
            <h4 class="section-title">⚙️ 设备信息</h4>
            <div class="info-list">
              <div class="info-item">
                <span class="info-label">设备型号</span>
                <span class="info-value">{{ selectedNode.model || '--' }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">已登记资产</span>
                <span class="info-value">{{ nodeDetail?.assetsCount ?? '--' }} 条</span>
              </div>
              <div class="info-item">
                <span class="info-label">所在位置</span>
                <span class="info-value">{{ nodeDetail?.location || '--' }}</span>
              </div>
            </div>
          </div>

          <div class="detail-section">
            <h4 class="section-title">📉 功率趋势（最近 12 小时）</h4>
            <div ref="detailChartRef" class="detail-chart"></div>
          </div>

          <div class="action-section">
            <el-button type="primary" size="large" @click="goDispatch">
              🚀 前往云端聚合与调度
            </el-button>
          </div>
        </div>
      </el-drawer>
    </div>
  </div>
</template>

<script setup>
/**
 * 首页驾驶舱：节点数据来自 perspectiveStore（GET /nodes），WS node_status 实时刷新指标；
 * 节点卡片显示 DID 短标识与 didStatus 徽章；详情抽屉展示 did / lastSeenAt / metrics 与历史指标曲线。
 */
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import TrustFlowBanner from '@/components/TrustFlowBanner.vue'
import { useLogStore } from '@/stores/logs'
import { usePerspectiveStore } from '@/stores/perspective'
import { getNode, getNodeMetrics } from '@/api/node'
import { wsClient, WS_TYPES, WS_STATUS } from '@/api/ws'
import { fmtDateTime, fmtTime, shortDid } from '@/utils/format'

const router = useRouter()
const logStore = useLogStore()
const perspectiveStore = usePerspectiveStore()

const chartRef = ref(null)
const loadChartRef = ref(null)
const storageChartRef = ref(null)
const detailChartRef = ref(null)
const drawerVisible = ref(false)
const selectedNodeId = ref(null)
const nodeDetail = ref(null)
const hoveredNode = ref(null)
const lastPushAt = ref('')

let chartInstance = null
let loadChartInstance = null
let storageChartInstance = null
let detailChartInstance = null
let offWs = null
let resizeHandler = null

/** 节点列表直接取 store（AppLayout 已 fetchNodes 并订阅 node_status） */
const nodes = computed(() => perspectiveStore.nodes)
const selectedNode = computed(() => nodes.value.find(n => n.id === selectedNodeId.value) || null)

const nodePositions = [
  { top: '1%', left: '3%' },
  { top: '1%', right: '15%' },
  { top: '53%', left: '15%' },
  { top: '53%', right: '3%' }
]
function getNodeStyle(index) {
  return nodePositions[index % nodePositions.length]
}

const onlineCount = computed(() => nodes.value.filter(n => n.status === 'online').length)
const warningCount = computed(() => nodes.value.filter(n => n.status === 'warning').length)
const activeDidCount = computed(() => nodes.value.filter(n => n.didStatus === 'active').length)
const totalLoad = computed(() => nodes.value.reduce((s, n) => s + (n.metrics?.load || 0), 0).toFixed(0))
const totalPV = computed(() => nodes.value.reduce((s, n) => s + (n.metrics?.pvOutput || 0), 0).toFixed(1))
const wsLabel = computed(() => {
  const s = wsClient.status.value
  if (s === WS_STATUS.CONNECTED) return '实时在线'
  if (s === WS_STATUS.MOCK) return '离线模拟推送'
  if (s === WS_STATUS.RECONNECTING || s === WS_STATUS.CONNECTING) return '连接中…'
  return '未连接'
})

const DID_LABELS = { active: 'DID 活跃', frozen: 'DID 冻结', revoked: 'DID 注销' }
function didStatusLabel(s) { return DID_LABELS[s] || 'DID 未知' }
function didShort(did) { return did ? shortDid(did) : '未绑定 DID' }
function statusLabel(s) { return s === 'online' ? '在线' : s === 'warning' ? '告警' : '离线' }
function fmt(v, d = 1) { const n = Number(v); return Number.isNaN(n) ? '--' : (Number.isInteger(n) ? String(n) : n.toFixed(d)) }

function getResponsiveFontSize(baseSize) {
  const vw = window.innerWidth / 100
  return Math.round(Math.max(8, Math.min(baseSize, baseSize * vw / 14)))
}

function initMainChart() {
  if (!chartRef.value) return
  chartInstance = chartInstance || echarts.init(chartRef.value)
  const centerPoint = { name: '云端调度中心', x: 300, y: 200, symbolSize: 0, itemStyle: { color: 'transparent' }, label: { show: false } }
  const coords = [{ x: 100, y: 80 }, { x: 430, y: 80 }, { x: 165, y: 320 }, { x: 490, y: 320 }]
  const nodePoints = nodes.value.map((n, i) => ({ name: n.id, ...coords[i % coords.length], symbolSize: 0, itemStyle: { color: 'transparent' }, label: { show: false } }))
  const links = nodePoints.map(node => ({
    source: '云端调度中心',
    target: node.name,
    lineStyle: { color: '#00B4D8', width: 2, type: 'dashed', curveness: 0.2, opacity: 0.6 }
  }))
  chartInstance.setOption({
    backgroundColor: 'transparent',
    series: [{ type: 'graph', layout: 'none', roam: false, data: [centerPoint, ...nodePoints], links, edgeSymbol: ['none', 'arrow'], edgeSymbolSize: [0, 8], animationDuration: 1500 }]
  })
}

function loadChartOption() {
  const fs = getResponsiveFontSize(10)
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(13, 27, 42, 0.95)', borderColor: '#00B4D8', textStyle: { color: '#fff', fontSize: fs } },
    grid: { left: '3%', right: '3%', bottom: '3%', top: '10%', containLabel: true },
    xAxis: { type: 'category', data: nodes.value.map(n => n.id.replace('Node-', '')), axisLine: { lineStyle: { color: '#243447' } }, axisLabel: { color: '#8892B0', fontSize: fs } },
    yAxis: { type: 'value', axisLine: { show: false }, axisLabel: { color: '#8892B0', fontSize: fs }, splitLine: { lineStyle: { color: '#243447', type: 'dashed' } } },
    series: [{
      type: 'bar',
      data: nodes.value.map(n => ({
        value: Number(n.metrics.load.toFixed(1)),
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: n.status === 'online' ? '#2ECC71' : '#F39C12' },
            { offset: 1, color: n.status === 'online' ? '#1a7f37' : '#d68910' }
          ]),
          borderRadius: [4, 4, 0, 0]
        }
      })),
      barWidth: '50%',
      label: { show: true, position: 'top', color: '#8892B0', fontSize: fs, formatter: '{c}' }
    }]
  }
}

function storageChartOption() {
  const fs = getResponsiveFontSize(10)
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', backgroundColor: 'rgba(13, 27, 42, 0.95)', borderColor: '#00B4D8', textStyle: { color: '#fff', fontSize: fs } },
    series: [{
      type: 'pie',
      radius: ['50%', '70%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 4, borderColor: '#0D1B2A', borderWidth: 2 },
      label: { show: true, position: 'outside', color: '#8892B0', fontSize: fs, formatter: '{b}: {d}%' },
      labelLine: { lineStyle: { color: '#243447' } },
      data: nodes.value.map(n => ({
        value: Number(n.metrics.soc.toFixed(1)),
        name: n.id.replace('Node-', ''),
        itemStyle: { color: n.metrics.soc > 60 ? '#2ECC71' : n.metrics.soc > 30 ? '#F39C12' : '#E63946' }
      }))
    }]
  }
}

function initSideCharts() {
  if (loadChartRef.value) {
    loadChartInstance = loadChartInstance || echarts.init(loadChartRef.value)
    loadChartInstance.setOption(loadChartOption())
  }
  if (storageChartRef.value) {
    storageChartInstance = storageChartInstance || echarts.init(storageChartRef.value)
    storageChartInstance.setOption(storageChartOption())
  }
}

/** WS 推送后只更新数据，不重建实例 */
function refreshCharts() {
  loadChartInstance?.setOption({ series: [{ data: loadChartOption().series[0].data }] })
  storageChartInstance?.setOption({ series: [{ data: storageChartOption().series[0].data }] })
}

/** 详情抽屉：拉取节点历史指标（GET /nodes/{id}/metrics）画最近 12 小时曲线 */
async function initDetailChart() {
  if (!detailChartRef.value || !selectedNode.value) return
  detailChartInstance?.dispose()
  detailChartInstance = echarts.init(detailChartRef.value)
  const fs = getResponsiveFontSize(10)
  let items = []
  try {
    const data = await getNodeMetrics(selectedNode.value.id, { interval: 'hour' })
    items = (data?.items || []).slice(-12)
  } catch {
    items = []
  }
  if (!items.length) {
    // 无历史数据时用当前指标铺平一条线
    const m = selectedNode.value.metrics
    items = Array.from({ length: 6 }, (_, i) => ({ ts: `T${i}`, load: m.load, pvOutput: m.pvOutput }))
  }
  const hours = items.map(i => (i.ts?.length > 12 ? fmtTime(i.ts, 'HH:mm') : i.ts))
  detailChartInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(13, 27, 42, 0.95)', borderColor: '#00B4D8', textStyle: { color: '#fff', fontSize: fs } },
    legend: { data: ['负荷', '光伏'], textStyle: { color: '#8892B0', fontSize: fs }, top: 0 },
    grid: { left: '3%', right: '3%', bottom: '3%', top: '15%', containLabel: true },
    xAxis: { type: 'category', data: hours, axisLine: { lineStyle: { color: '#243447' } }, axisLabel: { color: '#8892B0', fontSize: fs } },
    yAxis: { type: 'value', axisLine: { show: false }, axisLabel: { color: '#8892B0', fontSize: fs }, splitLine: { lineStyle: { color: '#243447', type: 'dashed' } } },
    series: [
      { name: '负荷', type: 'line', smooth: true, data: items.map(i => i.load), lineStyle: { color: '#00B4D8', width: 2 }, itemStyle: { color: '#00B4D8' },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(0, 180, 216, 0.3)' }, { offset: 1, color: 'rgba(0, 180, 216, 0.05)' }]) } },
      { name: '光伏', type: 'line', smooth: true, data: items.map(i => i.pvOutput), lineStyle: { color: '#F39C12', width: 2 }, itemStyle: { color: '#F39C12' },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(243, 156, 18, 0.3)' }, { offset: 1, color: 'rgba(243, 156, 18, 0.05)' }]) } }
    ]
  })
}

async function selectNode(node) {
  selectedNodeId.value = node.id
  nodeDetail.value = null
  drawerVisible.value = true
  logStore.addLog(`选中节点：${node.name}（${didShort(node.did)}，${didStatusLabel(node.didStatus)}）`, 'INFO', 'CLOUD')
  nextTick(() => initDetailChart())
  try {
    nodeDetail.value = await getNode(node.id)
  } catch {
    nodeDetail.value = null
  }
}

function goDispatch() {
  if (selectedNode.value) perspectiveStore.setCurrentNode(selectedNode.value.id)
  logStore.addLog(`从节点 ${selectedNode.value?.id || ''} 进入云端聚合与调度`, 'INFO', 'CLOUD')
  drawerVisible.value = false
  router.push('/cloud/aggregate')
}

onMounted(async () => {
  if (!perspectiveStore.nodesLoaded) {
    try { await perspectiveStore.fetchNodes() } catch { /* 已由 request.js 提示 */ }
  }
  initMainChart()
  initSideCharts()
  logStore.addLog(`进入全网设备状态图：${nodes.value.length} 个节点，${activeDidCount.value} 个 DID 活跃`, 'INFO', 'CLOUD')

  // 订阅 node_status：store 已更新指标，这里刷新图表与时间戳
  offWs = wsClient.on(WS_TYPES.NODE_STATUS, (payload, msg) => {
    lastPushAt.value = fmtTime(msg?.ts || Date.now(), 'HH:mm:ss')
    refreshCharts()
  })

  resizeHandler = () => {
    chartInstance?.resize()
    loadChartInstance?.resize()
    storageChartInstance?.resize()
    detailChartInstance?.resize()
  }
  window.addEventListener('resize', resizeHandler)
})

// 节点数量变化（首次拉取完成）时重建拓扑
watch(() => nodes.value.length, () => { initMainChart(); initSideCharts() })

onUnmounted(() => {
  offWs && offWs()
  chartInstance?.dispose()
  loadChartInstance?.dispose()
  storageChartInstance?.dispose()
  detailChartInstance?.dispose()
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
})
</script>

<style scoped>
.topology-container {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.page-header {
  margin-bottom: 1.5vh;
}

.page-title {
  font-size: clamp(16px, 2vw, 24px);
  color: var(--color-text);
  margin-bottom: 0.5vh;
}

.page-desc {
  color: var(--color-text-secondary);
  font-size: clamp(11px, 1.2vw, 14px);
}

.topology-content {
  flex: 1;
  display: flex;
  gap: 1.5vw;
  overflow: hidden;
}

.main-area {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: transparent;
  border-radius: 1vw;
  padding: 1.2vw;
  border: 1px solid rgba(0, 180, 216, 0.15);
}

.topology-wrapper {
  flex: 1;
  position: relative;
  min-height: 25vh;
}

.chart-container {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
}

.node-cards {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
}

.node-card {
  position: absolute;
  width: clamp(100px, 11vw, 140px);
  background: linear-gradient(135deg, rgba(27, 40, 56, 0.95) 0%, rgba(13, 27, 42, 0.95) 100%);
  border-radius: clamp(6px, 0.6vw, 10px);
  border: 2px solid var(--bg-tertiary);
  cursor: pointer;
  pointer-events: auto;
  transition: all 0.3s ease;
  overflow: hidden;
  box-shadow: 0 0.3vw 1.5vw rgba(0, 0, 0, 0.3);
}

.node-card:hover, .node-card.hover {
  transform: translateY(-0.3vw) scale(1.02);
  box-shadow: 0 0.5vw 2vw rgba(0, 180, 216, 0.3);
  border-color: var(--color-primary);
}

.node-card.online { border-left: 0.3vw solid var(--color-success); }
.node-card.warning { border-left: 0.3vw solid var(--color-warning); }
.node-card.offline { border-left: 0.3vw solid var(--color-danger); }

.card-header {
  display: flex;
  align-items: center;
  gap: 0.5vw;
  padding: 0.8vw 1vw;
  background: var(--bg-tertiary);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.node-status-indicator {
  width: clamp(5px, 0.5vw, 8px);
  height: clamp(5px, 0.5vw, 8px);
  border-radius: 50%;
  animation: pulse 2s infinite;
}

.node-status-indicator.online { background: var(--color-success); }
.node-status-indicator.warning { background: var(--color-warning); }
.node-status-indicator.offline { background: var(--color-danger); }

.node-id {
  font-size: clamp(10px, 1.1vw, 13px);
  font-weight: 600;
  color: var(--color-text);
}

.node-badge {
  margin-left: auto;
  padding: 0.15vw 0.6vw;
  border-radius: 0.6vw;
  font-size: clamp(8px, 0.8vw, 10px);
  font-weight: 600;
}

.node-badge.online {
  background: rgba(46, 204, 113, 0.2);
  color: var(--color-success);
}

.node-badge.warning {
  background: rgba(243, 156, 18, 0.2);
  color: var(--color-warning);
}

.node-badge.offline {
  background: rgba(230, 57, 70, 0.2);
  color: var(--color-danger);
}

.card-body {
  padding: 0.8vw 1vw;
}

.data-row {
  display: flex;
  align-items: center;
  gap: 0.5vw;
  padding: 0.4vw 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
}

.data-row:last-child {
  border-bottom: none;
}

.data-icon {
  font-size: clamp(10px, 1.1vw, 14px);
  width: clamp(14px, 1.5vw, 20px);
  text-align: center;
}

.data-label {
  font-size: clamp(9px, 0.9vw, 11px);
  color: var(--color-text-secondary);
  width: clamp(28px, 3vw, 40px);
}

.data-value {
  margin-left: auto;
  font-size: clamp(9px, 1vw, 12px);
  font-weight: 600;
  color: var(--color-text);
  font-family: 'Consolas', monospace;
}

.data-value.low {
  color: var(--color-warning);
}

.card-footer {
  padding: 0.6vw 1vw;
  background: rgba(0, 180, 216, 0.05);
  border-top: 1px solid rgba(255, 255, 255, 0.03);
}

.view-detail {
  font-size: clamp(8px, 0.9vw, 11px);
  color: var(--color-primary);
  opacity: 0.7;
  transition: opacity 0.3s ease;
}

.node-card:hover .view-detail {
  opacity: 1;
}

.center-node {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.6vw;
  pointer-events: none;
}

.center-icon {
  font-size: clamp(32px, 4vw, 48px);
  animation: pulse 3s infinite;
}

.center-label {
  font-size: clamp(10px, 1.2vw, 14px);
  font-weight: 600;
  color: var(--color-primary);
  text-shadow: 0 0 1.5vw rgba(0, 180, 216, 0.5);
}

.stats-overview {
  display: flex;
  gap: 1vw;
  padding-top: 1vw;
  border-top: 1px solid var(--bg-tertiary);
}

.stat-card {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 0.8vw;
  background: transparent;
  padding: 0.8vw 1.2vw;
  border-radius: 0.6vw;
  border-left: 0.2vw solid var(--color-primary);
  border: 1px solid rgba(0, 180, 216, 0.2);
}

.stat-card.warning {
  border-left-color: var(--color-warning);
}

.stat-icon {
  font-size: clamp(16px, 2vw, 24px);
}

.stat-info {
  display: flex;
  flex-direction: column;
}

.stat-value {
  font-size: clamp(14px, 1.6vw, 20px);
  font-weight: bold;
  color: var(--color-text);
}

.stat-label {
  font-size: clamp(9px, 1vw, 12px);
  color: var(--color-text-secondary);
}

.side-panel {
  width: clamp(180px, 18vw, 280px);
  min-width: 150px;
  display: flex;
  flex-direction: column;
  gap: 1vw;
}

.panel-section {
  background: transparent;
  border-radius: 1vw;
  padding: 1.2vw;
  border: 1px solid rgba(0, 180, 216, 0.1);
}

.section-title {
  display: flex;
  align-items: center;
  gap: 0.5vw;
  margin-bottom: 0.8vw;
  color: var(--color-primary);
  font-size: clamp(11px, 1.2vw, 14px);
  font-weight: 600;
}

.title-icon {
  font-size: clamp(12px, 1.3vw, 16px);
}

.mini-chart {
  height: clamp(80px, 10vh, 120px);
}

.node-list {
  display: flex;
  flex-direction: column;
  gap: 0.5vw;
}

.node-item {
  display: flex;
  align-items: center;
  gap: 0.8vw;
  padding: 0.8vw;
  background: transparent;
  border-radius: 0.6vw;
  cursor: pointer;
  transition: all 0.3s ease;
  border: 1px solid rgba(0, 180, 216, 0.2);
}

.node-item:hover {
  border-color: var(--color-primary);
  background: rgba(0, 180, 216, 0.1);
}

.node-item.online { border-left: 0.2vw solid var(--color-success); }
.node-item.warning { border-left: 0.2vw solid var(--color-warning); }
.node-item.offline { border-left: 0.2vw solid var(--color-danger); }

.node-icon {
  font-size: clamp(12px, 1.3vw, 16px);
}

.node-info {
  flex: 1;
}

.node-name {
  font-size: clamp(10px, 1.1vw, 13px);
  color: var(--color-text);
  font-weight: 500;
}

.node-meta {
  font-size: clamp(8px, 0.9vw, 11px);
  color: var(--color-text-secondary);
  margin-top: 0.1vw;
}

.node-arrow {
  color: var(--color-text-secondary);
  font-size: clamp(10px, 1.1vw, 14px);
}

.node-detail {
  padding: 0 1.2vw;
}

.detail-header {
  display: flex;
  align-items: center;
  gap: 1.2vw;
  margin-bottom: 1.5vw;
  padding-bottom: 1.2vw;
  border-bottom: 1px solid var(--bg-tertiary);
}

.node-avatar {
  width: clamp(40px, 5vw, 60px);
  height: clamp(40px, 5vw, 60px);
  border-radius: 1vw;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: clamp(20px, 2.5vw, 28px);
}

.node-avatar.online { background: rgba(46, 204, 113, 0.2); }
.node-avatar.warning { background: rgba(243, 156, 18, 0.2); }
.node-avatar.offline { background: rgba(230, 57, 70, 0.2); }

.node-basic {
  display: flex;
  flex-direction: column;
  gap: 0.3vw;
}

.node-id {
  font-size: clamp(14px, 1.5vw, 18px);
  font-weight: 600;
  color: var(--color-text);
}

.node-status {
  font-size: clamp(10px, 1.1vw, 13px);
}

.node-status.online { color: var(--color-success); }
.node-status.warning { color: var(--color-warning); }
.node-status.offline { color: var(--color-danger); }

.detail-section {
  margin-bottom: 1.5vw;
}

.detail-section .section-title {
  font-size: clamp(11px, 1.2vw, 14px);
  color: var(--color-text);
  margin-bottom: 0.8vw;
}

.data-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.8vw;
}

.data-card {
  display: flex;
  align-items: center;
  gap: 0.8vw;
  background: var(--bg-tertiary);
  padding: 0.8vw;
  border-radius: 0.6vw;
}

.data-icon {
  font-size: clamp(16px, 2vw, 24px);
}

.data-content {
  display: flex;
  flex-direction: column;
}

.data-value {
  font-size: clamp(12px, 1.5vw, 18px);
  font-weight: 600;
  color: var(--color-text);
}

.data-label {
  font-size: clamp(8px, 0.9vw, 11px);
  color: var(--color-text-secondary);
}

.info-list {
  display: flex;
  flex-direction: column;
  gap: 0.5vw;
}

.info-item {
  display: flex;
  justify-content: space-between;
  padding: 0.6vw 0.8vw;
  background: var(--bg-tertiary);
  border-radius: 0.4vw;
}

.info-label {
  font-size: clamp(10px, 1.1vw, 13px);
  color: var(--color-text-secondary);
}

.info-value {
  font-size: clamp(10px, 1.1vw, 13px);
  color: var(--color-text);
}

.detail-chart {
  height: clamp(100px, 12vh, 150px);
  background: var(--bg-tertiary);
  border-radius: 0.6vw;
}

.action-section {
  margin-top: 1.5vw;
}

.action-section .el-button {
  width: 100%;
  height: clamp(36px, 4vw, 48px);
  font-size: clamp(12px, 1.3vw, 16px);
}

.dispatch-result {
  text-align: center;
  padding: 1.5vw;
}

.result-icon {
  font-size: clamp(40px, 5vw, 64px);
  margin-bottom: 1.2vw;
}

.result-text {
  font-size: clamp(14px, 1.5vw, 18px);
  color: var(--color-text);
  margin-bottom: 1.5vw;
}

.task-info {
  background: var(--bg-tertiary);
  padding: 1.2vw;
  border-radius: 0.6vw;
  text-align: left;
}

.task-info p {
  margin: 0.5vw 0;
  color: var(--color-text-secondary);
}

/* ---- 第二波新增：DID 徽章 / 实时标签 ---- */
.card-did {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.4vw;
  padding: 0.4vw 1vw 0;
}
.did-short {
  font-family: 'Consolas', monospace;
  font-size: clamp(8px, 0.8vw, 10px);
  color: var(--color-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.did-badge {
  display: inline-block;
  padding: 0.1vw 0.5vw;
  border-radius: 999px;
  font-size: clamp(8px, 0.75vw, 10px);
  font-weight: 600;
  white-space: nowrap;
  border: 1px solid transparent;
}
.did-badge.mini { font-size: 9px; padding: 0 6px; margin-left: 4px; }
.did-badge.active { color: var(--color-success); background: rgba(46, 204, 113, 0.15); border-color: rgba(46, 204, 113, 0.4); }
.did-badge.frozen { color: var(--color-warning); background: rgba(243, 156, 18, 0.15); border-color: rgba(243, 156, 18, 0.4); }
.did-badge.revoked { color: var(--color-danger); background: rgba(230, 57, 70, 0.15); border-color: rgba(230, 57, 70, 0.4); }
.did-badge.unknown { color: var(--color-text-secondary); background: rgba(136, 146, 176, 0.15); }
.center-sub { font-size: clamp(9px, 0.9vw, 11px); color: var(--color-text-secondary); }
.live-tag { margin-left: auto; font-size: 10px; font-weight: 400; color: var(--color-text-secondary); }
.live-tag.on { color: var(--color-success); }
.topology-content { min-height: 0; }
.info-value.mono { font-family: 'Consolas', monospace; font-size: 11px; word-break: break-all; text-align: right; max-width: 70%; }
</style>
