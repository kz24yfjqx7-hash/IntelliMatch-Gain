<template>
  <div class="classification-container">
    <div class="page-header">
      <h2 class="page-title">📊 本地感知与分级</h2>
      <p class="page-desc">边缘节点本地数据采集，调用算法服务 k-means + 规则加权完成自动分类分级（POST /assets/classify），结果可一键登记为数据资产</p>
      <div class="current-node">
        <span class="node-label">当前节点：</span>
        <span class="node-name">{{ currentNode.name }}</span>
        <span class="node-status" :class="currentNode.status">{{ currentNode.status === 'online' ? '在线' : currentNode.status === 'warning' ? '告警' : '离线' }}</span>
        <span class="node-did" :title="currentNode.did">{{ shortDid(currentNode.did) }}</span>
      </div>
      <div class="compute-status">
        <span class="status-dot"></span>
        <span class="status-text">分级引擎：算法服务 k-means(k=3) + 规则</span>
      </div>
    </div>

    <div class="classification-content">
      <div class="sensor-status">
        <div class="status-item" v-for="sensor in sensors" :key="sensor.id">
          <span class="sensor-icon">{{ sensor.icon }}</span>
          <span class="sensor-name">{{ sensor.name }}</span>
          <span class="sensor-status" :class="sensor.status">{{ sensor.statusText }}</span>
        </div>
        <div class="toolbar">
          <el-button type="primary" :loading="classifying" @click="startClassify">🚀 开始分级</el-button>
          <el-button :disabled="!analysisComplete || registering" :loading="registering" type="success" v-permission.disable="'asset:write'" @click="registerAll">
            🔗 一键登记为数据资产
          </el-button>
          <el-button text @click="resetRecords">重置字段</el-button>
        </div>
      </div>

      <div class="data-table-wrapper">
        <el-table :data="tableData" style="width: 100%" :row-class-name="getRowClass">
          <el-table-column width="48">
            <template #header>
              <el-checkbox :model-value="allChecked" :indeterminate="someChecked && !allChecked" @change="toggleAll" />
            </template>
            <template #default="{ row }">
              <el-checkbox v-model="row.enabled" />
            </template>
          </el-table-column>
          <el-table-column label="数据字段" width="150">
            <template #default="{ row }">
              <span class="field-name">{{ row.field }}</span>
            </template>
          </el-table-column>
          <el-table-column label="采集值" width="120">
            <template #default="{ row }">
              <span class="value-text">{{ row.value }}</span>
            </template>
          </el-table-column>
          <el-table-column label="数据类型" width="110">
            <template #default="{ row }">
              <el-select v-model="row.dataType" size="small" @change="markDirty">
                <el-option v-for="(label, key) in DATA_TYPE_LABELS" :key="key" :label="label" :value="key" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="含字段（影响敏感度）" min-width="210">
            <template #default="{ row }">
              <el-select v-model="row.fields" multiple collapse-tags collapse-tags-tooltip size="small" style="width: 100%" @change="markDirty">
                <el-option v-for="f in FIELD_OPTIONS" :key="f" :label="f" :value="f" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="粒度" width="100">
            <template #default="{ row }">
              <el-select v-model="row.freq" size="small" @change="markDirty">
                <el-option v-for="(label, key) in FREQ_LABELS" :key="key" :label="label" :value="key" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="数据量(条)" width="120">
            <template #default="{ row }">
              <el-input-number v-model="row.volume" :min="1" :max="10000000" :step="100" size="small" controls-position="right" style="width: 100%" @change="markDirty" />
            </template>
          </el-table-column>
          <el-table-column label="分级结果" width="120">
            <template #default="{ row }">
              <div class="level-badge" :class="levelClass(row.result?.level)" v-if="row.result">
                <span class="level-text">{{ LEVEL_LABELS[row.result.level] || row.result.level }}</span>
              </div>
              <span v-else-if="classifying && row.enabled" class="analyzing">分析中...</span>
              <span v-else class="analyzing">{{ row.enabled ? '待分级' : '未参与' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="score / 簇" width="100">
            <template #default="{ row }">
              <span v-if="row.result" class="mono">{{ fmtNumber(row.result.score, 3) }} / C{{ row.result.cluster }}</span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column label="判定理由" min-width="180">
            <template #default="{ row }">
              <span class="desc-text" v-if="row.result">{{ row.result.reason }}</span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column label="登记" width="150">
            <template #default="{ row }">
              <span v-if="row.registered" class="registered" :title="`hash ${row.registered.hash}`">
                #{{ row.registered.id }} · {{ row.registered.evidenceId }}
              </span>
              <span v-else>-</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="bottom-grid" v-if="analysisComplete">
        <div class="cluster-analysis">
          <div class="analysis-header">
            <span class="header-icon">🎯</span>
            <span class="header-title">聚类分析（{{ algorithm }}）</span>
            <span class="header-meta">traceId {{ lastTraceId || '--' }}</span>
          </div>
          <div ref="scatterRef" class="scatter-chart"></div>
          <div class="cluster-stats">
            <div class="stat-item" v-for="(c, i) in clusterCenters" :key="i">
              <span class="stat-label">簇 C{{ i }} 中心：</span>
              <span class="stat-value">敏感 {{ fmtNumber(c[0], 2) }} / 粒度 {{ fmtNumber(c[1], 2) }} / 数据量 {{ fmtNumber(c[2], 2) }}</span>
            </div>
          </div>
        </div>

        <div class="summary-card">
          <div class="summary-header">
            <span class="summary-icon">📋</span>
            <span>分级统计</span>
          </div>
          <div class="summary-stats">
            <div class="stat-item" v-for="lv in ['L1', 'L2', 'L3', 'L4']" :key="lv">
              <span class="stat-value" :class="lv.toLowerCase()">{{ stats[lv] }}</span>
              <span class="stat-label">{{ LEVEL_LABELS[lv] }}</span>
            </div>
          </div>
          <div class="factor-list">
            <div v-for="row in classifiedRows" :key="row.key" class="factor-row">
              <span class="factor-name">{{ row.field }}</span>
              <span class="factor-bar">
                <span class="bar sens" :style="{ width: pct(row.result.factors?.sensitivity) }" title="敏感度"></span>
                <span class="bar gran" :style="{ width: pct(row.result.factors?.granularity) }" title="粒度"></span>
                <span class="bar vol" :style="{ width: pct(row.result.factors?.volume) }" title="数据量"></span>
              </span>
            </div>
            <div class="factor-legend"><i class="sens"></i>敏感度 <i class="gran"></i>粒度 <i class="vol"></i>数据量</div>
          </div>
          <div v-if="registerSummary" class="register-summary">
            <div class="rs-title">✅ 已登记 {{ registerSummary.count }} 条资产并上链</div>
            <div class="rs-item" v-for="r in registerSummary.items" :key="r.id">
              <span>#{{ r.id }} {{ r.name }}</span>
              <span class="mono">{{ shortHash(r.hash) }} · {{ r.evidenceId }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * 本地感知与分级：
 * - records 由当前节点的字段表构造（dataType / fields / freq / volume 可编辑，改输入重跑结果会变）
 * - 「开始分级」→ classifyAssets({records}) → 展示 level/score/reason/factors/cluster 与 clusterCenters 散点图
 * - 「一键登记为数据资产」→ registerAsset（sourceDid 用当前节点 did）→ 提示 hash/evidenceId 并写日志
 */
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { useLogStore } from '@/stores/logs'
import { usePerspectiveStore } from '@/stores/perspective'
import { classifyAssets, registerAsset } from '@/api/asset'
import { fmtNumber, shortDid, shortHash, LEVEL_LABELS, DATA_TYPE_LABELS } from '@/utils/format'

const logStore = useLogStore()
const perspectiveStore = usePerspectiveStore()

const FIELD_OPTIONS = ['ts', 'value', 'power', 'soc', 'gps', 'location', 'owner', 'user', 'id', 'price', 'contract', 'phone', 'address']
const FREQ_LABELS = { second: '秒级', minute: '分钟级', '15min': '15 分钟', hour: '小时级', day: '日级' }

const sensors = ref([
  { id: 1, name: '光伏传感器', icon: '☀️', status: 'online', statusText: '在线' },
  { id: 2, name: '储能传感器', icon: '🔋', status: 'online', statusText: '在线' },
  { id: 3, name: '负荷传感器', icon: '⚡', status: 'online', statusText: '在线' },
  { id: 4, name: '位置模块', icon: '📍', status: 'online', statusText: '在线' }
])

const currentNode = computed(() => perspectiveStore.currentNodeInfo)
const tableData = ref([])
const classifying = ref(false)
const registering = ref(false)
const analysisComplete = ref(false)
const clusterCenters = ref([])
const algorithm = ref('')
const lastTraceId = ref('')
const registerSummary = ref(null)
const scatterRef = ref(null)
let scatterChart = null

/** 由当前节点实时指标构造默认字段表（用户可改） */
function buildRecords() {
  const d = perspectiveStore.currentNodeData
  const area = `节点${String(perspectiveStore.currentNode).split('-')[1] || ''}区域`
  return [
    { key: 'pv', field: '光伏发电功率', value: `${fmtNumber(d.pvOutput)} kW`, dataType: 'pv', fields: ['ts', 'value'], freq: 'minute', volume: 1440, payload: { pvOutput: d.pvOutput } },
    { key: 'storage', field: '储能充放电功率', value: `${fmtNumber(d.storageOutput)} kW`, dataType: 'storage', fields: ['ts', 'power', 'soc'], freq: 'minute', volume: 1440, payload: { storageOutput: d.storageOutput, soc: d.soc } },
    { key: 'load', field: '负荷曲线数据', value: `${fmtNumber(d.load)} kW`, dataType: 'load', fields: ['ts', 'value', 'user'], freq: 'minute', volume: 8640, payload: { load: d.load } },
    { key: 'location', field: '用户位置信息', value: area, dataType: 'load', fields: ['gps', 'location', 'owner', 'address'], freq: 'second', volume: 86400, payload: { area } },
    { key: 'status', field: '设备运行状态', value: d.soc > 50 ? '正常运行' : '低电量警告', dataType: 'storage', fields: ['ts'], freq: 'hour', volume: 24, payload: { soc: d.soc, status: d.soc > 50 ? 'normal' : 'low' } },
    { key: 'dispatch', field: '调度指令记录', value: '近 24h 指令', dataType: 'dispatch', fields: ['ts', 'id', 'price', 'contract'], freq: '15min', volume: 96, payload: { commands: 3 } }
  ].map(r => ({ ...r, enabled: true, result: null, registered: null }))
}

const allChecked = computed(() => tableData.value.length > 0 && tableData.value.every(r => r.enabled))
const someChecked = computed(() => tableData.value.some(r => r.enabled))
function toggleAll(v) { tableData.value.forEach(r => { r.enabled = Boolean(v) }) }

const classifiedRows = computed(() => tableData.value.filter(r => r.result))
const stats = computed(() => {
  const s = { L1: 0, L2: 0, L3: 0, L4: 0 }
  classifiedRows.value.forEach(r => { if (s[r.result.level] !== undefined) s[r.result.level]++ })
  return s
})

function levelClass(level) { return { L1: 'level-1', L2: 'level-2', L3: 'level-3', L4: 'level-4' }[level] || '' }
function pct(v) { return `${Math.round(Math.max(0, Math.min(1, Number(v) || 0)) * 100)}%` }
function getRowClass({ row }) { return row.result ? `row-${row.result.level}` : '' }

/** 输入被修改后旧结果失效，提示重跑 */
function markDirty() {
  tableData.value.forEach(r => { r.result = null; r.registered = null })
  analysisComplete.value = false
  registerSummary.value = null
}

function resetRecords() {
  tableData.value = buildRecords()
  markDirty()
  logStore.addLog(`[${perspectiveStore.currentNode}] 重置分级字段表`, 'INFO', 'EDGE')
}

async function startClassify() {
  const rows = tableData.value.filter(r => r.enabled)
  if (!rows.length) { ElMessage.warning('请至少勾选一条记录'); return }
  classifying.value = true
  registerSummary.value = null
  logStore.addLog(`[${perspectiveStore.currentNode}] 唤醒本地传感器采集异构数据，提交 ${rows.length} 条记录至分级引擎`, 'INFO', 'EDGE')
  try {
    const records = rows.map(r => ({ dataType: r.dataType, fields: r.fields, freq: r.freq, volume: r.volume }))
    const data = await classifyAssets({ records })
    const results = data?.results || []
    rows.forEach((r, i) => { r.result = results.find(x => x.index === i) || results[i] || null })
    tableData.value.filter(r => !r.enabled).forEach(r => { r.result = null })
    clusterCenters.value = data?.clusterCenters || []
    algorithm.value = data?.algorithm || 'kmeans(k=3)+rule'
    lastTraceId.value = data?.traceId || ''
    analysisComplete.value = true
    const summary = ['L1', 'L2', 'L3', 'L4'].map(l => `${l}×${stats.value[l]}`).join(' ')
    logStore.addLog(`[${perspectiveStore.currentNode}] 分级完成：${summary}（${algorithm.value}）`, 'INFO', 'EDGE', { traceId: lastTraceId.value })
    if (stats.value.L3 + stats.value.L4 > 0) {
      logStore.addLog(`[${perspectiveStore.currentNode}] 检测到 ${stats.value.L3 + stats.value.L4} 条 L3/L4 敏感数据，禁止原始数据直接出域`, 'WARN', 'EDGE')
    }
    nextTick(renderScatter)
  } catch (e) {
    logStore.addLog(`[${perspectiveStore.currentNode}] 分级失败：${e?.message || e}`, 'ERROR', 'EDGE', { traceId: e?.traceId })
  } finally {
    classifying.value = false
  }
}

/** 一键登记：每条已分级记录 → POST /assets（sourceDid = 当前节点 DID） */
async function registerAll() {
  const rows = classifiedRows.value.filter(r => !r.registered)
  if (!rows.length) { ElMessage.info('没有待登记的记录'); return }
  const sourceDid = currentNode.value.did
  if (!sourceDid) { ElMessage.warning('当前节点未绑定 DID，无法登记'); return }
  registering.value = true
  const items = []
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-T:]/g, '')
  for (const r of rows) {
    try {
      const name = `${currentNode.value.name}-${r.field}-${stamp}`
      const res = await registerAsset({
        name,
        dataType: r.dataType,
        sourceDid,
        level: r.result.level,
        payload: { ...r.payload, fields: r.fields, freq: r.freq, volume: r.volume, ts: new Date().toISOString() },
        description: `边缘自动分级 ${r.result.level}（score ${r.result.score}，${r.result.reason}）`
      })
      r.registered = res
      items.push({ id: res.id, name, hash: res.hash, evidenceId: res.evidenceId })
      logStore.addLog(`资产登记成功 #${res.id}「${name}」${res.level}，hash ${shortHash(res.hash)}，存证 ${res.evidenceId}`, 'INFO', 'EDGE', { traceId: res.traceId })
    } catch (e) {
      logStore.addLog(`资产登记失败「${r.field}」：${e?.message || e}`, 'ERROR', 'EDGE', { traceId: e?.traceId })
      if (e?.code === 1003) break
    }
  }
  registering.value = false
  if (items.length) {
    registerSummary.value = { count: items.length, items }
    ElMessage.success(`已登记 ${items.length} 条资产并上链，评审可在「能源数据资产」中心查看`)
  }
}

/** 聚类散点图：x 敏感度、y 粒度、气泡大小=数据量，叠加 clusterCenters */
function renderScatter() {
  if (!scatterRef.value) return
  scatterChart = scatterChart || echarts.init(scatterRef.value)
  const colors = ['#2ECC71', '#F39C12', '#E63946']
  const pts = classifiedRows.value.map(r => ({
    name: r.field,
    value: [r.result.factors?.sensitivity ?? 0, r.result.factors?.granularity ?? 0, r.result.factors?.volume ?? 0, r.result.level],
    itemStyle: { color: colors[r.result.cluster] || '#00B4D8' }
  }))
  scatterChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      backgroundColor: 'rgba(13, 27, 42, 0.95)', borderColor: '#00B4D8', textStyle: { color: '#fff', fontSize: 11 },
      formatter: p => p.seriesName === '簇中心'
        ? `簇中心 C${p.dataIndex}<br/>敏感 ${p.value[0].toFixed(2)} 粒度 ${p.value[1].toFixed(2)} 数据量 ${p.value[2].toFixed(2)}`
        : `${p.name}（${p.value[3]}）<br/>敏感 ${p.value[0]} 粒度 ${p.value[1]} 数据量 ${p.value[2]}`
    },
    grid: { left: 40, right: 16, top: 24, bottom: 32 },
    xAxis: { name: '敏感度', min: 0, max: 1, nameTextStyle: { color: '#8892B0', fontSize: 10 }, axisLabel: { color: '#8892B0', fontSize: 10 }, splitLine: { lineStyle: { color: '#243447', type: 'dashed' } } },
    yAxis: { name: '粒度', min: 0, max: 1, nameTextStyle: { color: '#8892B0', fontSize: 10 }, axisLabel: { color: '#8892B0', fontSize: 10 }, splitLine: { lineStyle: { color: '#243447', type: 'dashed' } } },
    series: [
      { name: '簇中心', type: 'scatter', symbol: 'diamond', symbolSize: 18, data: clusterCenters.value.map((c, i) => ({ value: c, itemStyle: { color: colors[i], opacity: 0.5, borderColor: '#fff', borderWidth: 1 } })) },
      { name: '记录', type: 'scatter', symbolSize: v => 10 + (v[2] || 0) * 22, data: pts, label: { show: true, position: 'top', color: '#8892B0', fontSize: 10, formatter: p => p.name } }
    ]
  })
}

watch(() => perspectiveStore.currentNode, () => {
  tableData.value = buildRecords()
  markDirty()
  logStore.addLog(`切换节点 → ${perspectiveStore.currentNodeInfo.name}，字段表已按实时指标刷新`, 'INFO', 'EDGE')
})

function onResize() { scatterChart?.resize() }

onMounted(() => {
  tableData.value = buildRecords()
  logStore.addLog(`进入本地感知与分级模块 - ${currentNode.value.name}`, 'INFO', 'EDGE')
  window.addEventListener('resize', onResize)
})
onUnmounted(() => {
  scatterChart?.dispose()
  window.removeEventListener('resize', onResize)
})
</script>

<style scoped>
.classification-container { height: 100%; display: flex; flex-direction: column; }
.page-header { margin-bottom: 16px; }
.page-title { font-size: 24px; color: var(--color-text); margin-bottom: 8px; }
.page-desc { color: var(--color-text-secondary); font-size: 14px; }
.current-node { display: inline-flex; align-items: center; gap: 8px; margin-top: 12px; padding: 8px 16px; background: rgba(0, 180, 216, 0.1); border-radius: 6px; border: 1px solid rgba(0, 180, 216, 0.2); }
.node-label { color: var(--color-text-secondary); font-size: 13px; }
.node-name { color: var(--color-primary); font-weight: 600; font-size: 14px; }
.node-status { font-size: 12px; padding: 2px 8px; border-radius: 4px; }
.node-status.online { background: rgba(46, 204, 113, 0.2); color: var(--color-success); }
.node-status.warning { background: rgba(243, 156, 18, 0.2); color: var(--color-warning); }
.node-status.offline { background: rgba(230, 57, 70, 0.2); color: var(--color-danger); }
.node-did { font-family: 'Consolas', monospace; font-size: 11px; color: var(--color-text-secondary); }
.compute-status { display: inline-flex; align-items: center; gap: 6px; margin-left: 12px; padding: 4px 12px; background: rgba(46, 204, 113, 0.15); border-radius: 4px; font-size: 12px; color: var(--color-success); }
.status-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-success); animation: pulse 2s infinite; }
.classification-content { flex: 1; display: flex; flex-direction: column; gap: 16px; }
.sensor-status { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.status-item { display: flex; align-items: center; gap: 8px; padding: 10px 16px; border-radius: 8px; border: 1px solid rgba(0, 180, 216, 0.15); }
.sensor-icon { font-size: 18px; }
.sensor-name { color: var(--color-text); font-size: 13px; }
.sensor-status { font-size: 12px; padding: 2px 8px; border-radius: 4px; }
.sensor-status.online { background: rgba(46, 204, 113, 0.2); color: var(--color-success); }
.toolbar { margin-left: auto; display: flex; gap: 8px; }
.data-table-wrapper { background: transparent; border-radius: 12px; padding: 12px; border: 1px solid rgba(0, 180, 216, 0.15); }
.data-table-wrapper :deep(.row-L3 td), .data-table-wrapper :deep(.row-L4 td) { background: rgba(230, 57, 70, 0.06) !important; }
.field-name { font-weight: 500; color: var(--color-text); }
.value-text, .mono { font-family: 'Consolas', monospace; color: var(--color-text); font-size: 12px; }
.analyzing { color: var(--color-text-secondary); font-style: italic; font-size: 12px; }
.level-badge { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 16px; font-size: 12px; animation: fadeIn 0.3s ease; }
.level-badge.level-1 { background: rgba(46, 204, 113, 0.2); color: var(--color-success); }
.level-badge.level-2 { background: rgba(0, 180, 216, 0.2); color: var(--color-primary); }
.level-badge.level-3 { background: rgba(243, 156, 18, 0.2); color: var(--color-warning); }
.level-badge.level-4 { background: rgba(230, 57, 70, 0.2); color: var(--color-danger); }
.desc-text { color: var(--color-text-secondary); font-size: 12px; }
.registered { font-family: 'Consolas', monospace; font-size: 11px; color: var(--color-success); }
.bottom-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 16px; }
.cluster-analysis { background: rgba(0, 180, 216, 0.05); border-radius: 12px; padding: 16px; border: 1px solid rgba(0, 180, 216, 0.15); }
.analysis-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-weight: 600; color: var(--color-primary); }
.header-icon { font-size: 20px; }
.header-title { font-size: 15px; }
.header-meta { margin-left: auto; font-size: 11px; font-weight: 400; color: var(--color-text-secondary); font-family: 'Consolas', monospace; }
.scatter-chart { height: 240px; }
.cluster-stats { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
.cluster-stats .stat-item { display: flex; gap: 8px; font-size: 12px; }
.cluster-stats .stat-label { color: var(--color-text-secondary); }
.cluster-stats .stat-value { font-family: 'Consolas', monospace; color: var(--color-primary); }
.summary-card { background: transparent; border-radius: 12px; padding: 16px; border: 1px solid var(--color-primary); animation: slideIn 0.5s ease; }
.summary-header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; color: var(--color-primary); font-size: 15px; font-weight: 600; }
.summary-stats { display: flex; justify-content: space-around; margin-bottom: 12px; }
.summary-stats .stat-item { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.summary-stats .stat-value { font-size: 30px; font-weight: bold; }
.summary-stats .stat-label { font-size: 12px; color: var(--color-text-secondary); }
.stat-value.l1 { color: var(--color-success); }
.stat-value.l2 { color: var(--color-primary); }
.stat-value.l3 { color: var(--color-warning); }
.stat-value.l4 { color: var(--color-danger); }
.factor-list { display: flex; flex-direction: column; gap: 6px; border-top: 1px solid rgba(0, 180, 216, 0.15); padding-top: 10px; }
.factor-row { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.factor-name { width: 100px; color: var(--color-text-secondary); }
.factor-bar { flex: 1; display: flex; flex-direction: column; gap: 2px; }
.factor-bar .bar { height: 4px; border-radius: 2px; min-width: 2px; }
.bar.sens, .factor-legend i.sens { background: var(--color-danger); }
.bar.gran, .factor-legend i.gran { background: var(--color-warning); }
.bar.vol, .factor-legend i.vol { background: var(--color-primary); }
.factor-legend { font-size: 11px; color: var(--color-text-secondary); display: flex; align-items: center; gap: 6px; }
.factor-legend i { display: inline-block; width: 10px; height: 4px; border-radius: 2px; margin-left: 8px; }
.register-summary { margin-top: 12px; padding: 10px; border-radius: 8px; background: rgba(46, 204, 113, 0.08); border: 1px solid rgba(46, 204, 113, 0.3); }
.rs-title { color: var(--color-success); font-weight: 600; font-size: 13px; margin-bottom: 6px; }
.rs-item { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; color: var(--color-text-secondary); }
@media (max-width: 1100px) { .bottom-grid { grid-template-columns: 1fr; } }
</style>
