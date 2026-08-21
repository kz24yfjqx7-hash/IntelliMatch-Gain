<template>
  <div class="risk-container">
    <div class="page-header">
      <h2 class="page-title">⚠️ 动态隐私风险评估</h2>
      <p class="page-desc">基于查询频率、数据粒度、暴露字段与剩余隐私预算四因子加权评分（POST /risk/assess），给出 ε 建议并拦截高风险出域</p>
      <div class="current-node">
        <span class="node-label">当前节点：</span>
        <span class="node-name">{{ perspectiveStore.currentNodeInfo.name }}</span>
        <span class="node-status" :class="perspectiveStore.currentNodeInfo.status">{{ perspectiveStore.currentNodeInfo.status === 'online' ? '在线' : '告警' }}</span>
      </div>
      <div class="compute-info">
        <span class="info-badge">风险模型：算法服务四因子加权（0.35/0.25/0.20/0.20）</span>
      </div>
    </div>

    <div class="risk-content">
      <div class="top-grid">
        <!-- 特征输入 -->
        <div class="panel controls">
          <div class="panel-title">🎛️ 访问特征（features）</div>
          <div class="ctrl">
            <div class="ctrl-head"><span>查询频率 queryFreq</span><b>{{ features.queryFreq }} 次/5min</b></div>
            <el-slider v-model="features.queryFreq" :min="0" :max="30" :step="1" />
          </div>
          <div class="ctrl">
            <div class="ctrl-head"><span>数据粒度 dataGranularity</span><b>{{ GRAN_LABELS[features.dataGranularity] }}</b></div>
            <el-select v-model="features.dataGranularity" size="small" style="width: 100%">
              <el-option v-for="(label, key) in GRAN_LABELS" :key="key" :label="label" :value="key" />
            </el-select>
          </div>
          <div class="ctrl">
            <div class="ctrl-head"><span>暴露字段 exposedFields</span><b>{{ features.exposedFields }} 个</b></div>
            <el-slider v-model="features.exposedFields" :min="0" :max="12" :step="1" />
          </div>
          <div class="ctrl">
            <div class="ctrl-head"><span>剩余隐私预算 epsilonRemaining</span><b>ε {{ features.epsilonRemaining.toFixed(2) }}</b></div>
            <el-slider v-model="features.epsilonRemaining" :min="0" :max="1" :step="0.05" />
          </div>
          <div class="ctrl-actions">
            <el-switch v-model="autoAssess" active-text="改参自动评估" size="small" />
            <el-button type="primary" size="small" :loading="assessing" @click="assess">立即评估</el-button>
          </div>
          <div class="preset-row">
            <span>场景预设：</span>
            <el-button link size="small" @click="applyPreset('low')">低风险统计查询</el-button>
            <el-button link size="small" @click="applyPreset('high')">高频分钟级拉取</el-button>
            <el-button link size="small" @click="applyPreset('critical')">预算耗尽批量导出</el-button>
          </div>
        </div>

        <!-- 仪表盘 -->
        <div class="panel gauge-panel" :class="levelClass">
          <div class="panel-title">📟 综合风险评分</div>
          <div ref="gaugeRef" class="gauge"></div>
          <div class="gauge-meta">
            <span class="level-tag" :class="levelClass">{{ RISK_LABELS[result?.level] || '--' }}风险 · {{ result?.level || '--' }}</span>
            <span class="evidence" v-if="result?.evidenceId">存证 {{ result.evidenceId }}</span>
          </div>
          <div class="suggestion" v-if="result">
            <span class="sg-icon">💡</span>
            <span>{{ result.suggestion }}</span>
          </div>
        </div>

        <!-- 因子表 + 权重柱图 -->
        <div class="panel factors-panel">
          <div class="panel-title">🧮 因子贡献（weight × score）</div>
          <el-table :data="factorRows" size="small" style="width: 100%">
            <el-table-column prop="name" label="因子" width="90" />
            <el-table-column label="权重" width="70">
              <template #default="{ row }">{{ fmtNumber(row.weight, 2) }}</template>
            </el-table-column>
            <el-table-column label="得分" width="70">
              <template #default="{ row }"><span :class="scoreClass(row.score)">{{ row.score }}</span></template>
            </el-table-column>
            <el-table-column label="贡献" width="70">
              <template #default="{ row }">{{ fmtNumber(row.weight * row.score, 1) }}</template>
            </el-table-column>
            <el-table-column prop="desc" label="说明" min-width="120" />
          </el-table>
          <div ref="factorChartRef" class="factor-chart"></div>
        </div>
      </div>

      <div class="bottom-grid">
        <div class="panel history-panel">
          <div class="panel-title">
            📈 历史评分（GET /risk/history）
            <span class="pt-meta">{{ history.length }} 条</span>
          </div>
          <div ref="historyRef" class="history-chart"></div>
        </div>
        <div class="panel stats-panel">
          <div class="panel-title">📊 本地统计（纯 JS 归一化）</div>
          <div class="analysis-stats">
            <div class="stat-item"><span class="stat-label">平均风险</span><span class="stat-value">{{ fmtNumber(stats.mean, 1) }}</span></div>
            <div class="stat-item"><span class="stat-label">标准差</span><span class="stat-value">{{ fmtNumber(stats.std, 2) }}</span></div>
            <div class="stat-item"><span class="stat-label">变异系数</span><span class="stat-value">{{ stats.mean ? fmtNumber(stats.std / stats.mean * 100, 1) : '--' }}%</span></div>
            <div class="stat-item"><span class="stat-label">偏度</span><span class="stat-value">{{ fmtNumber(stats.skewness, 3) }}</span></div>
            <div class="stat-item"><span class="stat-label">当前 z-score</span><span class="stat-value">{{ fmtNumber(latestZ, 2) }}</span></div>
            <div class="stat-item"><span class="stat-label">最高 / 最低</span><span class="stat-value">{{ fmtNumber(stats.max, 1) }} / {{ fmtNumber(stats.min, 1) }}</span></div>
          </div>
          <div class="level-dist">
            <div v-for="lv in ['low', 'medium', 'high', 'critical']" :key="lv" class="ld-item" :class="lv">
              <span class="ld-count">{{ levelDist[lv] || 0 }}</span>
              <span class="ld-label">{{ RISK_LABELS[lv] }}</span>
            </div>
          </div>
        </div>
      </div>

      <transition name="alert">
        <div class="alert-banner" v-if="showAlert" :class="result?.level">
          <div class="alert-icon">🚨</div>
          <div class="alert-content">
            <div class="alert-title">评估结论：{{ RISK_LABELS[result?.level] }}风险（{{ result?.riskScore }} 分）</div>
            <div class="alert-message">{{ result?.suggestion }}；已拦截原始物理数据直接出域，仅允许经差分隐私处理的聚合结果上传。</div>
          </div>
          <div class="alert-action">
            <el-button type="danger" size="small" @click="showAlert = false">知道了</el-button>
          </div>
        </div>
      </transition>
    </div>
  </div>
</template>

<script setup>
/**
 * 动态隐私风险评估：features 由滑块/下拉控制 → assessRisk({nodeId, features}) →
 * 仪表盘 riskScore / level 标签 / factors 表 + 权重柱图 / suggestion；历史 riskHistory 折线；统计与 z-score 本地纯 JS。
 */
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import { useLogStore } from '@/stores/logs'
import { usePerspectiveStore } from '@/stores/perspective'
import { assessRisk, riskHistory } from '@/api/risk'
import { scientificCompute } from '@/services/scientificCompute'
import { fmtNumber, fmtTime, RISK_LABELS } from '@/utils/format'

const logStore = useLogStore()
const perspectiveStore = usePerspectiveStore()

const GRAN_LABELS = { second: '秒级', minute: '分钟级', '15min': '15 分钟', hour: '小时级', day: '日级' }
const LEVEL_COLORS = { low: '#2ECC71', medium: '#00B4D8', high: '#F39C12', critical: '#E63946' }

const features = reactive({ queryFreq: 12, dataGranularity: 'minute', exposedFields: 6, epsilonRemaining: 0.6 })
const autoAssess = ref(true)
const assessing = ref(false)
const result = ref(null)
const history = ref([])
const stats = ref({})
const latestZ = ref(null)
const showAlert = ref(false)

const gaugeRef = ref(null)
const factorChartRef = ref(null)
const historyRef = ref(null)
let gaugeChart = null
let factorChart = null
let historyChart = null
let debounceTimer = null

const levelClass = computed(() => result.value?.level || 'none')
const factorRows = computed(() => result.value?.factors || [])
const levelDist = computed(() => history.value.reduce((acc, h) => { acc[h.level] = (acc[h.level] || 0) + 1; return acc }, {}))

function scoreClass(s) { return s >= 80 ? 'c-critical' : s >= 60 ? 'c-high' : s >= 40 ? 'c-medium' : 'c-low' }

const PRESETS = {
  low: { queryFreq: 2, dataGranularity: 'day', exposedFields: 2, epsilonRemaining: 0.95 },
  high: { queryFreq: 12, dataGranularity: 'minute', exposedFields: 6, epsilonRemaining: 0.6 },
  critical: { queryFreq: 28, dataGranularity: 'second', exposedFields: 11, epsilonRemaining: 0.05 }
}
function applyPreset(k) { Object.assign(features, PRESETS[k]) }

async function assess() {
  if (assessing.value) return
  assessing.value = true
  const nodeId = perspectiveStore.currentNode
  try {
    const data = await assessRisk({ nodeId, features: { ...features } })
    result.value = data
    logStore.addLog(`[${nodeId}] 风险评估：${data.riskScore} 分（${RISK_LABELS[data.level]}），${data.suggestion}`, data.level === 'high' || data.level === 'critical' ? 'WARN' : 'INFO', 'EDGE', { traceId: data.traceId })
    showAlert.value = data.level === 'high' || data.level === 'critical'
    if (showAlert.value) logStore.addLog(`[${nodeId}] 风险评估模块触发：禁止物理数据直接上传！`, 'WARN', 'EDGE')
    await loadHistory()
    nextTick(() => { renderGauge(); renderFactors() })
  } catch (e) {
    logStore.addLog(`[${nodeId}] 风险评估失败：${e?.message || e}`, 'ERROR', 'EDGE', { traceId: e?.traceId })
  } finally {
    assessing.value = false
  }
}

async function loadHistory() {
  try {
    const data = await riskHistory({ nodeId: perspectiveStore.currentNode, size: 30 })
    history.value = (data?.items || []).slice().reverse()
  } catch {
    history.value = []
  }
  const scores = history.value.map(h => Number(h.riskScore))
  stats.value = scores.length ? await scientificCompute.calculateStatistics(scores) : {}
  if (scores.length && result.value) {
    const { mean, std } = stats.value
    latestZ.value = std > 0 ? (Number(result.value.riskScore) - mean) / std : 0
  }
  nextTick(renderHistory)
}

function renderGauge() {
  if (!gaugeRef.value) return
  gaugeChart = gaugeChart || echarts.init(gaugeRef.value)
  const score = Number(result.value?.riskScore ?? 0)
  const color = LEVEL_COLORS[result.value?.level] || '#8892B0'
  gaugeChart.setOption({
    backgroundColor: 'transparent',
    series: [{
      type: 'gauge', min: 0, max: 100, startAngle: 210, endAngle: -30, radius: '95%',
      axisLine: { lineStyle: { width: 14, color: [[0.4, '#2ECC71'], [0.6, '#00B4D8'], [0.8, '#F39C12'], [1, '#E63946']] } },
      pointer: { itemStyle: { color }, width: 5, length: '65%' },
      axisTick: { distance: -14, length: 4, lineStyle: { color: '#0a1018', width: 1 } },
      splitLine: { distance: -14, length: 14, lineStyle: { color: '#0a1018', width: 2 } },
      axisLabel: { color: '#8892B0', distance: 18, fontSize: 10 },
      detail: { valueAnimation: true, formatter: '{value}', color, fontSize: 30, fontWeight: 'bold', offsetCenter: [0, '70%'] },
      title: { show: false },
      data: [{ value: score }]
    }],
    animationDuration: 900
  })
}

function renderFactors() {
  if (!factorChartRef.value) return
  factorChart = factorChart || echarts.init(factorChartRef.value)
  const rows = factorRows.value
  factorChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(13, 27, 42, 0.95)', borderColor: '#00B4D8', textStyle: { color: '#fff', fontSize: 11 } },
    legend: { data: ['权重×100', '得分'], textStyle: { color: '#8892B0', fontSize: 10 }, top: 0 },
    grid: { left: 36, right: 12, top: 26, bottom: 24 },
    xAxis: { type: 'category', data: rows.map(r => r.name), axisLabel: { color: '#8892B0', fontSize: 10 }, axisLine: { lineStyle: { color: '#243447' } } },
    yAxis: { type: 'value', max: 100, axisLabel: { color: '#8892B0', fontSize: 10 }, splitLine: { lineStyle: { color: '#243447', type: 'dashed' } } },
    series: [
      { name: '权重×100', type: 'bar', barWidth: 14, data: rows.map(r => Math.round(r.weight * 100)), itemStyle: { color: 'rgba(0, 180, 216, 0.5)', borderRadius: [3, 3, 0, 0] } },
      { name: '得分', type: 'bar', barWidth: 14, data: rows.map(r => ({ value: r.score, itemStyle: { color: r.score >= 80 ? '#E63946' : r.score >= 60 ? '#F39C12' : r.score >= 40 ? '#00B4D8' : '#2ECC71', borderRadius: [3, 3, 0, 0] } })), label: { show: true, position: 'top', color: '#8892B0', fontSize: 10 } }
    ]
  })
}

function renderHistory() {
  if (!historyRef.value) return
  historyChart = historyChart || echarts.init(historyRef.value)
  const h = history.value
  historyChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(13, 27, 42, 0.95)', borderColor: '#00B4D8', textStyle: { color: '#fff', fontSize: 11 }, formatter: ps => { const p = ps[0]; const r = h[p.dataIndex]; return `${fmtTime(r.at)}<br/>评分 ${r.riskScore}（${RISK_LABELS[r.level]}）` } },
    grid: { left: 40, right: 16, top: 20, bottom: 28 },
    xAxis: { type: 'category', data: h.map(r => fmtTime(r.at, 'MM-DD HH:mm')), axisLabel: { color: '#8892B0', fontSize: 10 }, axisLine: { lineStyle: { color: '#243447' } } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { color: '#8892B0', fontSize: 10 }, splitLine: { lineStyle: { color: '#243447', type: 'dashed' } } },
    visualMap: { show: false, pieces: [{ lte: 40, color: '#2ECC71' }, { gt: 40, lte: 60, color: '#00B4D8' }, { gt: 60, lte: 80, color: '#F39C12' }, { gt: 80, color: '#E63946' }] },
    series: [{
      type: 'line', smooth: true, symbolSize: 7, data: h.map(r => r.riskScore),
      markLine: { silent: true, symbol: 'none', lineStyle: { type: 'dashed', color: '#F39C12' }, label: { color: '#F39C12', fontSize: 10, formatter: '高风险线 60' }, data: [{ yAxis: 60 }] },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(0, 180, 216, 0.3)' }, { offset: 1, color: 'rgba(0, 180, 216, 0.02)' }]) }
    }]
  })
}

watch(features, () => {
  if (!autoAssess.value) return
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(assess, 500)
})

watch(() => perspectiveStore.currentNode, () => {
  showAlert.value = false
  result.value = null
  logStore.addLog(`[${perspectiveStore.currentNode}] 切换节点，重新评估隐私风险`, 'INFO', 'EDGE')
  assess()
})

function onResize() { gaugeChart?.resize(); factorChart?.resize(); historyChart?.resize() }

onMounted(async () => {
  logStore.addLog(`进入动态隐私风险评估模块 - ${perspectiveStore.currentNodeInfo.name}`, 'INFO', 'EDGE')
  renderGauge()
  await assess()
  window.addEventListener('resize', onResize)
})
onUnmounted(() => {
  clearTimeout(debounceTimer)
  gaugeChart?.dispose(); factorChart?.dispose(); historyChart?.dispose()
  window.removeEventListener('resize', onResize)
})
</script>

<style scoped>
.risk-container { height: 100%; display: flex; flex-direction: column; }
.page-header { margin-bottom: 16px; }
.page-title { font-size: 24px; color: var(--color-text); margin-bottom: 8px; }
.page-desc { color: var(--color-text-secondary); font-size: 14px; }
.current-node { display: inline-flex; align-items: center; gap: 8px; margin-top: 12px; padding: 8px 16px; background: rgba(0, 180, 216, 0.1); border-radius: 6px; border: 1px solid rgba(0, 180, 216, 0.2); }
.node-label { color: var(--color-text-secondary); font-size: 13px; }
.node-name { color: var(--color-primary); font-weight: 600; font-size: 14px; }
.node-status { font-size: 12px; padding: 2px 8px; border-radius: 4px; }
.node-status.online { background: rgba(46, 204, 113, 0.2); color: var(--color-success); }
.node-status.warning { background: rgba(243, 156, 18, 0.2); color: var(--color-warning); }
.compute-info { display: inline-flex; margin-left: 12px; }
.info-badge { padding: 4px 12px; background: rgba(46, 204, 113, 0.15); border-radius: 4px; font-size: 12px; color: var(--color-success); }
.risk-content { flex: 1; display: flex; flex-direction: column; gap: 16px; position: relative; }
.top-grid { display: grid; grid-template-columns: 300px minmax(0, 1fr) minmax(0, 1.3fr); gap: 16px; }
.bottom-grid { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: 16px; }
.panel { background: transparent; border-radius: 12px; padding: 16px; border: 1px solid rgba(0, 180, 216, 0.15); }
.panel-title { display: flex; align-items: center; gap: 8px; color: var(--color-primary); font-weight: 600; font-size: 14px; margin-bottom: 12px; }
.pt-meta { margin-left: auto; font-size: 11px; font-weight: 400; color: var(--color-text-secondary); }
.ctrl { margin-bottom: 8px; }
.ctrl-head { display: flex; justify-content: space-between; font-size: 12px; color: var(--color-text-secondary); }
.ctrl-head b { color: var(--color-text); font-family: 'Consolas', monospace; font-weight: 600; }
.ctrl-actions { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
.preset-row { margin-top: 8px; font-size: 11px; color: var(--color-text-secondary); display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.gauge-panel { display: flex; flex-direction: column; align-items: center; transition: border-color 0.3s; }
.gauge-panel.low { border-color: rgba(46, 204, 113, 0.5); }
.gauge-panel.medium { border-color: rgba(0, 180, 216, 0.5); }
.gauge-panel.high { border-color: rgba(243, 156, 18, 0.6); }
.gauge-panel.critical { border-color: rgba(230, 57, 70, 0.7); box-shadow: 0 0 20px rgba(230, 57, 70, 0.25); }
.gauge { width: 100%; height: 200px; }
.gauge-meta { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.level-tag { padding: 3px 14px; border-radius: 999px; font-size: 13px; font-weight: 600; }
.level-tag.low { color: var(--color-success); background: rgba(46, 204, 113, 0.15); }
.level-tag.medium { color: var(--color-primary); background: rgba(0, 180, 216, 0.15); }
.level-tag.high { color: var(--color-warning); background: rgba(243, 156, 18, 0.15); }
.level-tag.critical { color: var(--color-danger); background: rgba(230, 57, 70, 0.15); }
.level-tag.none { color: var(--color-text-secondary); background: rgba(136, 146, 176, 0.15); }
.evidence { font-size: 11px; color: var(--color-text-secondary); font-family: 'Consolas', monospace; }
.suggestion { margin-top: 10px; padding: 10px 12px; border-radius: 8px; background: rgba(0, 180, 216, 0.08); font-size: 12px; color: var(--color-text); display: flex; gap: 8px; line-height: 1.5; }
.factor-chart { height: 150px; margin-top: 8px; }
.c-low { color: var(--color-success); } .c-medium { color: var(--color-primary); } .c-high { color: var(--color-warning); } .c-critical { color: var(--color-danger); }
.history-chart { height: 200px; }
.analysis-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; }
.stat-item { display: flex; justify-content: space-between; font-size: 12px; }
.stat-label { color: var(--color-text-secondary); }
.stat-value { font-family: 'Consolas', monospace; color: var(--color-primary); font-weight: 600; }
.level-dist { display: flex; gap: 8px; margin-top: 12px; }
.ld-item { flex: 1; display: flex; flex-direction: column; align-items: center; padding: 6px; border-radius: 8px; border: 1px solid rgba(136, 146, 176, 0.2); }
.ld-count { font-size: 18px; font-weight: 700; }
.ld-label { font-size: 10px; color: var(--color-text-secondary); }
.ld-item.low .ld-count { color: var(--color-success); } .ld-item.medium .ld-count { color: var(--color-primary); } .ld-item.high .ld-count { color: var(--color-warning); } .ld-item.critical .ld-count { color: var(--color-danger); }
.alert-banner { position: absolute; top: 45%; left: 50%; transform: translate(-50%, -50%); background: linear-gradient(135deg, rgba(230, 57, 70, 0.95) 0%, rgba(192, 57, 43, 0.95) 100%); border-radius: 16px; padding: 24px 32px; display: flex; align-items: center; gap: 20px; box-shadow: 0 10px 40px rgba(230, 57, 70, 0.4); z-index: 100; min-width: 520px; max-width: 720px; }
.alert-banner.high { background: linear-gradient(135deg, rgba(243, 156, 18, 0.95) 0%, rgba(211, 84, 0, 0.95) 100%); box-shadow: 0 10px 40px rgba(243, 156, 18, 0.4); }
.alert-icon { font-size: 48px; animation: pulse 1s infinite; }
.alert-content { flex: 1; }
.alert-title { font-size: 18px; font-weight: 600; color: #fff; margin-bottom: 8px; }
.alert-message { font-size: 13px; color: rgba(255, 255, 255, 0.9); line-height: 1.5; }
.alert-action { flex-shrink: 0; }
.alert-enter-active, .alert-leave-active { transition: all 0.3s ease; }
.alert-enter-from, .alert-leave-to { opacity: 0; transform: translate(-50%, -50%) scale(0.9); }
@media (max-width: 1200px) { .top-grid { grid-template-columns: 1fr 1fr; } .bottom-grid { grid-template-columns: 1fr; } }
</style>
