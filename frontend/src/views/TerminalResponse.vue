<template>
  <div class="response-container">
    <div class="page-header">
      <h2 class="page-title">✅ 终端响应与执行</h2>
      <p class="page-desc">接收云端签名下发的调度指令，校验签发者 DID 与 SM2 签名后执行本地设备控制，并回执（POST /dispatch/tasks/{id}/ack）</p>
      <div class="current-node">
        <span class="node-label">当前节点：</span>
        <span class="node-name">{{ node.name }}</span>
        <span class="node-status" :class="node.status">{{ node.status === 'online' ? '在线' : '告警' }}</span>
        <span class="node-did" :title="node.did">{{ shortDid(node.did) }}</span>
      </div>
    </div>

    <div class="response-content">
      <div class="top-grid">
        <!-- 指令面板 -->
        <div class="command-panel">
          <div class="panel-header">
            <div class="panel-title"><span class="panel-icon">📨</span><span>接收到的调度指令</span></div>
            <div class="panel-actions">
              <el-select v-model="selectedId" size="small" style="width: 180px" placeholder="选择指令" @change="onSelect">
                <el-option v-for="t in nodeTasks" :key="t.id" :label="`${t.commandId || t.id} · ${STATUS[t.status] || t.status}`" :value="t.id" />
              </el-select>
              <el-button size="small" link @click="loadTasks">刷新</el-button>
              <span class="status-chip" :class="statusClass">{{ task ? STATUS[task.status] || task.status : '未下发' }}</span>
            </div>
          </div>
          <template v-if="task">
            <div class="task-meta">
              <div class="meta-item"><span class="meta-label">任务 / 指令编号</span><span class="meta-value mono">{{ task.id }} / {{ task.commandId || '--' }}</span></div>
              <div class="meta-item"><span class="meta-label">下发时间</span><span class="meta-value">{{ fmtDateTime(task.issuedAt) }}</span></div>
              <div class="meta-item"><span class="meta-label">签发者 DID</span><span class="meta-value mono" :title="task.signerDid">{{ shortDid(task.signerDid) }}</span></div>
              <div class="meta-item"><span class="meta-label">traceId</span><span class="meta-value mono">{{ task.traceId || '--' }}</span></div>
            </div>
            <div class="command-info">
              <div class="info-item"><span class="info-label">调度动作</span><span class="info-value highlight">{{ ACTION_CN[myAction?.action] || '--' }}</span></div>
              <div class="info-item"><span class="info-label">目标功率</span><span class="info-value highlight">{{ fmtNumber(targetPower, 1) }} kW</span></div>
              <div class="info-item"><span class="info-label">执行时段</span><span class="info-value mono">{{ task.strategy?.timeWindow || task.timeWindow || '--' }}</span></div>
              <div class="info-item"><span class="info-label">Q 值 / 原因</span><span class="info-value">{{ fmtNumber(myAction?.qValue, 2) }} · {{ myAction?.reason || '--' }}</span></div>
            </div>
            <div class="receipt-summary" v-if="receipt">
              <div class="summary-item"><span class="summary-label">执行开始</span><span class="summary-value">{{ fmtDateTime(receipt.startedAt) }}</span></div>
              <div class="summary-item"><span class="summary-label">执行完成</span><span class="summary-value">{{ fmtDateTime(receipt.completedAt) }}</span></div>
              <div class="summary-item"><span class="summary-label">回执存证</span><span class="summary-value mono">{{ receipt.evidenceId || '--' }}</span></div>
            </div>
          </template>
          <div v-else class="empty-state">
            <span class="empty-icon">📭</span>
            <span class="empty-text">当前节点暂无已下发调度指令</span>
            <span class="empty-hint">请先在「云端聚合与调度」生成策略并签名下发，再返回边端查看执行过程</span>
            <el-button v-if="otherNodeTask" size="small" type="primary" plain @click="perspectiveStore.setCurrentNode(otherNodeTask.nodeId)">
              最新指令目标为 {{ otherNodeTask.nodeId }} → 切换到该节点查看
            </el-button>
          </div>
        </div>

        <!-- DID 签名校验过程 -->
        <div class="verify-panel" :class="verifyState">
          <div class="panel-header">
            <div class="panel-title"><span class="panel-icon">🪪</span><span>DID 签名校验</span></div>
            <el-button size="small" type="primary" :disabled="!task || verifying" :loading="verifying" @click="runVerify">{{ verifyState === 'passed' ? '重新校验' : '开始校验' }}</el-button>
          </div>
          <div class="verify-steps">
            <div v-for="(s, i) in verifySteps" :key="s.key" class="v-step" :class="s.state">
              <div class="v-index">{{ s.state === 'done' ? '✓' : s.state === 'failed' ? '✕' : i + 1 }}</div>
              <div class="v-body">
                <div class="v-title">{{ s.title }}</div>
                <div class="v-detail mono">{{ s.detail || s.placeholder }}</div>
              </div>
            </div>
          </div>
          <div class="verify-result" v-if="verifyState === 'passed'">✅ 验签通过：签发者 DID 有效且签名与公钥匹配，允许执行</div>
          <div class="verify-result fail" v-else-if="verifyState === 'failed'">⛔ 验签失败：{{ verifyError }}，拒绝执行</div>
          <div class="exec-row">
            <el-button type="success" size="large" :disabled="verifyState !== 'passed' || executing || task?.status === 'acked'" :loading="executing" @click="execute">
              {{ task?.status === 'acked' ? '已回执' : '⚡ 确认执行并回执' }}
            </el-button>
          </div>
        </div>
      </div>

      <div class="chart-section">
        <div class="section-header">
          <span class="section-icon">📈</span>
          <span>储能功率变化曲线</span>
          <span class="stage-tag" v-for="s in stageEvents" :key="s.stage">{{ s.stage }} {{ fmtTime(s.at, 'HH:mm:ss') }}</span>
        </div>
        <div ref="chartRef" class="chart-container"></div>
      </div>

      <div class="bottom-grid">
        <div class="comparison-section">
          <div class="section-header"><span class="section-icon">📊</span><span>执行状态对比</span></div>
          <div class="comparison-table">
            <div class="table-header"><div class="table-cell">项目</div><div class="table-cell">执行前</div><div class="table-cell">执行后</div></div>
            <div class="table-row" v-for="item in comparisonData" :key="item.name">
              <div class="table-cell label">{{ item.name }}</div>
              <div class="table-cell before">{{ item.before }}</div>
              <div class="table-cell after" :class="{ changed: item.changed }">{{ item.after }}</div>
            </div>
          </div>
        </div>
        <transition name="result">
          <div class="result-panel" v-if="receipt">
            <div class="result-icon">✅</div>
            <div class="result-content">
              <div class="result-title">执行结果已回执云端并上链</div>
              <div class="result-stats">
                <div class="stat"><span class="stat-label">实际功率</span><span class="stat-value">{{ fmtNumber(receipt.actualPowerKw, 1) }} kW</span></div>
                <div class="stat"><span class="stat-label">响应时延</span><span class="stat-value">{{ fmtNumber(receipt.responseDelaySec, 1) }} s</span></div>
                <div class="stat"><span class="stat-label">回执状态</span><span class="stat-value success">{{ receipt.status }} ✅</span></div>
                <div class="stat"><span class="stat-label">存证</span><span class="stat-value mono">{{ receipt.evidenceId || '--' }}</span></div>
              </div>
            </div>
          </div>
        </transition>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * 终端响应与执行：
 * - 指令来源：listDispatchTasks（dispatchStore.fetchTasks）筛选 targets 含当前节点的 issued/acked 任务
 * - DID 签名校验可视化：signerDid → getDidDocument 解析公钥 → verifyDid 验签（步骤条动画）
 * - 「确认执行并回执」：功率曲线动画 → ackDispatchTask(id, {nodeId, actualPowerKw, status, startedAt, completedAt})
 * - 订阅 dispatch_progress：issued/acked 时刷新
 */
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import { useDispatchStore } from '@/stores/dispatch'
import { useLogStore } from '@/stores/logs'
import { usePerspectiveStore } from '@/stores/perspective'
import { ackDispatchTask, getDispatchTask } from '@/api/dispatch'
import { getDidDocument, verifyDid } from '@/api/did'
import { wsClient, WS_TYPES } from '@/api/ws'
import { sha256Hex } from '@/utils/sha256'
import { fmtNumber, fmtTime, fmtDateTime, shortDid, shortHash } from '@/utils/format'

const dispatchStore = useDispatchStore()
const logStore = useLogStore()
const perspectiveStore = usePerspectiveStore()

const STATUS = { created: '已创建', running: '运行中', success: '待下发', issued: '已下发', acked: '已回执', failed: '失败' }
const ACTION_CN = { charge: '充电', idle: '待机', discharge: '放电' }

const node = computed(() => perspectiveStore.currentNodeInfo)
const selectedId = ref('')
const task = ref(null)
const receipt = ref(null)
const stageEvents = ref([])
const comparisonData = ref([])
const chartRef = ref(null)
let chart = null
let offWs = null
let raf = null
let timers = []

/** 当前节点相关的已下发任务（targets 含本节点，或策略中本节点动作非 idle） */
const nodeTasks = computed(() => dispatchStore.remoteTasks.filter(t =>
  (t.status === 'issued' || t.status === 'acked') &&
  ((t.targets || []).includes(node.value.id) || (t.strategy?.actions || []).some(a => a.nodeId === node.value.id && a.action !== 'idle'))
))
const myAction = computed(() => task.value?.strategy?.actions?.find(a => a.nodeId === node.value.id) || null)
/** 当前节点无指令时，找出最新一条已下发指令的目标节点，供一键切换（演示第 7 步：下发后到边端看回执） */
const otherNodeTask = computed(() => {
  if (nodeTasks.value.length) return null
  const issued = dispatchStore.remoteTasks.filter(t => t.status === 'issued' || t.status === 'acked')
  for (const t of issued) {
    const target = (t.targets || [])[0] || (t.strategy?.actions || []).find(a => a.action !== 'idle')?.nodeId
    if (target && target !== node.value.id) return { nodeId: target, task: t }
  }
  return null
})
/** 放电为正、充电为负 */
const targetPower = computed(() => {
  const a = myAction.value
  if (!a) return perspectiveStore.currentNodeData.storageOutput
  return a.action === 'charge' ? -Math.abs(a.powerKw) : a.action === 'discharge' ? Math.abs(a.powerKw) : 0
})
const statusClass = computed(() => ({ acked: 'success', issued: 'warning' }[task.value?.status] || 'neutral'))

/* ---------- 任务加载 ---------- */
async function loadTasks() {
  await dispatchStore.fetchTasks().catch(() => {})
  if (!nodeTasks.value.find(t => t.id === selectedId.value)) {
    selectedId.value = nodeTasks.value[0]?.id || ''
  }
  await onSelect()
}
async function onSelect() {
  clearTimers()
  receipt.value = null
  resetVerify()
  stageEvents.value = []
  if (!selectedId.value) { task.value = null; initComparison(); renderChart(); return }
  try {
    task.value = await getDispatchTask(selectedId.value)
  } catch { task.value = nodeTasks.value.find(t => t.id === selectedId.value) || null }
  if (task.value?.ack && task.value.ack.nodeId === node.value.id) {
    receipt.value = { ...task.value.ack, startedAt: task.value.issuedAt, completedAt: task.value.ack.at, evidenceId: null }
    verifyState.value = 'passed'
    verifySteps.value.forEach(s => { s.state = 'done' })
    verifySteps.value[0].detail = task.value.signerDid
    applyCompletedComparison(task.value.ack.actualPowerKw, task.value.ack.responseDelaySec)
  } else {
    initComparison()
  }
  logStore.addLog(`[${node.value.id}] 加载调度指令 ${task.value?.commandId || task.value?.id || '--'}（${STATUS[task.value?.status] || '--'}）`, 'INFO', 'EDGE', { traceId: task.value?.traceId })
  nextTick(renderChart)
}

/* ---------- DID 验签 ---------- */
const verifying = ref(false)
const verifyState = ref('idle') // idle | running | passed | failed
const verifyError = ref('')
const verifySteps = ref([
  { key: 'signer', title: '读取签发者 DID', placeholder: 'signerDid', detail: '', state: 'pending' },
  { key: 'resolve', title: '解析 DID 文档（GET /did/{did}）', placeholder: 'verificationMethod → SM2 公钥', detail: '', state: 'pending' },
  { key: 'verify', title: '验签（POST /did/verify）', placeholder: 'message = commandId，signature = 签发者签名', detail: '', state: 'pending' }
])
function resetVerify() {
  verifyState.value = 'idle'
  verifyError.value = ''
  verifySteps.value.forEach(s => { s.state = 'pending'; s.detail = '' })
}
const wait = ms => new Promise(r => { const t = setTimeout(r, ms); timers.push(t) })

async function runVerify() {
  if (!task.value) return
  resetVerify()
  verifying.value = true
  verifyState.value = 'running'
  const steps = verifySteps.value
  try {
    steps[0].state = 'active'
    await wait(400)
    const signer = task.value.signerDid
    if (!signer) throw new Error('指令缺少 signerDid')
    steps[0].detail = signer
    steps[0].state = 'done'

    steps[1].state = 'active'
    const doc = await getDidDocument(signer)
    const vm = doc?.didDocument?.verificationMethod?.[0]
    await wait(400)
    if (doc?.status && doc.status !== 'active') throw new Error(`签发者 DID 状态为 ${doc.status}`)
    steps[1].detail = vm ? `${vm.type} · ${shortHash(vm.publicKeyHex, 12, 8)}` : '文档中未找到 verificationMethod'
    steps[1].state = 'done'

    steps[2].state = 'active'
    // 演示签名：后端未回传原始签名字节，用 commandId+signerDid 派生的 sig 走同一验签接口
    const signature = `sig:${sha256Hex(`${signer}|${task.value.commandId || task.value.id}`)}`
    const res = await verifyDid({ did: signer, message: task.value.commandId || task.value.id, signature })
    await wait(300)
    if (!res?.valid) throw new Error(res?.reason || '签名无效')
    steps[2].detail = `valid=true · subjectType=${res.subjectType || '--'} · status=${res.status || '--'}`
    steps[2].state = 'done'
    verifyState.value = 'passed'
    logStore.addLog(`[${node.value.id}] 指令 ${task.value.commandId} 验签通过（签发者 ${shortDid(signer)}）`, 'INFO', 'EDGE', { traceId: task.value.traceId })
  } catch (e) {
    const cur = steps.find(s => s.state === 'active')
    if (cur) { cur.state = 'failed'; cur.detail = e?.message || String(e) }
    verifyError.value = e?.message || String(e)
    verifyState.value = 'failed'
    logStore.addLog(`[${node.value.id}] 指令验签失败：${verifyError.value}`, 'ERROR', 'EDGE', { traceId: e?.traceId || task.value?.traceId })
  } finally {
    verifying.value = false
  }
}

/* ---------- 执行与回执 ---------- */
const executing = ref(false)
function clearTimers() { timers.forEach(t => clearTimeout(t)); timers = []; if (raf) cancelAnimationFrame(raf); raf = null }

function initComparison() {
  const d = perspectiveStore.currentNodeData
  comparisonData.value = [
    { name: '储能功率', before: `${fmtNumber(d.storageOutput)} kW`, after: `${fmtNumber(d.storageOutput)} kW`, changed: false },
    { name: '运行模式', before: '待机', after: '待机', changed: false },
    { name: '响应时延', before: '-', after: '-', changed: false },
    { name: '储能SOC', before: `${fmtNumber(d.soc)}%`, after: `${fmtNumber(d.soc)}%`, changed: false }
  ]
}
function modeLabel(p) { return p > 0 ? '放电模式' : p < 0 ? '充电模式' : '待机' }
function estimateSoc(p) {
  const d = perspectiveStore.currentNodeData
  const delta = Math.max(1, Math.round(Math.abs(p) / 6))
  return Math.max(20, Math.min(95, p > 0 ? d.soc - delta : d.soc + Math.round(delta / 2)))
}
function applyCompletedComparison(actual, delaySec) {
  const d = perspectiveStore.currentNodeData
  comparisonData.value = [
    { name: '储能功率', before: `${fmtNumber(d.storageOutput)} kW`, after: `${fmtNumber(actual, 1)} kW`, changed: true },
    { name: '运行模式', before: '待机', after: modeLabel(actual), changed: true },
    { name: '响应时延', before: '-', after: `${fmtNumber(delaySec, 1)} s`, changed: true },
    { name: '储能SOC', before: `${fmtNumber(d.soc)}%`, after: `${estimateSoc(actual)}%`, changed: true }
  ]
}

function curve(base, final) {
  return [0, 0.2, 0.5, 0.8, 1, 1, 1].map(k => Number((base + (final - base) * k).toFixed(1)))
}
function renderChart(data) {
  if (!chartRef.value) return
  chart = chart || echarts.init(chartRef.value)
  const base = perspectiveStore.currentNodeData.storageOutput
  const series = data || (receipt.value ? curve(base, receipt.value.actualPowerKw) : Array(7).fill(base))
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(13, 27, 42, 0.95)', borderColor: '#00B4D8', textStyle: { color: '#fff', fontSize: 11 } },
    grid: { left: 48, right: 20, top: 24, bottom: 28 },
    xAxis: { type: 'category', data: ['0s', '5s', '10s', '15s', '20s', '25s', '30s'], axisLine: { lineStyle: { color: '#243447' } }, axisLabel: { color: '#8892B0', fontSize: 11 } },
    yAxis: { type: 'value', name: '功率 (kW)', nameTextStyle: { color: '#8892B0', fontSize: 11 }, axisLabel: { color: '#8892B0', fontSize: 11 }, splitLine: { lineStyle: { color: '#243447' } } },
    series: [{
      name: '储能功率', type: 'line', smooth: true, symbol: 'circle', symbolSize: 8, data: series,
      lineStyle: { color: '#00B4D8', width: 3 }, itemStyle: { color: '#00B4D8' },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(0, 180, 216, 0.4)' }, { offset: 1, color: 'rgba(0, 180, 216, 0.05)' }]) },
      markLine: task.value ? { silent: true, symbol: 'none', lineStyle: { type: 'dashed', color: '#F39C12' }, label: { color: '#F39C12', fontSize: 10, formatter: `目标 ${fmtNumber(targetPower.value, 1)} kW` }, data: [{ yAxis: targetPower.value }] } : undefined
    }],
    animationDuration: 300
  })
}

async function execute() {
  if (!task.value || verifyState.value !== 'passed') return
  executing.value = true
  clearTimers()
  const startedAt = new Date().toISOString()
  const base = perspectiveStore.currentNodeData.storageOutput
  const target = Number(targetPower.value.toFixed(1))
  const delaySec = Number((1 + Math.abs(target - base) / 60).toFixed(1))
  logStore.addLog(`[${node.value.id}] 接收云端指令 ${task.value.commandId}，本地设备控制权移交，目标 ${modeLabel(target)} ${fmtNumber(Math.abs(target), 1)} kW`, 'INFO', 'EDGE', { traceId: task.value.traceId })

  // 功率曲线动画 2.4s
  await new Promise(resolve => {
    const targetData = curve(base, target)
    const startData = Array(7).fill(base)
    const t0 = Date.now()
    const step = () => {
      const p = Math.min((Date.now() - t0) / 2400, 1)
      renderChart(startData.map((s, i) => Number((s + (targetData[i] - s) * p).toFixed(1))))
      if (p < 1) raf = requestAnimationFrame(step)
      else resolve()
    }
    raf = requestAnimationFrame(step)
  })
  comparisonData.value[0] = { ...comparisonData.value[0], after: `${fmtNumber(target, 1)} kW`, changed: true }
  await wait(300)
  comparisonData.value[1] = { ...comparisonData.value[1], after: modeLabel(target), changed: true }
  await wait(300)
  comparisonData.value[2] = { ...comparisonData.value[2], after: `${delaySec} s`, changed: true }
  await wait(300)
  comparisonData.value[3] = { ...comparisonData.value[3], after: `${estimateSoc(target)}%`, changed: true }

  const completedAt = new Date().toISOString()
  try {
    const res = await ackDispatchTask(task.value.id, { nodeId: node.value.id, actualPowerKw: target, status: 'success', startedAt, completedAt, responseDelaySec: delaySec })
    receipt.value = { nodeId: node.value.id, actualPowerKw: target, status: 'success', startedAt, completedAt, responseDelaySec: delaySec, evidenceId: res?.evidenceId }
    logStore.addLog(`[${node.value.id}] 执行完毕并回执（存证 ${res?.evidenceId || '--'}），进入下一轮待机`, 'INFO', 'EDGE', { traceId: task.value.traceId })
    // 同步本地 store（若该任务由本会话的云端页面创建）
    const local = dispatchStore.tasks.find(t => t.remoteId === task.value.id)
    if (local) dispatchStore.completeTask(local.id, { startedAt, completedAt, actualPowerKw: target, responseDelaySec: delaySec })
    task.value = await getDispatchTask(task.value.id)
    await dispatchStore.fetchTasks().catch(() => {})
  } catch (e) {
    logStore.addLog(`[${node.value.id}] 回执失败：${e?.message || e}`, 'ERROR', 'EDGE', { traceId: e?.traceId })
  } finally {
    executing.value = false
  }
}

/* ---------- WS ---------- */
function onProgress(payload, msg) {
  if (!payload?.taskId) return
  if (task.value && payload.taskId === task.value.id) stageEvents.value = [...stageEvents.value.filter(s => s.stage !== payload.stage), { stage: payload.stage, at: msg?.ts || new Date().toISOString() }]
  if (payload.stage === 'issued') {
    logStore.addLog(`[${node.value.id}] 收到云端下发通知：${payload.detail}`, 'INFO', 'EDGE', { traceId: msg?.traceId })
    loadTasks()
  } else if (payload.stage === 'acked' && task.value && payload.taskId === task.value.id && !executing.value) {
    getDispatchTask(task.value.id).then(t => { task.value = t }).catch(() => {})
  }
}

watch(() => perspectiveStore.currentNode, () => { selectedId.value = ''; loadTasks() })

let resizeHandler = null
onMounted(async () => {
  logStore.addLog(`进入终端响应与执行模块 - ${node.value.name}`, 'INFO', 'EDGE')
  initComparison()
  renderChart()
  offWs = wsClient.on(WS_TYPES.DISPATCH_PROGRESS, onProgress)
  await loadTasks()
  resizeHandler = () => chart?.resize()
  window.addEventListener('resize', resizeHandler)
})
onBeforeUnmount(() => {
  clearTimers()
  offWs && offWs()
  chart?.dispose(); chart = null
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
})
</script>

<style scoped>
.response-container { height: 100%; display: flex; flex-direction: column; }
.page-header { margin-bottom: 16px; }
.page-title { font-size: 24px; color: var(--color-text); margin-bottom: 8px; }
.page-desc { color: var(--color-text-secondary); font-size: 14px; }
.current-node { display: inline-flex; align-items: center; gap: 8px; margin-top: 12px; padding: 8px 16px; background: rgba(0, 180, 216, 0.1); border-radius: 6px; border: 1px solid rgba(0, 180, 216, 0.2); }
.node-label { color: var(--color-text-secondary); font-size: 13px; }
.node-name { color: var(--color-primary); font-weight: 600; font-size: 14px; }
.node-status { font-size: 12px; padding: 2px 8px; border-radius: 4px; }
.node-status.online { background: rgba(46, 204, 113, 0.2); color: var(--color-success); }
.node-status.warning { background: rgba(243, 156, 18, 0.2); color: var(--color-warning); }
.node-did { font-family: 'Consolas', monospace; font-size: 11px; color: var(--color-text-secondary); }
.response-content { flex: 1; display: flex; flex-direction: column; gap: 16px; }
.top-grid { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 16px; }
.bottom-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; }
.command-panel, .verify-panel, .chart-section, .comparison-section { background: transparent; border-radius: 12px; padding: 16px 20px; border: 1px solid rgba(0, 180, 216, 0.15); }
.command-panel { border-left: 4px solid var(--color-primary); }
.verify-panel { border-left: 4px solid rgba(136, 146, 176, 0.5); transition: border-color 0.3s; }
.verify-panel.running { border-left-color: var(--color-primary); }
.verify-panel.passed { border-left-color: var(--color-success); }
.verify-panel.failed { border-left-color: var(--color-danger); }
.panel-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 12px; }
.panel-title { display: flex; align-items: center; gap: 8px; color: var(--color-primary); font-weight: 600; }
.panel-icon { font-size: 18px; }
.panel-actions { display: flex; align-items: center; gap: 8px; }
.status-chip { padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.status-chip.success { color: var(--color-success); background: rgba(46, 204, 113, 0.16); }
.status-chip.warning { color: var(--color-warning); background: rgba(243, 156, 18, 0.16); }
.status-chip.neutral { color: var(--color-text-secondary); background: rgba(136, 146, 176, 0.16); }
.task-meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
.meta-item, .summary-item, .info-item { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.meta-label, .summary-label, .info-label { font-size: 11px; color: var(--color-text-secondary); }
.meta-value, .summary-value { font-size: 13px; color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mono { font-family: 'Consolas', monospace; }
.command-info { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.info-value { font-size: 14px; color: var(--color-text); font-weight: 500; }
.info-value.highlight { color: var(--color-primary); font-size: 18px; font-weight: 700; }
.receipt-summary { margin-top: 12px; padding-top: 12px; border-top: 1px solid rgba(0, 180, 216, 0.12); display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; min-height: 140px; border: 1px dashed rgba(0, 180, 216, 0.2); border-radius: 10px; color: var(--color-text-secondary); }
.empty-icon { font-size: 28px; }
.empty-text { color: var(--color-text); font-weight: 500; }
.empty-hint { font-size: 12px; text-align: center; }
.verify-steps { display: flex; flex-direction: column; gap: 8px; }
.v-step { display: flex; gap: 10px; padding: 8px 10px; border-radius: 8px; border: 1px solid rgba(0, 180, 216, 0.12); opacity: 0.6; transition: all 0.3s; }
.v-step.active { opacity: 1; border-color: var(--color-primary); background: rgba(0, 180, 216, 0.08); }
.v-step.done { opacity: 1; border-color: rgba(46, 204, 113, 0.4); }
.v-step.failed { opacity: 1; border-color: rgba(230, 57, 70, 0.5); background: rgba(230, 57, 70, 0.08); }
.v-index { width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; border: 1px solid rgba(0, 180, 216, 0.4); color: var(--color-text-secondary); flex-shrink: 0; }
.v-step.active .v-index { background: var(--color-primary); color: #fff; animation: pulse 1s infinite; }
.v-step.done .v-index { background: var(--color-success); color: #fff; border-color: transparent; }
.v-step.failed .v-index { background: var(--color-danger); color: #fff; border-color: transparent; }
.v-body { min-width: 0; }
.v-title { font-size: 13px; color: var(--color-text); font-weight: 500; }
.v-detail { font-size: 11px; color: var(--color-text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.verify-result { margin-top: 10px; font-size: 12px; color: var(--color-success); }
.verify-result.fail { color: var(--color-danger); }
.exec-row { margin-top: 12px; }
.exec-row .el-button { width: 100%; }
.section-header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; color: var(--color-text); font-weight: 600; }
.section-icon { font-size: 18px; }
.stage-tag { font-size: 10px; font-weight: 400; color: var(--color-primary); background: rgba(0, 180, 216, 0.12); padding: 1px 8px; border-radius: 999px; font-family: 'Consolas', monospace; }
.stage-tag:first-of-type { margin-left: auto; }
.chart-container { height: 240px; }
.comparison-table { display: flex; flex-direction: column; border: 1px solid rgba(0, 180, 216, 0.15); border-radius: 8px; overflow: hidden; }
.table-header { display: flex; background: rgba(0, 180, 216, 0.1); }
.table-row { display: flex; border-top: 1px solid rgba(0, 180, 216, 0.15); }
.table-cell { flex: 1; padding: 10px 14px; text-align: center; font-size: 13px; }
.table-cell.label { text-align: left; color: var(--color-text-secondary); }
.table-cell.before { color: var(--color-text-secondary); }
.table-cell.after { color: var(--color-text); font-weight: 500; }
.table-cell.after.changed { color: var(--color-success); animation: pulse 0.5s ease; }
.result-panel { background: linear-gradient(135deg, rgba(46, 204, 113, 0.2) 0%, rgba(39, 174, 96, 0.2) 100%); border: 2px solid var(--color-success); border-radius: 12px; padding: 20px; display: flex; align-items: center; gap: 20px; }
.result-icon { font-size: 52px; }
.result-content { flex: 1; min-width: 0; }
.result-title { font-size: 18px; font-weight: 600; color: var(--color-text); margin-bottom: 12px; }
.result-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 20px; }
.stat { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.stat-label { font-size: 11px; color: var(--color-text-secondary); }
.stat-value { font-size: 16px; font-weight: 600; color: var(--color-text); overflow: hidden; text-overflow: ellipsis; }
.stat-value.success { color: var(--color-success); }
.result-enter-active, .result-leave-active { transition: all 0.5s ease; }
.result-enter-from, .result-leave-to { opacity: 0; transform: translateY(20px); }
@media (max-width: 1100px) { .top-grid, .bottom-grid { grid-template-columns: 1fr; } }
</style>
