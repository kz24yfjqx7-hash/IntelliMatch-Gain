<template>
  <div class="privacy-container">
    <div class="page-header">
      <h2 class="page-title">🔐 边缘隐私保护计算</h2>
      <p class="page-desc">联邦学习任务（POST /fl/tasks）：原始数据不出域，差分隐私加噪 + Top-k 稀疏后仅上传参数，每轮梯度哈希上链；进度经 WebSocket fl_progress 实时推送</p>
      <div class="current-node">
        <span class="node-label">当前节点：</span>
        <span class="node-name">{{ perspectiveStore.currentNodeInfo.name }}</span>
        <span class="node-status" :class="perspectiveStore.currentNodeInfo.status">{{ perspectiveStore.currentNodeInfo.status === 'online' ? '在线' : '告警' }}</span>
      </div>
      <div class="engine-status">
        <span class="status-dot"></span>
        <span class="status-text">训练引擎：算法服务 FedAvg（NumPy MLP 8-16-1）</span>
      </div>
    </div>

    <div class="privacy-content">
      <!-- 数据不出域流程动画 -->
      <div class="compute-section">
        <div class="section-header">
          <span class="section-icon">🔒</span>
          <span>数据不出域流程</span>
          <span class="flow-state" :class="{ running: isRunning }">{{ isRunning ? `第 ${task?.currentRound || 0}/${task?.totalRounds || 0} 轮训练中` : task ? STATUS_LABELS[task.status] || task.status : '等待创建任务' }}</span>
        </div>
        <DataFlowAnimation :running="isRunning" :active-index="flowIndex" />
      </div>

      <div class="main-grid">
        <!-- 左：创建表单 + 任务列表 -->
        <div class="left-col">
          <div class="card">
            <div class="card-title">🧪 新建联邦学习任务</div>
            <el-form label-position="top" size="small" class="fl-form">
              <el-form-item label="任务名称">
                <el-input v-model="form.name" placeholder="负荷预测联合建模" />
              </el-form-item>
              <el-form-item label="参与节点 nodeIds">
                <el-select v-model="form.nodeIds" multiple collapse-tags collapse-tags-tooltip style="width: 100%">
                  <el-option v-for="n in perspectiveStore.nodes" :key="n.id" :label="`${n.id} · ${n.name}`" :value="n.id" :disabled="n.status === 'offline'" />
                </el-select>
              </el-form-item>
              <el-form-item label="训练轮数 rounds">
                <el-slider v-model="form.rounds" :min="1" :max="30" :step="1" show-input :show-input-controls="false" />
              </el-form-item>
              <div class="form-row">
                <el-form-item>
                  <template #label><el-switch v-model="form.dp.enabled" size="small" /> 差分隐私 dp</template>
                  <div class="inline-inputs">
                    <span>ε</span><el-input-number v-model="form.dp.epsilon" :min="0.1" :max="10" :step="0.1" :precision="2" size="small" controls-position="right" :disabled="!form.dp.enabled" />
                    <span>δ</span><el-select v-model="form.dp.delta" size="small" :disabled="!form.dp.enabled" style="width: 90px">
                      <el-option v-for="d in [1e-3, 1e-4, 1e-5, 1e-6]" :key="d" :label="d.toExponential(0)" :value="d" />
                    </el-select>
                  </div>
                </el-form-item>
              </div>
              <el-form-item>
                <template #label><el-switch v-model="form.topk.enabled" size="small" /> Top-k 稀疏 topk</template>
                <el-slider v-model="form.topk.ratio" :min="0.01" :max="1" :step="0.01" :format-tooltip="v => `保留 ${(v * 100).toFixed(0)}%`" :disabled="!form.topk.enabled" />
              </el-form-item>
              <el-form-item label="投毒演示 simulatePoison（可选）">
                <el-select v-model="form.simulatePoison" clearable placeholder="不模拟" size="small" style="width: 100%">
                  <el-option v-for="id in form.nodeIds" :key="id" :label="`${id} 梯度翻转放大`" :value="id" />
                </el-select>
              </el-form-item>
            </el-form>
            <div class="form-actions">
              <el-button type="primary" :loading="creating" v-permission.disable="'algo:execute'" @click="createAndStart">🚀 创建并启动</el-button>
              <el-button type="danger" plain :disabled="!isRunning" :loading="cancelling" v-permission.disable="'algo:execute'" @click="cancelCurrent">⏹ 取消</el-button>
            </div>
          </div>

          <div class="card">
            <div class="card-title">📚 任务列表 <el-button link size="small" @click="loadTasks">刷新</el-button></div>
            <div class="task-list">
              <div v-for="t in taskList" :key="t.id" class="task-item" :class="[t.status, { active: t.id === task?.id }]" @click="selectTask(t.id)">
                <div class="ti-head"><span class="ti-id">{{ t.id }}</span><span class="ti-status" :class="t.status">{{ STATUS_LABELS[t.status] || t.status }}</span></div>
                <div class="ti-meta">{{ t.name }} · {{ t.currentRound }}/{{ t.totalRounds }} 轮{{ t.modelVersion ? ` · ${t.modelVersion}` : '' }}</div>
              </div>
              <div v-if="!taskList.length" class="empty">暂无任务</div>
            </div>
          </div>
        </div>

        <!-- 中：任务状态 / 曲线 -->
        <div class="mid-col">
          <div class="card status-card" v-if="task">
            <div class="status-head">
              <span class="task-id">{{ task.id }}</span>
              <span class="task-name">{{ task.name }}</span>
              <span class="chip" :class="task.status">{{ STATUS_LABELS[task.status] || task.status }}</span>
              <span v-if="task.modelVersion" class="chip model">模型 {{ task.modelVersion }}</span>
              <span class="trace">traceId {{ task.traceId || '--' }}</span>
            </div>
            <el-progress :percentage="progressPct" :stroke-width="10" :color="progressColor" :format="() => `${task.currentRound}/${task.totalRounds} 轮`" />
            <div class="node-chips">
              <span v-for="n in task.nodes || []" :key="n.nodeId" class="node-chip" :class="{ off: !n.joined }" :title="n.did">
                {{ n.nodeId }} · {{ n.samples }} 样本 · {{ n.joined ? '已加入' : '未加入' }}
              </span>
            </div>
            <div v-if="task.anomaly" class="anomaly">
              🚨 检测到异常：{{ task.anomaly.type === 'gradient_poisoning' ? `可疑梯度上传（${task.anomaly.nodeId}，第 ${task.anomaly.round} 轮）` : task.anomaly.type === 'privacy_budget_exhausted' ? `隐私预算耗尽（第 ${task.anomaly.round} 轮）` : task.anomaly.type }}
              <span class="anomaly-detail">{{ task.anomaly.detail }}</span>
            </div>
          </div>
          <div class="card empty-card" v-else>
            <span class="empty-icon">🧬</span>
            <span>创建任务或在左侧选择历史任务查看收敛过程</span>
          </div>

          <div class="card">
            <div class="card-title">📉 收敛曲线（loss / acc）<span class="ct-meta">{{ rounds.length }} 轮</span></div>
            <div ref="convRef" class="chart"></div>
          </div>
          <div class="card">
            <div class="card-title">📦 Top-k 压缩率实时曲线<span class="ct-meta">{{ task?.topk?.enabled ? `ratio ${task.topk.ratio}` : 'Top-k 关闭' }}</span></div>
            <div ref="compRef" class="chart small"></div>
          </div>
        </div>

        <!-- 右：隐私预算 + 轮次哈希 -->
        <div class="right-col">
          <div class="card budget-card" :class="{ over: budgetOver }">
            <div class="card-title">🛡️ 隐私预算仪表盘</div>
            <div ref="budgetRef" class="budget-ring"></div>
            <div class="budget-meta">
              <div><span class="bm-label">已消耗 ε</span><b :class="{ danger: budgetOver }">{{ fmtNumber(epsilonSpent, 3) }}</b></div>
              <div><span class="bm-label">总预算 ε</span><b>{{ task?.dp?.enabled ? fmtNumber(task.dp.epsilon, 2) : '关闭' }}</b></div>
              <div><span class="bm-label">δ</span><b>{{ task?.dp?.delta ? Number(task.dp.delta).toExponential(0) : '--' }}</b></div>
            </div>
            <div v-if="budgetOver" class="budget-warn">预算已超限，任务将被算法服务标记 privacy_budget_exhausted</div>
          </div>

          <div class="card">
            <div class="card-title">⛓️ 每轮梯度哈希上链<span class="ct-meta">{{ rounds.length }} 条存证</span></div>
            <div class="round-list">
              <div v-for="r in roundsDesc" :key="r.round" class="round-item">
                <span class="r-no">R{{ r.round }}</span>
                <span class="r-hash mono" :title="r.gradientHash">{{ shortHash(r.gradientHash, 8, 6) }}</span>
                <span class="r-ev mono">{{ r.evidenceId || '--' }}</span>
                <span class="r-loss">loss {{ fmtNumber(r.loss, 4) }}</span>
              </div>
              <div v-if="!rounds.length" class="empty">训练开始后逐轮展示</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 本地单次演示工具（纯 JS） -->
      <div class="gradient-section">
        <div class="section-header">
          <span class="section-icon">🧰</span>
          <span>本地演示：单次差分加噪 & Top-k 稀疏（浏览器内纯 JS，真实训练走上方 FL 任务）</span>
          <div class="demo-ctrl">
            <span>ε</span><el-input-number v-model="demo.epsilon" :min="0.1" :max="5" :step="0.1" size="small" controls-position="right" />
            <span>k</span><el-input-number v-model="demo.k" :min="1" :max="36" :step="1" size="small" controls-position="right" />
            <el-button size="small" type="primary" @click="runDemo">🎲 重新生成</el-button>
          </div>
        </div>
        <div class="demo-grid">
          <div class="demo-compare">
            <div class="data-item" v-for="item in demoRows" :key="item.field">
              <span class="data-field">{{ item.field }}</span>
              <span class="data-value">{{ item.before }}</span>
              <span class="arrow">→</span>
              <span class="data-value noisy">{{ item.after }}</span>
            </div>
            <div class="demo-meta">拉普拉斯尺度 b = Δ/ε = {{ fmtNumber(1 / demo.epsilon, 3) }}；高斯 σ = {{ fmtNumber(demo.sigma, 3) }}</div>
          </div>
          <div class="matrix-container">
            <div class="matrix-grid">
              <div v-for="(cell, index) in demo.matrix" :key="index" class="matrix-cell" :class="{ sparse: cell.sparse }">{{ cell.sparse ? '' : cell.value.toFixed(3) }}</div>
            </div>
            <div class="matrix-info">
              <div class="info-item"><span class="info-label">L1 范数</span><span class="info-value">{{ fmtNumber(demo.norms.l1Norm, 4) }}</span></div>
              <div class="info-item"><span class="info-label">L2 范数</span><span class="info-value">{{ fmtNumber(demo.norms.l2Norm, 4) }}</span></div>
              <div class="info-item"><span class="info-label">L∞ 范数</span><span class="info-value">{{ fmtNumber(demo.norms.infinityNorm, 4) }}</span></div>
              <div class="info-item"><span class="info-label">压缩率</span><span class="info-value">{{ fmtNumber(demo.compression, 1) }}%</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * 边缘隐私保护计算：接 FL 任务 API（create → start → WS fl_progress 增量 → getFlTask 收尾），
 * 隐私预算环形仪表盘、loss/acc 收敛曲线、Top-k 压缩率曲线、每轮 gradientHash/evidenceId 列表、数据不出域动画；
 * 页面底部保留 scientificCompute 的纯 JS 单次加噪 / Top-k 演示。
 */
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import DataFlowAnimation from '@/components/legacy/DataFlowAnimation.vue'
import { useLogStore } from '@/stores/logs'
import { usePerspectiveStore } from '@/stores/perspective'
import { createFlTask, startFlTask, cancelFlTask, getFlTask, listFlTasks } from '@/api/fl'
import { wsClient, WS_TYPES } from '@/api/ws'
import { scientificCompute } from '@/services/scientificCompute'
import { fmtNumber, shortHash } from '@/utils/format'

const logStore = useLogStore()
const perspectiveStore = usePerspectiveStore()

const STATUS_LABELS = { created: '已创建', running: '训练中', success: '已完成', failed: '失败', cancelled: '已取消' }

const form = reactive({
  name: '',
  nodeIds: [],
  rounds: 10,
  dp: { enabled: true, epsilon: 1.0, delta: 1e-5 },
  topk: { enabled: true, ratio: 0.1 },
  simulatePoison: ''
})
const creating = ref(false)
const cancelling = ref(false)
const task = ref(null)
const taskList = ref([])
const flowIndex = ref(-1)

const convRef = ref(null)
const compRef = ref(null)
const budgetRef = ref(null)
let convChart = null
let compChart = null
let budgetChart = null
let offWs = null
let pollTimer = null
let flowTimer = null

const rounds = computed(() => task.value?.rounds || [])
const roundsDesc = computed(() => rounds.value.slice().reverse())
const isRunning = computed(() => task.value?.status === 'running')
const progressPct = computed(() => task.value?.totalRounds ? Math.round((task.value.currentRound / task.value.totalRounds) * 100) : 0)
const progressColor = computed(() => task.value?.status === 'failed' || task.value?.status === 'cancelled' ? '#E63946' : task.value?.status === 'success' ? '#2ECC71' : '#00B4D8')
const epsilonSpent = computed(() => Number(task.value?.dp?.epsilonSpent ?? rounds.value[rounds.value.length - 1]?.epsilonSpent ?? 0))
const budgetOver = computed(() => Boolean(task.value?.dp?.enabled) && epsilonSpent.value > Number(task.value.dp.epsilon) + 1e-6)

/* ---------- 任务操作 ---------- */
async function loadTasks() {
  try {
    const data = await listFlTasks({ size: 20 })
    taskList.value = data?.items || []
  } catch { taskList.value = [] }
}

async function selectTask(id) {
  try {
    task.value = await getFlTask(id)
    logStore.addLog(`查看联邦任务 ${id}（${STATUS_LABELS[task.value.status] || task.value.status}，${task.value.rounds?.length || 0} 轮）`, 'INFO', 'EDGE', { traceId: task.value.traceId })
    nextTick(renderAll)
    if (task.value.status === 'running') startPolling()
  } catch { /* request.js 已提示 */ }
}

async function createAndStart() {
  if (!form.nodeIds.length) { ElMessage.warning('请选择参与节点'); return }
  creating.value = true
  try {
    const payload = {
      name: form.name || `负荷预测联合建模-${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`,
      nodeIds: form.nodeIds,
      rounds: form.rounds,
      dp: { enabled: form.dp.enabled, epsilon: form.dp.epsilon, delta: form.dp.delta },
      topk: { enabled: form.topk.enabled, ratio: form.topk.ratio }
    }
    if (form.simulatePoison) payload.simulatePoison = form.simulatePoison
    const created = await createFlTask(payload)
    logStore.addLog(`创建联邦任务 ${created.id}：${payload.nodeIds.length} 节点 / ${payload.rounds} 轮 / ε=${payload.dp.epsilon} / Top-k ${payload.topk.ratio}`, 'INFO', 'EDGE', { traceId: created.traceId })
    // 复用创建时的 traceId，使「创建 → 启动 → 每轮上链 → 完成」在审计追踪里是一条链
    await startFlTask(created.id, created.traceId)
    logStore.addLog(`联邦任务 ${created.id} 已启动，等待 fl_progress 推送`, 'INFO', 'EDGE', { traceId: created.traceId })
    task.value = await getFlTask(created.id)
    await loadTasks()
    nextTick(renderAll)
    startPolling()
  } catch (e) {
    logStore.addLog(`联邦任务创建/启动失败：${e?.message || e}`, 'ERROR', 'EDGE', { traceId: e?.traceId })
  } finally {
    creating.value = false
  }
}

async function cancelCurrent() {
  if (!task.value) return
  cancelling.value = true
  try {
    await cancelFlTask(task.value.id, task.value.traceId)
    logStore.addLog(`联邦任务 ${task.value.id} 已取消（完成 ${task.value.currentRound} 轮）`, 'WARN', 'EDGE')
    task.value = await getFlTask(task.value.id)
    await loadTasks()
    nextTick(renderAll)
  } catch { /* 已提示 */ } finally { cancelling.value = false }
}

/** 兜底轮询：WS 断开时也能看到进度；任务结束后自动停止 */
function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    if (!task.value || task.value.status !== 'running') { stopPolling(); return }
    try {
      const t = await getFlTask(task.value.id)
      if ((t.rounds?.length || 0) >= rounds.value.length) { task.value = t; renderAll() }
      if (t.status !== 'running') { onFinished(t); stopPolling() }
    } catch { /* 忽略 */ }
  }, 3000)
}
function stopPolling() { if (pollTimer) clearInterval(pollTimer); pollTimer = null }

function onFinished(t) {
  task.value = t
  loadTasks()
  const last = t.rounds?.[t.rounds.length - 1]
  logStore.addLog(`联邦任务 ${t.id} ${STATUS_LABELS[t.status]}${t.modelVersion ? `，产出模型 ${t.modelVersion}` : ''}${last ? `，最终 loss=${fmtNumber(last.loss, 4)} acc=${fmtNumber(last.acc, 3)}` : ''}`, t.status === 'success' ? 'INFO' : 'WARN', 'EDGE', { traceId: t.traceId })
  renderAll()
}

/** WS fl_progress：按 taskId 过滤后增量追加轮次 */
function onProgress(payload, msg) {
  if (!task.value || payload?.taskId !== task.value.id) return
  if (task.value.status !== 'running') task.value.status = 'running'
  const exists = rounds.value.find(r => r.round === payload.round)
  if (!exists) {
    task.value.rounds = [...rounds.value, { round: payload.round, loss: payload.loss, acc: payload.acc, compressionRatio: payload.compressionRatio, epsilonSpent: payload.epsilonSpent, gradientHash: payload.gradientHash || '', evidenceId: payload.evidenceId || '' }]
  }
  task.value.currentRound = payload.round
  task.value.totalRounds = payload.totalRounds || task.value.totalRounds
  if (task.value.dp) task.value.dp.epsilonSpent = payload.epsilonSpent
  if (task.value.topk) task.value.topk.compressionRatio = payload.compressionRatio
  logStore.addLog(`[${task.value.id}] 第 ${payload.round}/${payload.totalRounds} 轮：loss=${fmtNumber(payload.loss, 4)} acc=${fmtNumber(payload.acc, 3)} 压缩率 ${fmtNumber(payload.compressionRatio, 1)}% ε=${fmtNumber(payload.epsilonSpent, 3)}`, 'INFO', 'EDGE', { traceId: msg?.traceId })
  renderAll()
  if (payload.round >= payload.totalRounds) {
    // 最后一轮：稍后拉一次详情拿 modelVersion / anomaly
    setTimeout(async () => { try { onFinished(await getFlTask(task.value.id)) } catch { /* 忽略 */ } }, 800)
  }
}

/* ---------- 图表 ---------- */
const axisStyle = { axisLabel: { color: '#8892B0', fontSize: 10 }, axisLine: { lineStyle: { color: '#243447' } }, splitLine: { lineStyle: { color: '#243447', type: 'dashed' } } }
const tooltipStyle = { trigger: 'axis', backgroundColor: 'rgba(13, 27, 42, 0.95)', borderColor: '#00B4D8', textStyle: { color: '#fff', fontSize: 11 } }

function renderConv() {
  if (!convRef.value) return
  convChart = convChart || echarts.init(convRef.value)
  const r = rounds.value
  convChart.setOption({
    backgroundColor: 'transparent',
    tooltip: tooltipStyle,
    legend: { data: ['loss', 'acc'], textStyle: { color: '#8892B0', fontSize: 10 }, top: 0 },
    grid: { left: 44, right: 44, top: 28, bottom: 26 },
    xAxis: { type: 'category', data: r.map(x => `R${x.round}`), ...axisStyle },
    yAxis: [
      { type: 'value', name: 'loss', nameTextStyle: { color: '#8892B0', fontSize: 10 }, ...axisStyle },
      { type: 'value', name: 'acc', min: 0, max: 1, nameTextStyle: { color: '#8892B0', fontSize: 10 }, ...axisStyle, splitLine: { show: false } }
    ],
    series: [
      { name: 'loss', type: 'line', smooth: true, symbolSize: 6, data: r.map(x => x.loss), lineStyle: { color: '#E63946', width: 2 }, itemStyle: { color: '#E63946' }, areaStyle: { color: 'rgba(230, 57, 70, 0.12)' } },
      { name: 'acc', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 6, data: r.map(x => x.acc), lineStyle: { color: '#2ECC71', width: 2 }, itemStyle: { color: '#2ECC71' } }
    ]
  })
}

function renderComp() {
  if (!compRef.value) return
  compChart = compChart || echarts.init(compRef.value)
  const r = rounds.value
  compChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { ...tooltipStyle, valueFormatter: v => `${fmtNumber(v, 2)}%` },
    grid: { left: 44, right: 16, top: 16, bottom: 26 },
    xAxis: { type: 'category', data: r.map(x => `R${x.round}`), ...axisStyle },
    yAxis: { type: 'value', min: 0, max: 100, ...axisStyle },
    series: [{ type: 'line', step: 'middle', symbolSize: 5, data: r.map(x => x.compressionRatio), lineStyle: { color: '#00B4D8', width: 2 }, itemStyle: { color: '#00B4D8' }, areaStyle: { color: 'rgba(0, 180, 216, 0.15)' }, label: { show: r.length <= 12, position: 'top', color: '#8892B0', fontSize: 9, formatter: p => `${fmtNumber(p.value, 1)}%` } }]
  })
}

function renderBudget() {
  if (!budgetRef.value) return
  budgetChart = budgetChart || echarts.init(budgetRef.value)
  const total = Number(task.value?.dp?.epsilon) || 0
  const spent = epsilonSpent.value
  const enabled = Boolean(task.value?.dp?.enabled)
  const pct = enabled && total > 0 ? Math.min(100, (spent / total) * 100) : 0
  const color = !enabled ? '#8892B0' : budgetOver.value ? '#E63946' : pct >= 80 ? '#F39C12' : '#2ECC71'
  budgetChart.setOption({
    backgroundColor: 'transparent',
    series: [{
      type: 'gauge', startAngle: 90, endAngle: -270, radius: '92%',
      pointer: { show: false },
      progress: { show: true, overlap: false, roundCap: true, clip: false, itemStyle: { color } },
      axisLine: { lineStyle: { width: 16, color: [[1, 'rgba(0, 180, 216, 0.12)']] } },
      splitLine: { show: false }, axisTick: { show: false }, axisLabel: { show: false },
      detail: { valueAnimation: true, offsetCenter: [0, '-6%'], fontSize: 26, fontWeight: 'bold', color, formatter: v => enabled ? `${v.toFixed(0)}%` : 'DP 关闭' },
      title: { offsetCenter: [0, '30%'], color: '#8892B0', fontSize: 11 },
      data: [{ value: pct, name: enabled ? `ε ${fmtNumber(spent, 3)} / ${fmtNumber(total, 2)}` : '未启用差分隐私' }]
    }],
    animationDuration: 600
  })
}

function renderAll() { renderConv(); renderComp(); renderBudget() }

/* ---------- 本地演示 ---------- */
const demo = reactive({ epsilon: 0.5, k: 8, matrix: [], norms: {}, compression: 0, sigma: 0 })
const demoRows = ref([])
async function runDemo() {
  const d = perspectiveStore.currentNodeData
  const values = [d.pvOutput, d.storageOutput, d.load]
  const lap = await scientificCompute.applyDifferentialPrivacy(values, demo.epsilon, 1.0)
  const gau = await scientificCompute.applyGaussianPrivacy([d.soc], demo.epsilon, 1e-5)
  demo.sigma = gau.sigma
  const area = `节点${String(perspectiveStore.currentNode).split('-')[1] || ''}区域`
  demoRows.value = [
    { field: '光伏发电功率', before: `${fmtNumber(values[0])} kW`, after: `${fmtNumber(lap.noisyData[0], 3)} kW` },
    { field: '储能充放电功率', before: `${fmtNumber(values[1])} kW`, after: `${fmtNumber(lap.noisyData[1], 3)} kW` },
    { field: '负荷曲线数据', before: `${fmtNumber(values[2])} kW`, after: `${fmtNumber(lap.noisyData[2], 3)} kW` },
    { field: '储能 SOC（高斯）', before: `${fmtNumber(d.soc)}%`, after: `${fmtNumber(gau.noisyData[0], 3)}%` },
    { field: '用户位置信息', before: area, after: '*** 已匿名/脱敏 ***' }
  ]
  const g = scientificCompute.randomGradients(36, 0.5)
  const topk = await scientificCompute.topkGradientSparsity(g, demo.k)
  demo.norms = await scientificCompute.calculateGradientNorm(g)
  demo.compression = topk.compressionRatio
  const keep = new Set(topk.keptIndices)
  demo.matrix = g.map((v, i) => ({ value: v, sparse: !keep.has(i) }))
}
watch(() => [demo.epsilon, demo.k], runDemo)
watch(() => perspectiveStore.currentNode, runDemo)

/** 流程动画环节轮转：训练中每 1.2s 前进一格 */
watch(isRunning, run => {
  clearInterval(flowTimer)
  if (run) {
    flowIndex.value = 0
    flowTimer = setInterval(() => { flowIndex.value = (flowIndex.value + 1) % 5 }, 1200)
  } else {
    flowIndex.value = task.value?.status === 'success' ? 4 : -1
  }
}, { immediate: true })

function onResize() { convChart?.resize(); compChart?.resize(); budgetChart?.resize() }

onMounted(async () => {
  logStore.addLog(`进入边缘隐私保护计算模块 - ${perspectiveStore.currentNodeInfo.name}`, 'INFO', 'EDGE')
  form.nodeIds = perspectiveStore.nodes.filter(n => n.status !== 'offline').map(n => n.id)
  offWs = wsClient.on(WS_TYPES.FL_PROGRESS, onProgress)
  await runDemo()
  await loadTasks()
  const running = taskList.value.find(t => t.status === 'running')
  if (running) await selectTask(running.id)
  else if (taskList.value[0]) await selectTask(taskList.value[0].id)
  renderAll()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  offWs && offWs()
  stopPolling()
  clearInterval(flowTimer)
  convChart?.dispose(); compChart?.dispose(); budgetChart?.dispose()
  window.removeEventListener('resize', onResize)
})
</script>

<style scoped>
.privacy-container { height: 100%; display: flex; flex-direction: column; }
.page-header { margin-bottom: 16px; }
.page-title { font-size: 24px; color: var(--color-text); margin-bottom: 8px; }
.page-desc { color: var(--color-text-secondary); font-size: 14px; }
.current-node { display: inline-flex; align-items: center; gap: 8px; margin-top: 12px; padding: 8px 16px; background: rgba(0, 180, 216, 0.1); border-radius: 6px; border: 1px solid rgba(0, 180, 216, 0.2); }
.node-label { color: var(--color-text-secondary); font-size: 13px; }
.node-name { color: var(--color-primary); font-weight: 600; font-size: 14px; }
.node-status { font-size: 12px; padding: 2px 8px; border-radius: 4px; }
.node-status.online { background: rgba(46, 204, 113, 0.2); color: var(--color-success); }
.node-status.warning { background: rgba(243, 156, 18, 0.2); color: var(--color-warning); }
.engine-status { display: inline-flex; align-items: center; gap: 6px; margin-left: 12px; padding: 4px 12px; background: rgba(46, 204, 113, 0.15); border-radius: 4px; font-size: 12px; color: var(--color-success); }
.status-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-success); animation: pulse 2s infinite; }
.privacy-content { flex: 1; display: flex; flex-direction: column; gap: 16px; }
.compute-section, .gradient-section, .card { background: transparent; border-radius: 12px; padding: 16px; border: 1px solid rgba(0, 180, 216, 0.15); }
.section-header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; color: var(--color-primary); font-size: 15px; font-weight: 600; }
.section-icon { font-size: 20px; }
.flow-state { margin-left: auto; font-size: 12px; font-weight: 400; color: var(--color-text-secondary); padding: 2px 10px; border-radius: 999px; border: 1px solid rgba(136, 146, 176, 0.3); }
.flow-state.running { color: var(--color-success); border-color: rgba(46, 204, 113, 0.4); animation: pulse 1.5s infinite; }
.main-grid { display: grid; grid-template-columns: 300px minmax(0, 1fr) 280px; gap: 16px; }
.left-col, .mid-col, .right-col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.card-title { display: flex; align-items: center; gap: 8px; color: var(--color-primary); font-weight: 600; font-size: 14px; margin-bottom: 10px; }
.ct-meta { margin-left: auto; font-size: 11px; font-weight: 400; color: var(--color-text-secondary); }
.fl-form :deep(.el-form-item) { margin-bottom: 10px; }
.fl-form :deep(.el-form-item__label) { color: var(--color-text-secondary); font-size: 12px; padding-bottom: 2px; }
.inline-inputs { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--color-text-secondary); }
.inline-inputs :deep(.el-input-number) { width: 100px; }
.form-actions { display: flex; gap: 8px; }
.task-list { display: flex; flex-direction: column; gap: 6px; max-height: 260px; overflow: auto; }
.task-item { padding: 8px 10px; border-radius: 8px; border: 1px solid rgba(0, 180, 216, 0.15); cursor: pointer; transition: all 0.2s; }
.task-item:hover, .task-item.active { border-color: var(--color-primary); background: rgba(0, 180, 216, 0.08); }
.ti-head { display: flex; justify-content: space-between; align-items: center; }
.ti-id { font-family: 'Consolas', monospace; font-size: 12px; color: var(--color-text); }
.ti-status, .chip { font-size: 10px; padding: 1px 8px; border-radius: 999px; font-weight: 600; }
.ti-status.running, .chip.running { color: var(--color-primary); background: rgba(0, 180, 216, 0.15); }
.ti-status.success, .chip.success { color: var(--color-success); background: rgba(46, 204, 113, 0.15); }
.ti-status.failed, .ti-status.cancelled, .chip.failed, .chip.cancelled { color: var(--color-danger); background: rgba(230, 57, 70, 0.15); }
.ti-status.created, .chip.created { color: var(--color-text-secondary); background: rgba(136, 146, 176, 0.15); }
.chip.model { color: var(--color-warning); background: rgba(243, 156, 18, 0.15); }
.ti-meta { font-size: 11px; color: var(--color-text-secondary); margin-top: 2px; }
.empty { font-size: 12px; color: var(--color-text-secondary); text-align: center; padding: 12px; }
.status-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.task-id { font-family: 'Consolas', monospace; font-weight: 600; color: var(--color-text); }
.task-name { color: var(--color-text-secondary); font-size: 13px; }
.trace { margin-left: auto; font-family: 'Consolas', monospace; font-size: 11px; color: var(--color-text-secondary); }
.node-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.node-chip { font-size: 11px; padding: 2px 8px; border-radius: 6px; background: rgba(46, 204, 113, 0.1); color: var(--color-success); border: 1px solid rgba(46, 204, 113, 0.3); }
.node-chip.off { color: var(--color-text-secondary); background: rgba(136, 146, 176, 0.1); border-color: rgba(136, 146, 176, 0.3); }
.anomaly { margin-top: 10px; padding: 8px 12px; border-radius: 8px; background: rgba(230, 57, 70, 0.12); border: 1px solid rgba(230, 57, 70, 0.4); color: var(--color-danger); font-size: 12px; font-weight: 600; }
.anomaly-detail { display: block; font-weight: 400; color: var(--color-text-secondary); margin-top: 2px; }
.empty-card { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; min-height: 110px; color: var(--color-text-secondary); font-size: 13px; }
.empty-icon { font-size: 28px; }
.chart { height: 230px; }
.chart.small { height: 150px; }
.budget-card { transition: border-color 0.3s; }
.budget-card.over { border-color: rgba(230, 57, 70, 0.7); box-shadow: 0 0 18px rgba(230, 57, 70, 0.25); }
.budget-ring { height: 180px; }
.budget-meta { display: flex; justify-content: space-around; text-align: center; }
.budget-meta div { display: flex; flex-direction: column; gap: 2px; }
.bm-label { font-size: 10px; color: var(--color-text-secondary); }
.budget-meta b { font-family: 'Consolas', monospace; color: var(--color-text); font-size: 13px; }
.budget-meta b.danger { color: var(--color-danger); }
.budget-warn { margin-top: 8px; font-size: 11px; color: var(--color-danger); text-align: center; }
.round-list { display: flex; flex-direction: column; gap: 4px; max-height: 300px; overflow: auto; }
.round-item { display: grid; grid-template-columns: 32px 1fr 64px 76px; gap: 6px; font-size: 11px; padding: 4px 6px; border-radius: 6px; background: rgba(0, 180, 216, 0.05); align-items: center; }
.r-no { color: var(--color-primary); font-weight: 600; }
.mono { font-family: 'Consolas', monospace; }
.r-hash { color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.r-ev { color: var(--color-success); }
.r-loss { color: var(--color-text-secondary); text-align: right; }
.demo-ctrl { margin-left: auto; display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 400; color: var(--color-text-secondary); }
.demo-ctrl :deep(.el-input-number) { width: 90px; }
.demo-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr); gap: 20px; }
.demo-compare .data-item { display: grid; grid-template-columns: 1.2fr 1fr 20px 1fr; gap: 8px; padding: 8px 0; border-bottom: 1px solid rgba(0, 180, 216, 0.12); font-size: 12px; align-items: center; }
.data-field { color: var(--color-text-secondary); }
.data-value { font-family: 'Consolas', monospace; color: var(--color-text); }
.data-value.noisy { color: var(--color-primary); }
.arrow { color: var(--color-text-secondary); text-align: center; }
.demo-meta { margin-top: 8px; font-size: 11px; color: var(--color-text-secondary); font-family: 'Consolas', monospace; }
.matrix-container { display: flex; gap: 16px; align-items: flex-start; }
.matrix-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 3px; flex: 1; max-width: 340px; }
.matrix-cell { aspect-ratio: 1; background: rgba(0, 180, 216, 0.15); border-radius: 4px; display: flex; align-items: center; justify-content: center; font-size: 9px; font-family: 'Consolas', monospace; color: var(--color-text); transition: all 0.3s; }
.matrix-cell.sparse { background: rgba(230, 57, 70, 0.2); color: transparent; }
.matrix-info { display: flex; flex-direction: column; gap: 6px; padding: 10px; background: rgba(0, 180, 216, 0.08); border-radius: 8px; min-width: 150px; }
.info-item { display: flex; justify-content: space-between; gap: 10px; font-size: 12px; }
.info-label { color: var(--color-text-secondary); }
.info-value { font-family: 'Consolas', monospace; color: var(--color-primary); font-weight: 600; }
@media (max-width: 1300px) { .main-grid { grid-template-columns: 280px minmax(0, 1fr); } .right-col { grid-column: 1 / -1; flex-direction: row; } .right-col .card { flex: 1; } }
@media (max-width: 1000px) { .main-grid { grid-template-columns: 1fr; } .demo-grid { grid-template-columns: 1fr; } }
</style>
