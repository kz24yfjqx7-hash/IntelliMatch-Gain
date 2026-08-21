<template>
  <div class="audit-container">
    <div class="page-header">
      <div>
        <h2 class="page-title">📋 系统审计与日志中心</h2>
        <p class="page-desc">后端审计 API：看板统计、日志检索与导出、任务级全流程追踪（traceId）、风险告警、审计报告；底部保留前端操作记录</p>
      </div>
      <el-button size="small" @click="loadStats">刷新统计</el-button>
    </div>

    <!-- 统计卡片 -->
    <div class="stats-bar">
      <div class="stat-item"><span class="stat-icon">📊</span><span class="stat-label">今日日志</span><span class="stat-value">{{ stats.todayLogs ?? '--' }}</span></div>
      <div class="stat-item error"><span class="stat-icon">🚨</span><span class="stat-label">高风险日志</span><span class="stat-value">{{ stats.highRiskLogs ?? '--' }}</span></div>
      <div class="stat-item warn"><span class="stat-icon">🔔</span><span class="stat-label">未确认告警</span><span class="stat-value">{{ stats.openAlerts ?? '--' }}</span></div>
      <div class="stat-item success"><span class="stat-icon">⛓️</span><span class="stat-label">上链日志</span><span class="stat-value">{{ stats.onChainLogs ?? '--' }}</span></div>
      <div class="stat-item info"><span class="stat-icon">🧾</span><span class="stat-label">前端操作记录</span><span class="stat-value">{{ logStore.logs.length }}</span></div>
    </div>

    <div class="charts-row">
      <div class="chart-card"><div class="chart-title">按模块分布</div><div ref="moduleRef" class="chart"></div></div>
      <div class="chart-card"><div class="chart-title">按风险等级</div><div ref="riskRef" class="chart"></div></div>
      <div class="chart-card wide"><div class="chart-title">近 7 日趋势</div><div ref="trendRef" class="chart"></div></div>
    </div>

    <el-tabs v-model="tab" class="audit-tabs">
      <!-- ② 日志检索 -->
      <el-tab-pane label="日志检索" name="logs">
        <div class="filter-bar">
          <el-input v-model="query.traceId" size="small" placeholder="traceId" clearable style="width: 200px" />
          <el-input v-model="query.actorDid" size="small" placeholder="actorDid" clearable style="width: 200px" />
          <el-input v-model="query.action" size="small" placeholder="action（如 dispatch:issue）" clearable style="width: 180px" />
          <el-select v-model="query.riskLevel" size="small" placeholder="riskLevel" clearable style="width: 120px">
            <el-option v-for="(l, k) in RISK_LABELS" :key="k" :label="l" :value="k" />
          </el-select>
          <el-date-picker v-model="query.range" type="datetimerange" size="small" range-separator="~" start-placeholder="from" end-placeholder="to" style="width: 340px" />
          <el-input v-model="query.keyword" size="small" placeholder="关键字" clearable style="width: 160px" @keyup.enter="searchLogs" />
          <el-button type="primary" size="small" :loading="logsLoading" @click="searchLogs">查询</el-button>
          <el-button size="small" @click="resetQuery">重置</el-button>
          <el-button size="small" type="success" plain :loading="exporting" @click="exportCsv">导出 CSV</el-button>
        </div>
        <el-table :data="logs.items" size="small" :row-class-name="riskRowClass" max-height="420" @row-click="row => openTrace(row.traceId)">
          <el-table-column label="时间" width="150"><template #default="{ row }">{{ fmtDateTime(row.at) }}</template></el-table-column>
          <el-table-column label="traceId" width="190"><template #default="{ row }"><span class="mono link">{{ row.traceId }}</span></template></el-table-column>
          <el-table-column prop="actorName" label="操作者" width="90" />
          <el-table-column prop="module" label="模块" width="90" />
          <el-table-column prop="action" label="动作" width="150" />
          <el-table-column label="结果" width="80"><template #default="{ row }"><span class="result-badge" :class="row.result">{{ row.result }}</span></template></el-table-column>
          <el-table-column label="风险" width="70"><template #default="{ row }"><span class="level-badge" :class="row.riskLevel">{{ RISK_LABELS[row.riskLevel] || row.riskLevel }}</span></template></el-table-column>
          <el-table-column prop="detail" label="详情" min-width="220" show-overflow-tooltip />
          <el-table-column label="存证 / hash" width="220"><template #default="{ row }"><span class="mono small">{{ row.evidenceId || '--' }} · {{ shortHash(row.hash, 8, 6) }}</span></template></el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination background layout="total, sizes, prev, pager, next" :total="logs.total" :page-sizes="[10, 20, 50, 100]" v-model:current-page="query.page" v-model:page-size="query.size" @current-change="searchLogs" @size-change="searchLogs" />
        </div>
      </el-tab-pane>

      <!-- ③ 全流程追踪 -->
      <el-tab-pane label="全流程追踪" name="trace">
        <div class="filter-bar">
          <el-input v-model="traceInput" size="small" placeholder="输入 traceId（默认最近一条高风险日志）" class="mono" style="width: 340px" @keyup.enter="loadTrace" />
          <el-button type="primary" size="small" :loading="traceLoading" @click="loadTrace">追踪</el-button>
          <el-button size="small" link v-for="c in traceCandidates" :key="c.traceId" @click="openTrace(c.traceId)">{{ c.label }}</el-button>
        </div>
        <div v-if="trace" class="trace-board">
          <div class="trace-summary">
            <div class="summary-card"><span class="summary-label">traceId</span><span class="summary-value mono">{{ trace.traceId }}</span></div>
            <div class="summary-card"><span class="summary-label">操作者</span><span class="summary-value">{{ trace.summary?.actorName || shortDid(trace.summary?.actorDid) }}</span></div>
            <div class="summary-card"><span class="summary-label">结果</span><span class="summary-value"><span class="result-badge" :class="trace.summary?.result">{{ trace.summary?.result }}</span></span></div>
            <div class="summary-card"><span class="summary-label">最高风险</span><span class="summary-value"><span class="level-badge" :class="trace.summary?.riskLevel">{{ RISK_LABELS[trace.summary?.riskLevel] || '--' }}</span></span></div>
            <div class="summary-card"><span class="summary-label">时长 / 步骤 / 存证</span><span class="summary-value">{{ fmtDuration(trace.summary?.durationMs) }} / {{ trace.summary?.stepCount ?? trace.steps?.length }} / {{ trace.summary?.evidenceCount ?? '--' }}</span></div>
            <div class="summary-card"><span class="summary-label">起止</span><span class="summary-value">{{ fmtTime(trace.summary?.startAt) }} → {{ fmtTime(trace.summary?.endAt) }}</span></div>
          </div>
          <el-timeline class="trace-timeline">
            <el-timeline-item v-for="s in trace.steps" :key="s.seq" :timestamp="fmtDateTime(s.at)" placement="top" :type="stepType(s)" :hollow="s.result === 'success' || s.result === 'allowed'">
              <div class="step-card" :class="s.result">
                <div class="step-head">
                  <span class="step-seq">#{{ s.seq }}</span>
                  <span class="step-module">{{ MODULE_LABELS[s.module] || s.module }}</span>
                  <span class="step-action mono">{{ s.action }}</span>
                  <span class="result-badge" :class="s.result">{{ s.result }}</span>
                  <span v-if="s.riskLevel" class="level-badge" :class="s.riskLevel">{{ RISK_LABELS[s.riskLevel] }}</span>
                </div>
                <div class="step-detail">{{ s.detail || '--' }}</div>
                <div class="step-meta mono">{{ s.actorName || shortDid(s.actorDid) }}{{ s.resourceType ? ` · ${s.resourceType}/${s.resourceId}` : '' }}{{ s.evidenceId ? ` · 存证 ${s.evidenceId}` : '' }}</div>
              </div>
            </el-timeline-item>
          </el-timeline>
        </div>
        <div v-else class="empty-state"><div class="empty-icon">🧭</div><div class="empty-text">输入 traceId 查看从登录、鉴权到算法、存证的完整链路</div></div>
      </el-tab-pane>

      <!-- ④ 风险告警 -->
      <el-tab-pane name="alerts">
        <template #label>风险告警 <el-badge :value="openCount" :hidden="!openCount" type="danger" /></template>
        <div class="filter-bar">
          <el-radio-group v-model="alertStatus" size="small" @change="loadAlerts">
            <el-radio-button value="open">未确认</el-radio-button>
            <el-radio-button value="acked">已确认</el-radio-button>
            <el-radio-button value="">全部</el-radio-button>
          </el-radio-group>
          <span class="rule-legend"><span v-for="(n, c) in RULE_NAMES" :key="c" class="rule-chip">{{ c.slice(0, 3) }} {{ n }}</span></span>
        </div>
        <el-table :data="alerts" size="small" max-height="420" :row-class-name="({ row }) => `risk-${row.riskLevel}`">
          <el-table-column label="时间" width="150"><template #default="{ row }">{{ fmtDateTime(row.at || row.createdAt) }}</template></el-table-column>
          <el-table-column label="规则" width="170"><template #default="{ row }"><b>{{ row.ruleCode?.slice(0, 3) }}</b> {{ RULE_NAMES[row.ruleCode] || row.ruleName || row.ruleCode }}</template></el-table-column>
          <el-table-column label="风险" width="70"><template #default="{ row }"><span class="level-badge" :class="row.riskLevel">{{ RISK_LABELS[row.riskLevel] }}</span></template></el-table-column>
          <el-table-column prop="message" label="消息" min-width="240" show-overflow-tooltip />
          <el-table-column label="主体 DID" width="200"><template #default="{ row }"><span class="mono small" :title="row.actorDid">{{ shortDid(row.actorDid) }}</span></template></el-table-column>
          <el-table-column label="traceId" width="180"><template #default="{ row }"><span class="mono small link" @click="openTrace(row.traceId)">{{ row.traceId || '--' }}</span></template></el-table-column>
          <el-table-column label="状态" width="80"><template #default="{ row }"><span class="result-badge" :class="row.status === 'acked' ? 'success' : 'denied'">{{ row.status === 'acked' ? '已确认' : '未确认' }}</span></template></el-table-column>
          <el-table-column label="操作" width="80"><template #default="{ row }"><el-button v-if="row.status !== 'acked'" size="small" type="primary" link @click="ack(row)">确认</el-button></template></el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ⑤ 审计报告 -->
      <el-tab-pane label="审计报告" name="report">
        <div class="filter-bar">
          <el-radio-group v-model="report.period" size="small"><el-radio-button value="day">日报</el-radio-button><el-radio-button value="week">周报</el-radio-button><el-radio-button value="month">月报</el-radio-button></el-radio-group>
          <el-date-picker v-model="report.date" type="date" size="small" value-format="YYYY-MM-DD" placeholder="日期" style="width: 150px" />
          <el-button type="primary" size="small" :loading="reportLoading" @click="loadReport">生成报告</el-button>
          <SourceBadge v-if="report.data" :source="report.data.narrativeSource" prefix="叙述来源：" />
        </div>
        <div v-if="report.data" class="report-board">
          <div class="narrative">{{ report.data.narrative }}</div>
          <div class="report-grid">
            <div class="report-card">
              <div class="rc-title">身份操作 identityOps</div>
              <div class="rc-row" v-for="(v, k) in report.data.identityOps" :key="k"><span>{{ { register: 'DID 签发', freeze: '冻结', revoke: '注销', rotate: '密钥轮换' }[k] || k }}</span><b>{{ v }}</b></div>
            </div>
            <div class="report-card">
              <div class="rc-title">权限操作 permissionOps</div>
              <div class="rc-row" v-for="(v, k) in report.data.permissionOps" :key="k"><span>{{ { applied: '申请', approved: '通过', rejected: '驳回', revoked: '回收' }[k] || k }}</span><b>{{ v }}</b></div>
            </div>
            <div class="report-card">
              <div class="rc-title">存证 evidence</div>
              <div class="rc-row"><span>总计</span><b>{{ report.data.evidence?.total }}</b></div>
              <div class="rc-row" v-for="(v, k) in report.data.evidence?.byCategory || {}" :key="k"><span>{{ k }}</span><b>{{ v }}</b></div>
            </div>
            <div class="report-card">
              <div class="rc-title">风险事件 riskEvents</div>
              <div class="rc-row" v-for="r in report.data.riskEvents" :key="r.ruleCode"><span>{{ RULE_NAMES[r.ruleCode] || r.ruleCode }}</span><b><span class="level-badge" :class="r.level">{{ r.count }}</span></b></div>
              <div v-if="!report.data.riskEvents?.length" class="muted">本期未触发风控规则</div>
            </div>
            <div class="report-card" v-if="report.data.totals">
              <div class="rc-title">汇总 totals</div>
              <div class="rc-row"><span>日志</span><b>{{ report.data.totals.logs }}</b></div>
              <div class="rc-row"><span>高风险</span><b>{{ report.data.totals.highRisk }}</b></div>
              <div class="rc-row"><span>拒绝/失败</span><b>{{ report.data.totals.denied }}</b></div>
              <div class="rc-row"><span>告警</span><b>{{ report.data.totals.alerts }}</b></div>
            </div>
          </div>
        </div>
        <div v-else class="empty-state"><div class="empty-icon">📑</div><div class="empty-text">选择周期与日期后生成报告，叙述由 DeepSeek 生成（失败时规则化文本）</div></div>
      </el-tab-pane>

      <!-- ⑥ 前端操作记录 -->
      <el-tab-pane label="前端操作记录" name="local">
        <div class="filter-bar">
          <span class="filter-label">级别：</span>
          <el-radio-group v-model="levelFilter" size="small"><el-radio-button value="ALL">全部</el-radio-button><el-radio-button value="INFO">INFO</el-radio-button><el-radio-button value="WARN">WARN</el-radio-button><el-radio-button value="ERROR">ERROR</el-radio-button></el-radio-group>
          <span class="filter-label">来源：</span>
          <el-select v-model="sourceFilter" size="small" style="width: 120px"><el-option label="全部" value="ALL" /><el-option v-for="s in sources" :key="s" :label="s" :value="s" /></el-select>
          <el-button size="small" @click="logStore.clearLogs()" :disabled="!logStore.logs.length">清空</el-button>
        </div>
        <el-table :data="localLogs" size="small" max-height="420">
          <el-table-column prop="timestamp" label="时间" width="170" />
          <el-table-column label="级别" width="80"><template #default="{ row }"><span class="lvl" :class="row.level.toLowerCase()">{{ row.level }}</span></template></el-table-column>
          <el-table-column prop="source" label="来源" width="90" />
          <el-table-column prop="content" label="内容" min-width="320" />
          <el-table-column label="traceId" width="190"><template #default="{ row }"><span class="mono small link" v-if="row.traceId" @click="openTrace(row.traceId)">{{ row.traceId }}</span><span v-else>—</span></template></el-table-column>
          <el-table-column prop="result" label="结果" width="80" />
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
/**
 * 系统审计与日志中心（全面重做）：
 * ① getAuditStats 统计卡 + byModule 饼 / byRisk 柱 / trend 折线
 * ② listAuditLogs 检索（traceId/actorDid/action/riskLevel/from/to/keyword，分页）+ exportAuditLogs → saveBlob
 * ③ getAuditTrace：summary 卡 + el-timeline（默认填最近一条 high 日志的 traceId）
 * ④ listAlerts/ackAlert，ruleCode 中文映射；订阅 WS audit_alert 实时插入 + ElNotification
 * ⑤ getAuditReport：统计 + narrative + narrativeSource 徽章
 * ⑥ 保留 logStore 本地日志作为「前端操作记录」
 */
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts'
import { ElNotification } from 'element-plus'
import SourceBadge from '@/components/legacy/SourceBadge.vue'
import { useLogStore } from '@/stores/logs'
import { listAuditLogs, getAuditTrace, listAlerts, ackAlert, getAuditReport, getAuditStats, exportAuditLogs } from '@/api/audit'
import { saveBlob } from '@/api/request'
import { wsClient, WS_TYPES } from '@/api/ws'
import { toIso8, fmtTime, fmtDateTime, fmtDuration, shortDid, shortHash, RISK_LABELS } from '@/utils/format'

const logStore = useLogStore()

const RULE_NAMES = { R01_UNAUTHORIZED: '越权访问', R02_ABNORMAL_DID: '异常 DID 登录', R03_PERM_CHURN: '高频权限变更', R04_BULK_EXPORT: '批量数据导出', R05_SUSPICIOUS_GRAD: '可疑梯度上传' }
const MODULE_LABELS = { auth: '认证', did: 'DID', key: '密钥', asset: '资产', permission: '权限', evidence: '存证', algo: '算法', audit: '审计', node: '节点' }

const tab = ref('logs')

/* ① 统计 */
const stats = ref({})
const moduleRef = ref(null)
const riskRef = ref(null)
const trendRef = ref(null)
let moduleChart = null
let riskChart = null
let trendChart = null
const RISK_COLORS = { low: '#2ECC71', medium: '#00B4D8', high: '#F39C12', critical: '#E63946' }

async function loadStats() {
  try {
    stats.value = await getAuditStats()
    nextTick(renderStats)
  } catch { /* 已提示 */ }
}
function renderStats() {
  const s = stats.value || {}
  const tip = { backgroundColor: 'rgba(13, 27, 42, 0.95)', borderColor: '#00B4D8', textStyle: { color: '#fff', fontSize: 11 } }
  if (moduleRef.value) {
    moduleChart = moduleChart || echarts.init(moduleRef.value)
    moduleChart.setOption({
      backgroundColor: 'transparent', tooltip: { trigger: 'item', ...tip },
      series: [{ type: 'pie', radius: ['45%', '70%'], itemStyle: { borderColor: '#0a1018', borderWidth: 2, borderRadius: 4 }, label: { color: '#8892B0', fontSize: 10, formatter: '{b} {c}' }, data: (s.byModule || []).filter(m => m.count).map(m => ({ name: MODULE_LABELS[m.module] || m.module, value: m.count })) }]
    })
  }
  if (riskRef.value) {
    riskChart = riskChart || echarts.init(riskRef.value)
    const br = s.byRisk || []
    riskChart.setOption({
      backgroundColor: 'transparent', tooltip: { trigger: 'axis', ...tip },
      grid: { left: 36, right: 12, top: 16, bottom: 24 },
      xAxis: { type: 'category', data: br.map(r => RISK_LABELS[r.riskLevel] || r.riskLevel), axisLabel: { color: '#8892B0', fontSize: 10 }, axisLine: { lineStyle: { color: '#243447' } } },
      yAxis: { type: 'value', axisLabel: { color: '#8892B0', fontSize: 10 }, splitLine: { lineStyle: { color: '#243447', type: 'dashed' } } },
      series: [{ type: 'bar', barWidth: 24, data: br.map(r => ({ value: r.count, itemStyle: { color: RISK_COLORS[r.riskLevel] || '#8892B0', borderRadius: [4, 4, 0, 0] } })), label: { show: true, position: 'top', color: '#8892B0', fontSize: 10 } }]
    })
  }
  if (trendRef.value) {
    trendChart = trendChart || echarts.init(trendRef.value)
    const tr = s.trend || []
    trendChart.setOption({
      backgroundColor: 'transparent', tooltip: { trigger: 'axis', ...tip },
      legend: { data: ['总量', '高风险'], textStyle: { color: '#8892B0', fontSize: 10 }, top: 0 },
      grid: { left: 36, right: 12, top: 26, bottom: 24 },
      xAxis: { type: 'category', data: tr.map(t => t.date.slice(5)), axisLabel: { color: '#8892B0', fontSize: 10 }, axisLine: { lineStyle: { color: '#243447' } } },
      yAxis: { type: 'value', axisLabel: { color: '#8892B0', fontSize: 10 }, splitLine: { lineStyle: { color: '#243447', type: 'dashed' } } },
      series: [
        { name: '总量', type: 'line', smooth: true, data: tr.map(t => t.total), lineStyle: { color: '#00B4D8' }, itemStyle: { color: '#00B4D8' }, areaStyle: { color: 'rgba(0, 180, 216, 0.15)' } },
        { name: '高风险', type: 'line', smooth: true, data: tr.map(t => t.high), lineStyle: { color: '#E63946' }, itemStyle: { color: '#E63946' } }
      ]
    })
  }
}

/* ② 日志检索 */
const query = reactive({ traceId: '', actorDid: '', action: '', riskLevel: '', range: null, keyword: '', page: 1, size: 20 })
const logs = ref({ items: [], total: 0 })
const logsLoading = ref(false)
const exporting = ref(false)
function buildParams() {
  const p = { page: query.page, size: query.size }
  for (const k of ['traceId', 'actorDid', 'action', 'riskLevel', 'keyword']) if (query[k]) p[k] = query[k]
  if (query.range?.[0]) p.from = toIso8(query.range[0])
  if (query.range?.[1]) p.to = toIso8(query.range[1])
  return p
}
async function searchLogs() {
  logsLoading.value = true
  try { logs.value = await listAuditLogs(buildParams()) } catch { /* 已提示 */ } finally { logsLoading.value = false }
}
function resetQuery() { Object.assign(query, { traceId: '', actorDid: '', action: '', riskLevel: '', range: null, keyword: '', page: 1 }); searchLogs() }
async function exportCsv() {
  exporting.value = true
  try {
    const { page, size, ...rest } = buildParams()
    const blob = await exportAuditLogs(rest)
    const name = `audit-logs-${new Date().toISOString().slice(0, 10)}.csv`
    saveBlob(blob, name)
    logStore.addLog(`导出审计日志 CSV：${name}`, 'INFO', 'AUDIT')
  } catch { /* 已提示 */ } finally { exporting.value = false }
}
function riskRowClass({ row }) { return `risk-${row.riskLevel}` }

/* ③ 全流程追踪 */
const traceInput = ref('')
const trace = ref(null)
const traceLoading = ref(false)
const traceCandidates = ref([])
async function loadTrace() {
  const tid = traceInput.value.trim()
  if (!tid) return
  traceLoading.value = true
  try {
    trace.value = await getAuditTrace(tid)
    logStore.addLog(`全流程追踪 ${tid}：${trace.value.steps?.length || 0} 步，结果 ${trace.value.summary?.result}`, 'INFO', 'AUDIT', { traceId: tid })
  } catch { trace.value = null } finally { traceLoading.value = false }
}
function openTrace(tid) {
  if (!tid) return
  traceInput.value = tid
  tab.value = 'trace'
  loadTrace()
}
function stepType(s) {
  if (s.result === 'denied' || s.result === 'failed') return 'danger'
  if (s.riskLevel === 'high' || s.riskLevel === 'critical') return 'warning'
  if (s.module === 'evidence') return 'success'
  return 'primary'
}
/** 默认 traceId：最近一条 high 日志；并给出几条候选（越权 / fl:train / dispatch） */
async function prepareTraceCandidates() {
  const cands = []
  try {
    const high = await listAuditLogs({ riskLevel: 'high', size: 1 })
    if (high?.items?.[0]) cands.push({ traceId: high.items[0].traceId, label: `最近高风险：${high.items[0].action}` })
    const fl = await listAuditLogs({ action: 'fl:train', size: 1 })
    if (fl?.items?.[0]) cands.push({ traceId: fl.items[0].traceId, label: '联邦训练链路' })
    const dp = await listAuditLogs({ action: 'dispatch:issue', size: 1 })
    if (dp?.items?.[0]) cands.push({ traceId: dp.items[0].traceId, label: '调度下发链路' })
  } catch { /* 忽略 */ }
  traceCandidates.value = cands.filter((c, i, a) => a.findIndex(x => x.traceId === c.traceId) === i)
  if (!traceInput.value && cands[0]) traceInput.value = cands[0].traceId
}

/* ④ 告警 */
const alertStatus = ref('open')
const alerts = ref([])
const openCount = computed(() => alerts.value.filter(a => a.status !== 'acked').length || stats.value.openAlerts || 0)
async function loadAlerts() {
  try {
    const params = alertStatus.value ? { status: alertStatus.value, size: 100 } : { size: 100 }
    alerts.value = (await listAlerts(params))?.items || []
  } catch { /* 已提示 */ }
}
async function ack(row) {
  try {
    await logStore.ackAlert(row.id)
    row.status = 'acked'
    if (alertStatus.value === 'open') alerts.value = alerts.value.filter(a => a.id !== row.id)
    loadStats()
  } catch { /* 已提示 */ }
}
let offWs = null
function onAlert(p, msg) {
  const item = { id: p.alertId, ruleCode: p.ruleCode, ruleName: RULE_NAMES[p.ruleCode], riskLevel: p.riskLevel, message: p.message, actorDid: p.actorDid, status: 'open', traceId: msg?.traceId, at: msg?.ts || new Date().toISOString() }
  if (alertStatus.value !== 'acked' && !alerts.value.find(a => a.id === item.id)) alerts.value.unshift(item)
  stats.value = { ...stats.value, openAlerts: (stats.value.openAlerts || 0) + 1, highRiskLogs: (stats.value.highRiskLogs || 0) + (p.riskLevel === 'high' || p.riskLevel === 'critical' ? 1 : 0) }
  ElNotification({
    title: `风险告警 ${p.ruleCode?.slice(0, 3)} · ${RULE_NAMES[p.ruleCode] || p.ruleCode}`,
    message: `${p.message}（${RISK_LABELS[p.riskLevel] || p.riskLevel}风险）`,
    type: p.riskLevel === 'critical' || p.riskLevel === 'high' ? 'error' : 'warning',
    duration: 6000
  })
}

/* ⑤ 报告 */
const report = reactive({ period: 'day', date: toIso8(new Date()).slice(0, 10), data: null })
const reportLoading = ref(false)
async function loadReport() {
  reportLoading.value = true
  try {
    report.data = await getAuditReport({ period: report.period, date: report.date })
    logStore.addLog(`生成审计${{ day: '日报', week: '周报', month: '月报' }[report.period]} ${report.date}（叙述来源 ${report.data.narrativeSource}）`, 'INFO', 'AUDIT')
  } catch { /* 已提示 */ } finally { reportLoading.value = false }
}

/* ⑥ 本地日志 */
const levelFilter = ref('ALL')
const sourceFilter = ref('ALL')
const sources = computed(() => [...new Set(logStore.logs.map(l => l.source))])
const localLogs = computed(() => logStore.logs.slice().reverse().filter(l => (levelFilter.value === 'ALL' || l.level === levelFilter.value) && (sourceFilter.value === 'ALL' || l.source === sourceFilter.value)))

function onResize() { moduleChart?.resize(); riskChart?.resize(); trendChart?.resize() }

onMounted(async () => {
  logStore.addLog('进入系统审计与日志中心', 'INFO', 'AUDIT')
  offWs = wsClient.on(WS_TYPES.AUDIT_ALERT, onAlert)
  await Promise.all([loadStats(), searchLogs(), loadAlerts(), prepareTraceCandidates()])
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  offWs && offWs()
  moduleChart?.dispose(); riskChart?.dispose(); trendChart?.dispose()
  window.removeEventListener('resize', onResize)
})
</script>

<style scoped>
.audit-container { height: 100%; display: flex; flex-direction: column; gap: 14px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; }
.page-title { font-size: 24px; color: var(--color-text); margin-bottom: 6px; }
.page-desc { color: var(--color-text-secondary); font-size: 13px; }
.stats-bar { display: flex; gap: 12px; }
.stat-item { flex: 1; display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-radius: 10px; border: 1px solid rgba(0, 180, 216, 0.15); border-left: 3px solid var(--color-primary); }
.stat-item.error { border-left-color: var(--color-danger); }
.stat-item.warn { border-left-color: var(--color-warning); }
.stat-item.success { border-left-color: var(--color-success); }
.stat-item.info { border-left-color: var(--color-text-secondary); }
.stat-icon { font-size: 18px; }
.stat-label { font-size: 12px; color: var(--color-text-secondary); }
.stat-value { margin-left: auto; font-size: 20px; font-weight: 700; color: var(--color-text); }
.charts-row { display: grid; grid-template-columns: 1fr 1fr 1.6fr; gap: 12px; }
.chart-card { border-radius: 10px; padding: 10px 12px; border: 1px solid rgba(0, 180, 216, 0.12); }
.chart-title { font-size: 12px; color: var(--color-primary); font-weight: 600; margin-bottom: 4px; }
.chart { height: 150px; }
.audit-tabs { flex: 1; border-radius: 12px; padding: 0 14px 14px; border: 1px solid rgba(0, 180, 216, 0.15); }
.audit-tabs :deep(.el-tabs__item) { color: var(--color-text-secondary); }
.audit-tabs :deep(.el-tabs__item.is-active) { color: var(--color-primary); }
.audit-tabs :deep(.el-tabs__nav-wrap::after) { background: rgba(0, 180, 216, 0.15); }
.filter-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.filter-label { font-size: 12px; color: var(--color-text-secondary); }
.pager { display: flex; justify-content: flex-end; margin-top: 10px; }
.mono { font-family: 'Consolas', monospace; }
.small { font-size: 11px; }
.link { cursor: pointer; color: var(--color-primary); }
.muted { font-size: 12px; color: var(--color-text-secondary); }
.level-badge, .result-badge { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.level-badge.low { color: var(--color-success); background: rgba(46, 204, 113, 0.15); }
.level-badge.medium { color: var(--color-primary); background: rgba(0, 180, 216, 0.15); }
.level-badge.high { color: var(--color-warning); background: rgba(243, 156, 18, 0.15); }
.level-badge.critical { color: var(--color-danger); background: rgba(230, 57, 70, 0.2); }
.result-badge.success, .result-badge.allowed { color: var(--color-success); background: rgba(46, 204, 113, 0.15); }
.result-badge.denied, .result-badge.failed { color: var(--color-danger); background: rgba(230, 57, 70, 0.15); }
:deep(.risk-high td) { background: rgba(243, 156, 18, 0.06) !important; }
:deep(.risk-critical td) { background: rgba(230, 57, 70, 0.08) !important; }
.trace-board { display: flex; flex-direction: column; gap: 14px; }
.trace-summary { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; }
.summary-card { display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(0, 180, 216, 0.15); min-width: 0; }
.summary-label { font-size: 11px; color: var(--color-text-secondary); }
.summary-value { font-size: 13px; color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trace-timeline { padding-left: 4px; }
.trace-timeline :deep(.el-timeline-item__timestamp) { color: var(--color-text-secondary); }
.step-card { padding: 8px 12px; border-radius: 8px; border: 1px solid rgba(0, 180, 216, 0.15); background: rgba(0, 180, 216, 0.04); }
.step-card.denied, .step-card.failed { border-color: rgba(230, 57, 70, 0.5); background: rgba(230, 57, 70, 0.08); }
.step-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.step-seq { font-size: 11px; color: var(--color-text-secondary); }
.step-module { font-size: 12px; font-weight: 600; color: var(--color-primary); }
.step-action { font-size: 12px; color: var(--color-text); }
.step-detail { font-size: 12px; color: var(--color-text); margin-top: 4px; }
.step-meta { font-size: 11px; color: var(--color-text-secondary); margin-top: 2px; }
.rule-legend { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; }
.rule-chip { font-size: 10px; padding: 1px 8px; border-radius: 999px; border: 1px solid rgba(0, 180, 216, 0.3); color: var(--color-text-secondary); }
.report-board { display: flex; flex-direction: column; gap: 12px; }
.narrative { padding: 12px 16px; border-radius: 10px; background: rgba(0, 180, 216, 0.06); border-left: 3px solid var(--color-primary); font-size: 14px; line-height: 1.8; color: var(--color-text); }
.report-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
.report-card { padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(0, 180, 216, 0.15); }
.rc-title { font-size: 12px; color: var(--color-primary); font-weight: 600; margin-bottom: 6px; }
.rc-row { display: flex; justify-content: space-between; font-size: 12px; color: var(--color-text-secondary); padding: 2px 0; }
.rc-row b { color: var(--color-text); }
.lvl { font-weight: 700; font-size: 11px; }
.lvl.info { color: var(--color-primary); } .lvl.warn { color: var(--color-warning); } .lvl.error { color: var(--color-danger); }
.empty-state { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 40px; color: var(--color-text-secondary); }
.empty-icon { font-size: 32px; }
.empty-text { font-size: 13px; }
@media (max-width: 1200px) { .charts-row { grid-template-columns: 1fr 1fr; } .chart-card.wide { grid-column: 1 / -1; } .trace-summary { grid-template-columns: repeat(3, 1fr); } .report-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
