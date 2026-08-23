<template>
  <div class="aggregate-container">
    <div class="page-header">
      <div>
        <h2 class="page-title">🔄 云端聚合与调度</h2>
        <p class="page-desc">联邦模型聚合 → DQN 生成调度策略 → DeepSeek 解释 → DID 签名下发 → 终端回执（接 /fl、/dispatch、/ai API，WebSocket dispatch_progress 实时推进）</p>
      </div>
      <div class="header-actions">
        <el-button type="success" :disabled="!remote?.strategy" @click="downloadReport">📄 生成分析报告</el-button>
        <el-button type="primary" :loading="running" @click="generateStrategy">{{ remote ? '🔁 重新生成调度策略' : '🚀 生成调度策略' }}</el-button>
      </div>
    </div>

    <!-- 越权拦截横幅 -->
    <transition name="fade">
      <div v-if="denyBanner" class="deny-banner">
        <span class="deny-icon">⛔</span>
        <div class="deny-body">
          <div class="deny-title">下发请求已被后端拦截（code {{ denyBanner.code }}）</div>
          <div class="deny-msg">{{ denyBanner.message }} · traceId <span class="mono">{{ denyBanner.traceId || '--' }}</span> · 已写入 high 风险审计日志，管理员将收到 R01 告警</div>
        </div>
        <el-button size="small" text @click="denyBanner = null">关闭</el-button>
      </div>
    </transition>

    <div class="aggregate-content">
      <div class="left-panel">
        <!-- 阶段流转 -->
        <div class="main-area">
          <div class="status-panel">
            <div v-for="(s, i) in STAGES" :key="s.key" class="status-item" :class="{ active: stageIndex >= i, completed: stageIndex > i || (stageIndex === i && s.key === 'acked'), failed: failedStage === s.key }">
              <div class="status-step">
                <span class="step-icon">{{ failedStage === s.key ? '❌' : stageIndex > i || (stageIndex === i && s.key === 'acked') ? '✅' : stageIndex === i ? '🔄' : '⏳' }}</span>
                <span class="step-text">{{ s.title }}</span>
              </div>
              <div class="status-detail">
                <span class="detail-value">{{ stageLog[s.key]?.at ? fmtTime(stageLog[s.key].at, 'HH:mm:ss') : '--' }}</span>
                <span class="detail-label" :title="stageLog[s.key]?.detail || s.desc">{{ stageLog[s.key]?.detail || s.desc }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="task-grid">
          <!-- ① FL 任务状态 -->
          <div class="task-card">
            <div class="card-header">
              <span class="title-icon">🧬</span>
              <span class="card-title">联邦学习任务状态</span>
              <el-select v-model="flTaskId" size="small" class="fl-select" placeholder="选择任务" @change="loadFlTask">
                <el-option v-for="t in flTasks" :key="t.id" :label="`${t.id} · ${FL_STATUS[t.status] || t.status}`" :value="t.id" />
              </el-select>
            </div>
            <template v-if="flTask">
              <div class="summary-list">
                <div class="summary-row"><span class="summary-key">任务</span><span class="summary-value mono">{{ flTask.id }} · {{ flTask.name }}</span></div>
                <div class="summary-row"><span class="summary-key">状态 / 轮次</span><span class="summary-value"><span class="chip" :class="flTask.status">{{ FL_STATUS[flTask.status] || flTask.status }}</span> {{ flTask.currentRound }}/{{ flTask.totalRounds }}</span></div>
                <div class="summary-row"><span class="summary-key">模型版本</span><span class="summary-value">{{ flTask.modelVersion || '训练完成后生成' }}</span></div>
                <div class="summary-row"><span class="summary-key">DP / Top-k</span><span class="summary-value">ε {{ fmtNumber(flTask.dp?.epsilonSpent, 3) }}/{{ fmtNumber(flTask.dp?.epsilon, 2) }} · 压缩 {{ fmtNumber(flTask.topk?.compressionRatio, 1) }}%</span></div>
              </div>
              <el-table :data="flTask.nodes || []" size="small" class="mini-table">
                <el-table-column prop="nodeId" label="节点" width="80" />
                <el-table-column label="DID" min-width="150">
                  <template #default="{ row }"><span class="mono" :title="row.did">{{ shortDid(row.did) }}</span></template>
                </el-table-column>
                <el-table-column label="加入" width="60">
                  <template #default="{ row }"><span :class="row.joined ? 'ok' : 'no'">{{ row.joined ? '是' : '否' }}</span></template>
                </el-table-column>
                <el-table-column prop="samples" label="样本" width="70" />
              </el-table>
              <div ref="flChartRef" class="fl-chart"></div>
            </template>
            <div v-else class="empty-state compact"><span class="empty-icon">🧬</span><span>暂无联邦任务，请先在「边缘隐私保护计算」创建</span></div>
            <div class="models-block">
              <div class="models-title">模型版本 <span class="muted">（GET /fl/models）</span></div>
              <div v-for="m in flModels" :key="m.version" class="model-row">
                <span class="mono">{{ m.version }}</span>
                <span class="muted">{{ m.taskId }} · loss {{ fmtNumber(m.loss, 4) }} · acc {{ fmtNumber(m.acc, 3) }}</span>
                <span class="chip" :class="m.status === 'published' ? 'success' : 'created'">{{ m.status === 'published' ? '已发布' : '草稿' }}</span>
                <el-button v-if="m.status !== 'published'" v-permission="'algo:execute'" size="small" type="primary" link :loading="publishing === m.version" @click="publish(m.version)">发布</el-button>
              </div>
              <div v-if="!flModels.length" class="muted">暂无模型</div>
            </div>
          </div>

          <!-- ② DQN 调度策略 -->
          <div class="task-card">
            <div class="card-header">
              <span class="title-icon">🧠</span>
              <span class="card-title">DQN 调度策略</span>
              <span class="status-chip" :class="remoteStatusClass">{{ remote ? DP_STATUS[remote.status] || remote.status : '未生成' }}</span>
            </div>
            <div class="gen-form">
              <el-select v-model="selectedNodeIds" multiple collapse-tags size="small" style="flex: 1" placeholder="参与节点">
                <el-option v-for="n in perspectiveStore.nodes" :key="n.id" :label="n.id" :value="n.id" />
              </el-select>
              <el-time-select v-model="timeWindow.start" size="small" start="00:00" step="01:00" end="23:00" placeholder="开始" style="width: 100px" />
              <span class="muted">~</span>
              <el-time-select v-model="timeWindow.end" size="small" start="01:00" step="01:00" end="23:59" placeholder="结束" style="width: 100px" />
            </div>
            <template v-if="remote?.strategy">
              <el-table :data="remote.strategy.actions" size="small" class="mini-table" :row-class-name="({ row }) => `act-${row.action}`">
                <el-table-column prop="nodeId" label="节点" width="80" />
                <el-table-column label="动作" width="80">
                  <template #default="{ row }"><span class="act" :class="row.action">{{ ACTION_CN[row.action] || row.action }}</span></template>
                </el-table-column>
                <el-table-column label="功率" width="80">
                  <template #default="{ row }">{{ fmtNumber(row.powerKw, 1) }} kW</template>
                </el-table-column>
                <el-table-column label="Q 值" width="70">
                  <template #default="{ row }">{{ fmtNumber(row.qValue, 2) }}</template>
                </el-table-column>
                <el-table-column prop="reason" label="原因" min-width="120" />
              </el-table>
              <div class="kv-row">
                <span>总回报 <b>{{ fmtNumber(remote.strategy.totalReward, 1) }}</b></span>
                <span>时间窗 <b class="mono">{{ remote.strategy.timeWindow || remote.timeWindow }}</b></span>
                <span>存证 <b class="mono">{{ remote.evidenceId || '--' }}</b></span>
                <span>traceId <b class="mono">{{ remote.traceId || '--' }}</b></span>
              </div>
              <div ref="qChartRef" class="q-chart"></div>
              <div class="constraints">
                <div class="c-title">约束校验 constraintsChecked：SOC {{ remote.constraintsChecked?.socMin ?? 20 }}~{{ remote.constraintsChecked?.socMax ?? 95 }}% · 单节点 ≤ {{ remote.constraintsChecked?.maxPowerKw ?? 30 }} kW</div>
                <div v-if="violations.length" class="c-list">
                  <div v-for="(v, i) in violations" :key="i" class="c-item">⚠️ {{ v.nodeId }} · {{ v.constraint || v.rule }} · {{ v.detail }}</div>
                </div>
                <div v-else class="c-ok">✅ 全部动作满足约束，无越限修正</div>
              </div>
            </template>
            <div v-else class="empty-state compact"><span class="empty-icon">{{ running ? '🌀' : '⏳' }}</span><span>{{ running ? 'DQN 推理中…' : '点击「生成调度策略」创建任务并运行 DQN' }}</span></div>
          </div>

          <!-- ③ 解释 + 签名下发 -->
          <div class="task-card wide">
            <div class="card-header">
              <span class="title-icon">🪄</span>
              <span class="card-title">策略解释与签名下发</span>
              <SourceBadge v-if="remote?.explanationSource" :source="remote.explanationSource" prefix="解释来源：" />
            </div>
            <div class="reasoning-block">
              <div class="reasoning-summary">{{ remote?.explanation || '生成策略后，这里展示 DeepSeek 对 DQN 动作的中文解释（断网时自动降级为缓存/规则模板并如实标注来源）' }}</div>
            </div>
            <div class="issue-row">
              <el-input v-model="signature" size="small" :placeholder="USE_MOCK ? '签发者 SM2 签名（演示：自动生成 sig:sha256…，输入 invalid 可演示 1004）' : '签发者 SM2 签名（留空 = 后端用托管私钥代签 signPayload；输入 invalid 可演示 1004）'" class="sig-input">
                <template #prepend>signature</template>
                <template #append><el-button @click="signature = dispatchStore.buildDemoSignature(localTaskId)">生成</el-button></template>
              </el-input>
              <el-button type="success" v-permission="'dispatch:issue'" :disabled="!canIssue" :loading="issuing" @click="issue(false)">🔏 签名下发</el-button>
              <el-button type="danger" plain :disabled="!remote?.strategy" :loading="issuing" title="任何角色可见：直接调用后端 issue 接口，无权限角色会被 1003 拦截并触发 R01 告警" @click="issue(true)">🧪 模拟越权下发</el-button>
            </div>
            <div v-if="issueResult" class="issue-result">
              ✅ 指令 <b class="mono">{{ issueResult.commandId }}</b> 已下发至 <b>{{ (issueResult.targets || []).join(', ') }}</b> · 签发者 <span class="mono">{{ issueResult.signerDid }}</span> · 存证 <span class="mono">{{ issueResult.evidenceId }}</span>
            </div>
            <div v-if="remote?.ack" class="issue-result ack">
              📬 终端回执：{{ remote.ack.nodeId }} {{ remote.ack.status }} · 实际功率 {{ fmtNumber(remote.ack.actualPowerKw, 1) }} kW · {{ fmtDateTime(remote.ack.at) }}
            </div>
          </div>

          <!-- ④ AI 分析面板 -->
          <div class="task-card wide">
            <div class="card-header">
              <span class="title-icon">🤖</span>
              <span class="card-title">AI 智能分析（POST /ai/analyze · scene=dispatch）</span>
              <SourceBadge v-if="ai" :source="ai.source" />
              <span v-if="ai" class="muted">{{ ai.latencyMs }} ms</span>
            </div>
            <div class="ai-ask">
              <el-input v-model="question" size="small" placeholder="例如：为什么选择节点C放电？" @keyup.enter="ask" />
              <el-button type="primary" size="small" :loading="asking" :disabled="!remote?.strategy" @click="ask">提问</el-button>
              <el-button size="small" link v-for="q in QUICK_QUESTIONS" :key="q" @click="question = q; ask()">{{ q }}</el-button>
            </div>
            <div v-if="ai" class="ai-answer">
              <div class="ai-text">{{ ai.answer }}</div>
              <div class="reasoning-list">
                <div v-for="(r, i) in ai.reasoning || []" :key="i" class="reasoning-item">{{ i + 1 }}. {{ r }}</div>
              </div>
              <div class="muted">存证 {{ ai.evidenceId || '--' }} · traceId {{ ai.traceId || '--' }}</div>
            </div>
            <div v-if="aiHistoryList.length" class="ai-history">
              <div v-for="h in aiHistoryList" :key="h.id" class="ai-h-item"><span class="muted">{{ fmtTime(h.createdAt) }}</span> <b>{{ h.question }}</b> <span class="muted">→ {{ String(h.answer).slice(0, 60) }}…</span></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧 -->
      <div class="side-panel">
        <div class="panel-section">
          <div class="section-title"><span class="title-icon">📊</span><span>聚合统计</span></div>
          <div class="stats-grid">
            <div class="stat-item"><div class="stat-value">{{ aggregation.onlineNodes }}/{{ aggregation.reportCount }}</div><div class="stat-label">在线节点</div></div>
            <div class="stat-item"><div class="stat-value">{{ fmtNumber(aggregation.totalLoadKw, 0) }}</div><div class="stat-label">总负荷 kW</div></div>
            <div class="stat-item"><div class="stat-value">{{ fmtNumber(aggregation.totalPvKw, 1) }}</div><div class="stat-label">总光伏 kW</div></div>
            <div class="stat-item"><div class="stat-value">{{ aggregation.highestLoadNodeId || '--' }}</div><div class="stat-label">最高负荷</div></div>
          </div>
        </div>
        <div class="panel-section">
          <div class="section-title"><span class="title-icon">🕸️</span><span>节点 → 云端</span></div>
          <div ref="graphRef" class="mini-chart tall"></div>
        </div>
        <div class="panel-section">
          <div class="section-title"><span class="title-icon">🗂️</span><span>调度任务</span><el-button link size="small" class="refresh" @click="loadRemoteTasks">刷新</el-button></div>
          <div class="upload-list">
            <div v-for="t in dispatchStore.remoteTasks" :key="t.id" class="upload-item" :class="{ active: t.id === remote?.id }" @click="viewRemote(t.id)">
              <div class="upload-icon">{{ t.status === 'acked' ? '📬' : t.status === 'issued' ? '📡' : t.status === 'success' ? '🧠' : '⏳' }}</div>
              <div class="upload-info">
                <div class="upload-name mono">{{ t.id }} <span class="chip" :class="t.status">{{ DP_STATUS[t.status] || t.status }}</span></div>
                <div class="muted">{{ t.name }} · {{ fmtTime(t.createdAt) }}</div>
              </div>
            </div>
            <div v-if="!dispatchStore.remoteTasks.length" class="muted">暂无任务</div>
          </div>
        </div>
        <div class="panel-section">
          <div class="section-title"><span class="title-icon">📚</span><span>任务级日志</span></div>
          <div class="task-log-list">
            <div v-for="log in taskLogs" :key="log.id" class="task-log-item">
              <div class="task-log-head"><span class="task-log-level" :class="log.level.toLowerCase()">{{ log.level }}</span><span class="task-log-time">{{ log.timestamp }}</span></div>
              <div class="task-log-content">{{ log.content }}</div>
            </div>
            <div v-if="!taskLogs.length" class="muted">任务创建后显示关联日志</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * 云端聚合与调度：
 * ① FL 任务状态（getFlTask）+ 模型版本（listFlModels / publishFlModel）
 * ② 调度：dispatchStore.runTask → createDispatchTask + runDispatchTask → strategy / qTable / constraintsChecked / explanation(+source)
 *    订阅 dispatch_progress：aggregating → computing → explaining → issued → acked
 * ③ 签名下发：dispatchStore.issueTask(signature)；「模拟越权下发」直接调 issueDispatchTask，捕获 1003 显示拦截横幅
 * ④ AI 分析：aiAnalyze({scene:'dispatch', context, question})；「生成分析报告」把真实 answer 拼成 Markdown 下载
 */
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import SourceBadge from '@/components/legacy/SourceBadge.vue'
import { useDispatchStore } from '@/stores/dispatch'
import { useLogStore } from '@/stores/logs'
import { usePerspectiveStore } from '@/stores/perspective'
import { useUserStore } from '@/stores/user'
import { buildAggregationInput } from '@/services/dispatchTask'
import { listFlTasks, getFlTask, listFlModels, publishFlModel } from '@/api/fl'
import { getDispatchTask, issueDispatchTask } from '@/api/dispatch'
import { aiAnalyze, aiHistory } from '@/api/ai'
import { saveBlob } from '@/api/request'
import { wsClient, WS_TYPES } from '@/api/ws'
import { fmtNumber, fmtTime, fmtDateTime, shortDid } from '@/utils/format'

const logStore = useLogStore()
const perspectiveStore = usePerspectiveStore()
const dispatchStore = useDispatchStore()
const userStore = useUserStore()

const STAGES = [
  { key: 'aggregating', title: '汇聚边缘指标', desc: '收集节点实时状态' },
  { key: 'computing', title: 'DQN 策略推理', desc: 'Q 网络 + 约束校验' },
  { key: 'explaining', title: 'DeepSeek 解释', desc: '三级降级 live/cache/rule' },
  { key: 'issued', title: '签名下发', desc: 'dispatch:issue + DID 签名' },
  { key: 'acked', title: '终端回执', desc: '边缘节点执行确认' }
]
const FL_STATUS = { created: '已创建', running: '训练中', success: '已完成', failed: '失败', cancelled: '已取消' }
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'
const DP_STATUS = { created: '已创建', running: '运行中', success: '待下发', issued: '已下发', acked: '已回执', failed: '失败' }
const ACTION_CN = { charge: '充电', idle: '待机', discharge: '放电' }
const QUICK_QUESTIONS = ['为什么选择该节点放电？', '约束校验拦截了什么？', '总回报如何计算？']

/* ---- FL ---- */
const flTasks = ref([])
const flTaskId = ref('')
const flTask = ref(null)
const flModels = ref([])
const publishing = ref('')
const flChartRef = ref(null)
let flChart = null

async function loadFl() {
  try {
    const [tasks, models] = await Promise.all([listFlTasks({ size: 20 }), listFlModels({ size: 20 })])
    flTasks.value = tasks?.items || []
    flModels.value = models?.items || []
    if (!flTaskId.value && flTasks.value.length) {
      flTaskId.value = (flTasks.value.find(t => t.status === 'running') || flTasks.value[0]).id
      await loadFlTask()
    }
  } catch { /* 已提示 */ }
}
async function loadFlTask() {
  if (!flTaskId.value) return
  try {
    flTask.value = await getFlTask(flTaskId.value)
    nextTick(renderFlChart)
  } catch { flTask.value = null }
}
async function publish(version) {
  publishing.value = version
  try {
    const res = await publishFlModel(version)
    logStore.addLog(`模型 ${version} 已发布（存证 ${res?.evidenceId || '--'}）`, 'INFO', 'CLOUD')
    ElMessage.success(`模型 ${version} 已发布`)
    flModels.value = (await listFlModels({ size: 20 }))?.items || []
  } catch { /* 已提示 */ } finally { publishing.value = '' }
}
function renderFlChart() {
  if (!flChartRef.value) return
  flChart = flChart || echarts.init(flChartRef.value)
  const r = flTask.value?.rounds || []
  flChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(13, 27, 42, 0.95)', borderColor: '#00B4D8', textStyle: { color: '#fff', fontSize: 10 } },
    grid: { left: 40, right: 36, top: 10, bottom: 22 },
    xAxis: { type: 'category', data: r.map(x => `R${x.round}`), axisLabel: { color: '#8892B0', fontSize: 9 }, axisLine: { lineStyle: { color: '#243447' } } },
    yAxis: [{ type: 'value', axisLabel: { color: '#8892B0', fontSize: 9 }, splitLine: { lineStyle: { color: '#243447', type: 'dashed' } } }, { type: 'value', min: 0, max: 1, axisLabel: { color: '#8892B0', fontSize: 9 }, splitLine: { show: false } }],
    series: [
      { name: 'loss', type: 'line', smooth: true, data: r.map(x => x.loss), lineStyle: { color: '#E63946' }, itemStyle: { color: '#E63946' }, symbolSize: 4 },
      { name: 'acc', type: 'line', yAxisIndex: 1, smooth: true, data: r.map(x => x.acc), lineStyle: { color: '#2ECC71' }, itemStyle: { color: '#2ECC71' }, symbolSize: 4 }
    ]
  })
}

/* ---- 调度 ---- */
const selectedNodeIds = ref([])
const timeWindow = reactive({ start: '15:00', end: '16:00' })
const running = ref(false)
const remote = ref(null)          // 后端任务对象（getDispatchTask）
const localTaskId = ref(null)     // store 本地任务 id（用于 issueTask）
const stageLog = reactive({})
const failedStage = ref('')
const signature = ref('')
const issuing = ref(false)
const issueResult = ref(null)
const denyBanner = ref(null)
const qChartRef = ref(null)
const graphRef = ref(null)
let qChart = null
let graphChart = null
let offWs = null

const reports = computed(() => perspectiveStore.dispatchReports.filter(r => !selectedNodeIds.value.length || selectedNodeIds.value.includes(r.nodeId)))
const aggregation = computed(() => buildAggregationInput(reports.value).summary)
const violations = computed(() => remote.value?.constraintsChecked?.violations || [])
const canIssue = computed(() => Boolean(remote.value?.strategy) && remote.value.status !== 'issued' && remote.value.status !== 'acked')
const remoteStatusClass = computed(() => ({ success: 'warning', issued: 'primary', acked: 'success', failed: 'danger', running: 'primary' }[remote.value?.status] || 'neutral'))
const stageIndex = computed(() => {
  const keys = STAGES.map(s => s.key)
  let idx = -1
  keys.forEach((k, i) => { if (stageLog[k]) idx = i })
  if (remote.value?.status === 'acked') return 4
  if (remote.value?.status === 'issued') return Math.max(idx, 3)
  if (remote.value?.strategy) return Math.max(idx, 2)
  return idx
})
const taskLogs = computed(() => localTaskId.value ? logStore.getLogsByTaskId(localTaskId.value).slice().reverse().slice(0, 6) : [])

function resetStages() { Object.keys(stageLog).forEach(k => delete stageLog[k]); failedStage.value = '' }

/** 从任务已存的时间戳还原各阶段的时间与详情。
 *  实时运行时靠 WebSocket dispatch_progress 逐步填 stageLog；但**查看**一个已有任务
 *  （切换任务、刷新、进页面默认选中）时没有实时事件，若只 resetStages 就全是 --。
 *  这里用后端返回的 createdAt/issuedAt/updatedAt 把已发生的阶段补上，切回来不再丢时间。 */
function hydrateStages(t) {
  if (!t) return
  // 只填还没有实时时间的阶段：WebSocket 送到的精确时间优先，hydrate 只补空缺
  const set = (k, at, detail) => { if (at && !stageLog[k]) stageLog[k] = { at, detail } }
  set('aggregating', t.createdAt, `汇聚 ${(t.nodeIds || []).length} 个节点实时指标`)
  if (t.strategy) set('computing', t.createdAt, 'DQN 生成调度策略，约束校验通过')
  if (t.explanation) set('explaining', t.createdAt, `解释来源：${{ live: 'DeepSeek 实时', cache: '缓存', rule: '规则模板' }[t.explanationSource] || t.explanationSource || '—'}`)
  if (t.issued) set('issued', t.issuedAt, `已下发至 ${(t.targets || t.nodeIds || []).join(', ')}${t.signerDid ? '，签发者 ' + t.signerDid : ''}`)
  if (t.ackStatus === 'acked') set('acked', t.updatedAt, t.ackDetail || '边缘节点执行确认')
}

function onProgress(payload, msg) {
  if (!payload?.taskId) return
  if (remote.value && payload.taskId !== remote.value.id && payload.taskId !== pendingRemoteId) return
  stageLog[payload.stage] = { detail: payload.detail, at: msg?.ts || new Date().toISOString() }
  if ((payload.stage === 'issued' || payload.stage === 'acked') && remote.value?.id === payload.taskId) {
    getDispatchTask(payload.taskId).then(t => { remote.value = t }).catch(() => {})
  }
}
let pendingRemoteId = null

async function generateStrategy() {
  if (!reports.value.length) { ElMessage.warning('请至少选择一个节点'); return }
  running.value = true
  resetStages()
  issueResult.value = null
  denyBanner.value = null
  pendingRemoteId = null
  logStore.addLog(`汇聚 ${reports.value.length} 个节点实时指标，创建调度任务（时间窗 ${timeWindow.start}~${timeWindow.end}）`, 'INFO', 'CLOUD')
  try {
    const res = await dispatchStore.runTask({ reports: reports.value, timeWindow: { start: timeWindow.start, end: timeWindow.end } })
    localTaskId.value = res.task?.id || null
    if (!res.ok || !res.task?.remoteId) {
      failedStage.value = 'computing'
      return
    }
    remote.value = await getDispatchTask(res.task.remoteId)
    hydrateStages(remote.value)   // WS 没送到时，从 createdAt 兜底填 汇聚/DQN推理/解释 的时间
    signature.value = dispatchStore.buildDemoSignature(localTaskId.value)
    ai.value = null
    await dispatchStore.fetchTasks().catch(() => {})
    nextTick(() => { renderQChart(); renderGraph() })
  } finally {
    running.value = false
  }
}

async function viewRemote(id) {
  try {
    remote.value = await getDispatchTask(id)
    const local = dispatchStore.tasks.find(t => t.remoteId === id)
    localTaskId.value = local?.id || null
    issueResult.value = remote.value.commandId ? { commandId: remote.value.commandId, targets: remote.value.targets, signerDid: remote.value.signerDid, evidenceId: remote.value.issueEvidenceId } : null
    resetStages()
    hydrateStages(remote.value)   // 查看已有任务时从已存时间戳还原各阶段，切回来不丢时间
    signature.value = dispatchStore.buildDemoSignature(localTaskId.value)
    ai.value = null
    logStore.addLog(`查看调度任务 ${id}（${DP_STATUS[remote.value.status] || remote.value.status}）`, 'INFO', 'CLOUD', { traceId: remote.value.traceId })
    nextTick(() => { renderQChart(); renderGraph() })
  } catch { /* 已提示 */ }
}

/**
 * 签名下发。simulate=true 为「模拟越权下发」：任何角色可点，直接调后端 issue 接口，
 * 无权限角色被 1003 拦截 → 显示横幅（告警由 WS audit_alert 弹出）。
 */
async function issue(simulate) {
  if (!remote.value?.strategy) return
  issuing.value = true
  denyBanner.value = null
  const sig = signature.value || dispatchStore.buildDemoSignature(localTaskId.value)
  try {
    let res
    if (!simulate && localTaskId.value) {
      res = await dispatchStore.issueTask(sig, localTaskId.value)
    } else {
      logStore.addLog(`${simulate ? '【越权演示】' : ''}以角色 ${userStore.roleLabel} 直接调用 issue 接口（任务 ${remote.value.id}）`, 'INFO', 'CLOUD')
      res = await issueDispatchTask(remote.value.id, { signature: sig }, remote.value.traceId)
      logStore.addLog(`任务 ${remote.value.id} 已下发（commandId ${res.commandId}）`, 'INFO', 'CLOUD')
    }
    issueResult.value = res
    remote.value = await getDispatchTask(remote.value.id)
    hydrateStages(remote.value)   // WS 可能没送到（重连/时序），从已存 issuedAt 兜底填「签名下发」时间
    await dispatchStore.fetchTasks().catch(() => {})
  } catch (e) {
    if (e?.code === 1003 || e?.code === 1004) {
      denyBanner.value = { code: e.code, message: e.message, traceId: e.traceId }
      failedStage.value = 'issued'
      logStore.addLog(`下发被后端拦截（code ${e.code}）：${e.message}`, 'ERROR', 'CLOUD', { traceId: e.traceId })
    }
  } finally {
    issuing.value = false
  }
}

function renderQChart() {
  if (!qChartRef.value || !remote.value?.qTable) return
  qChart = qChart || echarts.init(qChartRef.value)
  const q = remote.value.qTable
  qChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(13, 27, 42, 0.95)', borderColor: '#00B4D8', textStyle: { color: '#fff', fontSize: 10 } },
    legend: { data: ['charge', 'idle', 'discharge'], textStyle: { color: '#8892B0', fontSize: 10 }, top: 0 },
    grid: { left: 36, right: 12, top: 26, bottom: 22 },
    xAxis: { type: 'category', data: q.map(x => x.nodeId), axisLabel: { color: '#8892B0', fontSize: 10 }, axisLine: { lineStyle: { color: '#243447' } } },
    yAxis: { type: 'value', name: 'Q', nameTextStyle: { color: '#8892B0', fontSize: 10 }, axisLabel: { color: '#8892B0', fontSize: 10 }, splitLine: { lineStyle: { color: '#243447', type: 'dashed' } } },
    series: ['charge', 'idle', 'discharge'].map((a, i) => ({
      name: a, type: 'bar', barWidth: 12, data: q.map(x => x[a]),
      itemStyle: { color: ['#2ECC71', '#8892B0', '#F39C12'][i], borderRadius: [3, 3, 0, 0] },
      label: { show: true, position: 'top', fontSize: 9, color: '#8892B0', formatter: p => remote.value.strategy.actions.find(x => x.nodeId === q[p.dataIndex].nodeId)?.action === a ? '★' : '' }
    }))
  })
}

function renderGraph() {
  if (!graphRef.value) return
  graphChart = graphChart || echarts.init(graphRef.value)
  const targets = new Set(remote.value?.targets || remote.value?.strategy?.actions?.filter(a => a.action !== 'idle').map(a => a.nodeId) || [])
  graphChart.setOption({
    backgroundColor: 'transparent',
    series: [{
      type: 'graph', layout: 'force', roam: false, force: { repulsion: 180, edgeLength: 70, gravity: 0.2 },
      symbolSize: 34, label: { show: true, color: '#fff', fontSize: 10 },
      edgeSymbol: ['none', 'arrow'], edgeSymbolSize: [0, 8],
      data: [
        { name: '☁️ 云端', symbolSize: 52, itemStyle: { color: new echarts.graphic.RadialGradient(0.5, 0.3, 1, [{ offset: 0, color: '#00D4FF' }, { offset: 1, color: '#0077B6' }]), shadowBlur: 20, shadowColor: 'rgba(0,180,216,.6)' } },
        ...perspectiveStore.nodes.map(n => ({ name: n.id, symbol: 'roundRect', itemStyle: { color: targets.has(n.id) ? '#F39C12' : n.status === 'warning' ? '#D68910' : '#2ECC71' } }))
      ],
      links: perspectiveStore.nodes.map(n => ({ source: targets.has(n.id) ? '☁️ 云端' : n.id, target: targets.has(n.id) ? n.id : '☁️ 云端', lineStyle: { color: targets.has(n.id) ? '#F39C12' : 'rgba(0,180,216,.5)', width: targets.has(n.id) ? 3 : 1.5, curveness: 0.15 } }))
    }]
  })
}

/* ---- AI ---- */
const question = ref('为什么选择该节点放电？')
const asking = ref(false)
const ai = ref(null)
const aiHistoryList = ref([])

function buildAiContext() {
  const nodes = perspectiveStore.nodes.filter(n => remote.value?.nodeIds?.includes(n.id) ?? true).map(n => ({ id: n.id, pv: n.metrics.pvOutput, load: n.metrics.load, soc: n.metrics.soc, price: 0.8 }))
  return { taskId: remote.value?.id, nodes, actions: remote.value?.strategy?.actions || [], totalReward: remote.value?.strategy?.totalReward, constraintsChecked: remote.value?.constraintsChecked || null }
}
async function ask() {
  if (!remote.value?.strategy || asking.value) return
  asking.value = true
  try {
    ai.value = await aiAnalyze({ scene: 'dispatch', context: buildAiContext(), question: question.value })
    logStore.addLog(`AI 分析（${ai.value.source}，${ai.value.latencyMs}ms）：${question.value}`, 'INFO', 'CLOUD', { traceId: ai.value.traceId })
    aiHistoryList.value = ((await aiHistory({ scene: 'dispatch', size: 5 }))?.items || [])
  } catch { /* 已提示 */ } finally { asking.value = false }
}

/** 真实 answer 拼装 Markdown 报告并下载 */
async function downloadReport() {
  const r = remote.value
  if (!r?.strategy) return
  if (!ai.value) await ask()
  const lines = [
    '# 云端聚合与调度分析报告', '',
    `- 任务编号：${r.id}`, `- 生成时间：${new Date().toLocaleString('zh-CN')}`, `- 时间窗：${r.strategy.timeWindow || r.timeWindow}`, `- traceId：${r.traceId || '--'}`, `- 存证：${r.evidenceId || '--'}`, '',
    '## 1. 联邦模型状态',
    flTask.value ? `- ${flTask.value.id}（${FL_STATUS[flTask.value.status]}，${flTask.value.currentRound}/${flTask.value.totalRounds} 轮，模型 ${flTask.value.modelVersion || '未生成'}，ε 消耗 ${fmtNumber(flTask.value.dp?.epsilonSpent, 3)}/${fmtNumber(flTask.value.dp?.epsilon, 2)}）` : '- 无', '',
    '## 2. DQN 调度策略', '', '| 节点 | 动作 | 功率(kW) | Q 值 | 原因 |', '|---|---|---|---|---|',
    ...r.strategy.actions.map(a => `| ${a.nodeId} | ${ACTION_CN[a.action] || a.action} | ${fmtNumber(a.powerKw, 1)} | ${fmtNumber(a.qValue, 2)} | ${a.reason || ''} |`), '',
    `- 总回报：${fmtNumber(r.strategy.totalReward, 1)}`,
    `- 约束校验：${violations.value.length ? violations.value.map(v => `${v.nodeId} ${v.constraint || v.rule}（${v.detail}）`).join('；') : '全部满足'}`, '',
    `## 3. 策略解释（来源：${r.explanationSource || '--'}）`, '', r.explanation || '--', '',
    `## 4. AI 智能分析（来源：${ai.value?.source || '--'}，${ai.value?.latencyMs ?? '--'} ms）`, '', `**问：** ${question.value}`, '', `**答：** ${ai.value?.answer || '--'}`, '',
    ...(ai.value?.reasoning || []).map((x, i) => `${i + 1}. ${x}`), '',
    '## 5. 下发与回执',
    r.commandId ? `- 指令 ${r.commandId} 由 ${r.signerDid} 签名下发至 ${(r.targets || []).join(', ')}（存证 ${r.issueEvidenceId || '--'}）` : '- 尚未下发',
    r.ack ? `- 回执：${r.ack.nodeId} ${r.ack.status}，实际功率 ${fmtNumber(r.ack.actualPowerKw, 1)} kW` : '- 尚未回执', '',
    '> 本报告由平台基于 DQN 推理结果与 AI 解释接口的真实返回自动拼装，AI 来源字段如实标注 live/cache/rule；仅作辅助分析，不直接执行设备控制。'
  ]
  const name = `dispatch-report-${r.id}-${new Date().toISOString().slice(0, 10)}.md`
  saveBlob(new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' }), name)
  logStore.addLog(`分析报告已生成并下载：${name}`, 'INFO', 'CLOUD')
}

async function loadRemoteTasks() { await dispatchStore.fetchTasks().catch(() => {}) }

function onResize() { flChart?.resize(); qChart?.resize(); graphChart?.resize() }

watch(() => perspectiveStore.nodes.length, () => { if (!selectedNodeIds.value.length) selectedNodeIds.value = perspectiveStore.nodes.map(n => n.id) })

onMounted(async () => {
  logStore.addLog('进入云端聚合与调度模块', 'INFO', 'CLOUD')
  selectedNodeIds.value = perspectiveStore.nodes.map(n => n.id)
  offWs = wsClient.on(WS_TYPES.DISPATCH_PROGRESS, onProgress)
  await Promise.all([loadFl(), loadRemoteTasks()])
  // 默认展示最近一条已生成策略的任务
  const latest = dispatchStore.remoteTasks.find(t => t.strategy)
  if (latest) await viewRemote(latest.id)
  renderGraph()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  offWs && offWs()
  flChart?.dispose(); qChart?.dispose(); graphChart?.dispose()
  window.removeEventListener('resize', onResize)
})
</script>

<style scoped>
.aggregate-container { height: 100%; min-height: 0; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.page-title { font-size: 24px; color: var(--color-text); margin-bottom: 8px; }
.page-desc { color: var(--color-text-secondary); font-size: 13px; }
.header-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; justify-content: flex-end; }
.deny-banner { display: flex; align-items: center; gap: 12px; padding: 10px 16px; border-radius: 10px; background: rgba(230, 57, 70, 0.15); border: 1px solid rgba(230, 57, 70, 0.6); box-shadow: 0 0 20px rgba(230, 57, 70, 0.25); }
.deny-icon { font-size: 26px; }
.deny-body { flex: 1; }
.deny-title { color: var(--color-danger); font-weight: 700; font-size: 14px; }
.deny-msg { color: var(--color-text-secondary); font-size: 12px; margin-top: 2px; }
.aggregate-content { flex: 1; min-height: 0; min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr) 290px; gap: 16px; overflow: hidden; }
.left-panel { min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 14px; overflow-y: auto; padding-right: 4px; }
.main-area { border-radius: 12px; padding: 12px; border: 1px solid rgba(0, 180, 216, 0.15); }
.status-panel { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
.status-item { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 12px 8px; border-radius: 10px; border: 1px solid rgba(0, 180, 216, 0.15); opacity: 0.55; transition: all 0.3s ease; min-width: 0; overflow: hidden; }
.status-item.active { opacity: 1; background: rgba(0, 180, 216, 0.06); }
.status-item.completed { border-color: rgba(46, 204, 113, 0.35); }
.status-item.failed { border-color: rgba(230, 57, 70, 0.5); opacity: 1; }
.status-step { display: flex; align-items: center; gap: 8px; }
.step-icon { font-size: 18px; }
.step-text { font-size: 13px; color: var(--color-text); font-weight: 500; }
.status-detail { display: flex; flex-direction: column; align-items: center; text-align: center; width: 100%; min-width: 0; }
.detail-value { font-size: 13px; font-weight: 700; color: var(--color-primary); font-family: 'Consolas', monospace; }
/* 详情文字：宽度锁在单元格内，完整换行显示（签发者 DID 也完整展示）；
   长串按字符断行不溢出，五张卡靠 grid 默认 stretch 等高对齐 */
.detail-label { font-size: 10px; color: var(--color-text-secondary); width: 100%; word-break: break-all; line-height: 1.5; }
.task-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.task-card { border-radius: 12px; padding: 14px; border: 1px solid rgba(0, 180, 216, 0.15); display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.task-card.wide { grid-column: 1 / -1; }
.card-header { display: flex; align-items: center; gap: 8px; }
.title-icon { font-size: 18px; }
.card-title { font-size: 14px; font-weight: 600; color: var(--color-primary); }
.fl-select { margin-left: auto; width: 200px; }
.status-chip, .chip { margin-left: auto; padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.chip { margin-left: 0; padding: 1px 8px; font-size: 10px; }
.status-chip.neutral, .chip.created { color: var(--color-text-secondary); background: rgba(136, 146, 176, 0.15); }
.status-chip.primary, .chip.running, .chip.issued { color: var(--color-primary); background: rgba(0, 180, 216, 0.15); }
.status-chip.success, .chip.success, .chip.acked { color: var(--color-success); background: rgba(46, 204, 113, 0.15); }
.status-chip.warning { color: var(--color-warning); background: rgba(243, 156, 18, 0.15); }
.status-chip.danger, .chip.failed, .chip.cancelled { color: var(--color-danger); background: rgba(230, 57, 70, 0.15); }
.summary-list { display: flex; flex-direction: column; gap: 4px; }
.summary-row { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; }
.summary-key { color: var(--color-text-secondary); }
.summary-value { color: var(--color-text); text-align: right; display: flex; gap: 6px; align-items: center; }
.mono { font-family: 'Consolas', monospace; }
.muted { color: var(--color-text-secondary); font-size: 11px; }
.ok { color: var(--color-success); } .no { color: var(--color-danger); }
.mini-table { font-size: 12px; }
.mini-table :deep(.act-discharge td) { background: rgba(243, 156, 18, 0.06) !important; }
.mini-table :deep(.act-charge td) { background: rgba(46, 204, 113, 0.06) !important; }
.fl-chart { height: 120px; }
.models-block { border-top: 1px solid rgba(0, 180, 216, 0.12); padding-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.models-title { font-size: 12px; color: var(--color-text); font-weight: 600; }
.model-row { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--color-text); }
.gen-form { display: flex; align-items: center; gap: 8px; }
.act { padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.act.discharge { color: var(--color-warning); background: rgba(243, 156, 18, 0.15); }
.act.charge { color: var(--color-success); background: rgba(46, 204, 113, 0.15); }
.act.idle { color: var(--color-text-secondary); background: rgba(136, 146, 176, 0.15); }
.kv-row { display: flex; flex-wrap: wrap; gap: 8px 16px; font-size: 12px; color: var(--color-text-secondary); }
.kv-row b { color: var(--color-text); font-weight: 600; }
.q-chart { height: 150px; }
.constraints { padding: 8px 10px; border-radius: 8px; background: rgba(0, 180, 216, 0.05); font-size: 12px; }
.c-title { color: var(--color-text); font-weight: 600; margin-bottom: 4px; }
.c-item { color: var(--color-warning); }
.c-ok { color: var(--color-success); }
.reasoning-block { padding: 10px 12px; border-radius: 8px; background: rgba(0, 180, 216, 0.06); border-left: 3px solid var(--color-primary); }
.reasoning-summary { font-size: 13px; line-height: 1.6; color: var(--color-text); }
.issue-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.sig-input { flex: 1; min-width: 320px; }
.issue-result { padding: 8px 12px; border-radius: 8px; background: rgba(46, 204, 113, 0.1); border: 1px solid rgba(46, 204, 113, 0.35); font-size: 12px; color: var(--color-text); word-break: break-all; line-height: 1.6; }
.issue-result.ack { background: rgba(0, 180, 216, 0.08); border-color: rgba(0, 180, 216, 0.35); }
.ai-ask { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ai-ask .el-input { flex: 1; min-width: 260px; }
.ai-answer { display: flex; flex-direction: column; gap: 8px; }
.ai-text { font-size: 13px; line-height: 1.7; color: var(--color-text); padding: 10px 12px; border-radius: 8px; background: rgba(0, 180, 216, 0.06); }
.reasoning-list { display: flex; flex-direction: column; gap: 4px; }
.reasoning-item { font-size: 12px; color: var(--color-text-secondary); padding-left: 8px; border-left: 2px solid rgba(0, 180, 216, 0.4); }
.ai-history { border-top: 1px solid rgba(0, 180, 216, 0.12); padding-top: 6px; display: flex; flex-direction: column; gap: 2px; }
.ai-h-item { font-size: 11px; color: var(--color-text); }
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; min-height: 90px; color: var(--color-text-secondary); font-size: 12px; border: 1px dashed rgba(0, 180, 216, 0.2); border-radius: 8px; }
.empty-icon { font-size: 22px; }
.side-panel { min-height: 0; display: flex; flex-direction: column; gap: 12px; overflow-y: auto; }
.panel-section { border-radius: 12px; padding: 12px; border: 1px solid rgba(0, 180, 216, 0.1); }
.section-title { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; color: var(--color-primary); font-size: 13px; font-weight: 600; }
.refresh { margin-left: auto; }
.stats-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.stat-item { padding: 8px; border-radius: 8px; border: 1px solid rgba(0, 180, 216, 0.15); text-align: center; }
.stat-value { font-size: 16px; font-weight: 700; color: var(--color-text); }
.stat-label { font-size: 10px; color: var(--color-text-secondary); }
.mini-chart.tall { height: 190px; }
.upload-list, .task-log-list { display: flex; flex-direction: column; gap: 6px; max-height: 240px; overflow: auto; }
.upload-item { display: flex; gap: 8px; align-items: center; padding: 6px 8px; border-radius: 8px; border: 1px solid rgba(0, 180, 216, 0.12); cursor: pointer; }
.upload-item:hover, .upload-item.active { border-color: var(--color-primary); background: rgba(0, 180, 216, 0.08); }
.upload-icon { font-size: 16px; }
.upload-info { min-width: 0; flex: 1; }
.upload-name { font-size: 12px; color: var(--color-text); display: flex; gap: 6px; align-items: center; }
.task-log-item { padding: 6px 8px; border-radius: 6px; background: rgba(0, 180, 216, 0.04); }
.task-log-head { display: flex; justify-content: space-between; font-size: 10px; }
.task-log-level { font-weight: 700; }
.task-log-level.info { color: var(--color-primary); } .task-log-level.warn { color: var(--color-warning); } .task-log-level.error { color: var(--color-danger); }
.task-log-time { color: var(--color-text-secondary); }
.task-log-content { font-size: 11px; color: var(--color-text); margin-top: 2px; }
.fade-enter-active, .fade-leave-active { transition: opacity 0.3s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
@media (max-width: 1300px) { .task-grid { grid-template-columns: 1fr; } }
@media (max-width: 1000px) { .aggregate-content { grid-template-columns: 1fr; } .status-panel { grid-template-columns: repeat(3, 1fr); } }
</style>
