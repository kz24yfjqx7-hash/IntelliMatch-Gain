<template>
  <CenterPage icon="🗂️" title="能源数据资产中心" desc="数据登记 · 自动分类分级 · SM3 摘要上链 · 溯源链路 · 授权入口">
    <template #actions>
      <el-button v-permission="'asset:write'" type="primary" @click="openRegister">＋ 登记资产</el-button>
      <el-button @click="refreshAll" :loading="statsLoading">刷新</el-button>
    </template>

    <!-- ① 统计 + 图表 -->
    <div class="stat-row">
      <StatCard icon="🗂️" label="资产总数" :value="stats.total" :loading="statsLoading" />
      <StatCard icon="🔓" label="已授权" :value="stats.authorized" tone="success" :loading="statsLoading" />
      <StatCard icon="⛓️" label="已上链" :value="stats.onChain" tone="primary" :loading="statsLoading" />
      <StatCard v-for="l in stats.byLevel" :key="l.level" :icon="LEVEL_ICON[l.level]" :label="LEVEL_LABELS[l.level]" :value="l.count" :tone="LEVEL_TONE[l.level]" :loading="statsLoading" clickable @click="filterLevel(l.level)" />
    </div>
    <div class="grid-2">
      <div class="panel">
        <div class="panel-title">敏感等级分布 <span class="sub">byLevel · L1 公开 / L2 内部 / L3 敏感 / L4 核心</span></div>
        <EChart :option="levelOption" height="220px" @click="p => filterLevel(p.name)" />
      </div>
      <div class="panel">
        <div class="panel-title">数据类型分布 <span class="sub">byType</span></div>
        <EChart :option="typeOption" height="220px" @click="p => filterType(p.name)" />
      </div>
    </div>

    <!-- ③ 资产列表 -->
    <div class="panel">
      <div class="panel-title">资产列表 <span class="sub">链上只存 SM3 摘要，原始 payload 存库</span></div>
      <div class="toolbar">
        <el-select v-model="query.dataType" placeholder="数据类型" clearable style="width: 120px" popper-class="center-popper" @change="reload">
          <el-option v-for="(label, v) in DATA_TYPE_LABELS" :key="v" :label="label" :value="v" />
        </el-select>
        <el-select v-model="query.level" placeholder="等级" clearable style="width: 120px" popper-class="center-popper" @change="reload">
          <el-option v-for="(label, v) in LEVEL_LABELS" :key="v" :label="label" :value="v" />
        </el-select>
        <el-select v-model="query.sourceDid" placeholder="数据源 DID" clearable filterable style="width: 300px" popper-class="center-popper" @change="reload">
          <el-option v-for="d in sourceOptions" :key="d.did" :label="`${d.subjectName}（${d.subjectType}）${shortDid(d.did)}`" :value="d.did" />
        </el-select>
        <el-input v-model="query.keyword" placeholder="名称 / ID / 哈希" clearable style="width: 200px" @keyup.enter="reload" @clear="reload" />
        <el-button @click="reload">查询</el-button>
      </div>
      <el-table :data="list.items" v-loading="list.loading" size="small" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="资产名称" min-width="200" />
        <el-table-column label="类型" width="80">
          <template #default="{ row }">{{ DATA_TYPE_LABELS[row.dataType] || row.dataType }}</template>
        </el-table-column>
        <el-table-column label="等级" width="90">
          <template #default="{ row }"><span class="level-badge" :class="row.level">{{ row.level }}</span></template>
        </el-table-column>
        <el-table-column label="数据源 DID" min-width="200">
          <template #default="{ row }"><HashText :value="row.sourceDid" :head="22" /></template>
        </el-table-column>
        <el-table-column label="SM3 摘要" min-width="150">
          <template #default="{ row }"><HashText :value="row.hash" /></template>
        </el-table-column>
        <el-table-column label="授权" width="90">
          <template #default="{ row }">
            <el-tag :type="row.authStatus === 'authorized' ? 'success' : 'info'" size="small" effect="dark">{{ row.authStatus === 'authorized' ? '已授权' : '未授权' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="evidenceId" label="存证" width="100" />
        <el-table-column label="登记时间" width="150">
          <template #default="{ row }">{{ fmtDateTime(row.createdAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="viewDetail(row)">详情</el-button>
            <el-button link size="small" @click="viewLineage(row)">溯源</el-button>
            <el-button link type="warning" size="small" @click="openApply(row)">申请授权</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination v-model:current-page="query.page" v-model:page-size="query.size" :total="list.total" layout="total, sizes, prev, pager, next" :page-sizes="[10, 20, 50]" @current-change="load" @size-change="reload" />
      </div>
    </div>

    <!-- ② 登记对话框：左表单 / 右分级结果 -->
    <el-dialog v-model="registerVisible" title="数据资产登记" width="960px" class="center-dialog" destroy-on-close>
      <div class="reg-grid">
        <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
          <el-form-item label="资产名称" prop="name"><el-input v-model="form.name" placeholder="如：节点A光伏出力-20260817" /></el-form-item>
          <el-form-item label="数据类型" prop="dataType">
            <el-radio-group v-model="form.dataType">
              <el-radio-button v-for="(label, v) in DATA_TYPE_LABELS" :key="v" :value="v">{{ label }}</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="数据源 DID" prop="sourceDid">
            <el-select v-model="form.sourceDid" filterable style="width: 100%" popper-class="center-popper" placeholder="device / edge 类型的活跃 DID">
              <el-option v-for="d in sourceOptions" :key="d.did" :label="`${d.subjectName}（${d.subjectType}）${shortDid(d.did)}`" :value="d.did" />
            </el-select>
          </el-form-item>
          <el-form-item label="采集粒度">
            <el-select v-model="form.freq" style="width: 160px" popper-class="center-popper">
              <el-option label="秒级 second" value="second" />
              <el-option label="分钟级 minute" value="minute" />
              <el-option label="15 分钟 15min" value="15min" />
              <el-option label="小时级 hour" value="hour" />
              <el-option label="日级 day" value="day" />
            </el-select>
            <span class="muted" style="margin-left: 12px">数据量(条)</span>
            <el-input-number v-model="form.volume" :min="1" :max="100000000" :step="1000" style="margin-left: 8px" />
          </el-form-item>
          <el-form-item label="payload" prop="payloadText">
            <el-input v-model="form.payloadText" type="textarea" :rows="5" class="mono" />
          </el-form-item>
          <el-form-item label="描述"><el-input v-model="form.description" /></el-form-item>
          <el-form-item label="敏感等级">
            <el-radio-group v-model="form.level">
              <el-radio-button v-for="(label, v) in LEVEL_LABELS" :key="v" :value="v">{{ label }}</el-radio-button>
            </el-radio-group>
            <el-button style="margin-left: 12px" :loading="classifying" @click="autoClassify">⚙ 自动分级</el-button>
          </el-form-item>
        </el-form>
        <div class="classify-box">
          <div class="panel-title">分类分级结果 <span class="sub">k-means(k=3) + 规则加权</span></div>
          <template v-if="classifyResult">
            <div class="classify-head">
              <span class="level-badge big" :class="classifyResult.level">{{ classifyResult.level }}</span>
              <div>
                <div>score <b class="mono">{{ classifyResult.score }}</b> · 簇 #{{ classifyResult.cluster }}</div>
                <div class="muted" style="font-size: 12px">{{ classifyResult.reason }}</div>
              </div>
            </div>
            <EChart :option="radarOption" height="220px" />
            <div class="muted" style="font-size: 12px">阈值：L1 &lt;0.30 ≤ L2 &lt;0.50 ≤ L3 &lt;0.70 ≤ L4；可手动改级后提交</div>
          </template>
          <div v-else class="empty-tip">填写类型 / payload / 粒度 / 数据量后点击「自动分级」，算法服务返回等级、得分与因子</div>
        </div>
      </div>
      <template #footer>
        <el-button @click="registerVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitRegister">登记并上链</el-button>
      </template>
    </el-dialog>

    <!-- 登记结果 -->
    <el-dialog v-model="resultVisible" title="登记成功 · 已写入存证链" width="560px" class="center-dialog">
      <el-descriptions v-if="result" :column="1" border size="small">
        <el-descriptions-item label="资产 ID">{{ result.id }}</el-descriptions-item>
        <el-descriptions-item label="敏感等级"><span class="level-badge" :class="result.level">{{ result.level }}</span></el-descriptions-item>
        <el-descriptions-item label="SM3 摘要"><span class="mono">{{ result.hash }}</span></el-descriptions-item>
        <el-descriptions-item label="链上交易">{{ result.chainTxId }}</el-descriptions-item>
        <el-descriptions-item label="存证 ID">{{ result.evidenceId }}</el-descriptions-item>
        <el-descriptions-item label="授权状态">{{ result.authStatus }}</el-descriptions-item>
        <el-descriptions-item label="traceId"><span class="mono">{{ result.traceId || '--' }}</span></el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button @click="resultVisible = false">关闭</el-button>
        <el-button @click="resultVisible = false; viewLineage({ id: result.id, name: form.name })">查看溯源</el-button>
        <el-button type="primary" @click="resultVisible = false; openApply({ id: result.id, name: form.name })">申请授权</el-button>
      </template>
    </el-dialog>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailVisible" :title="`资产详情 · ${detail?.name || ''}`" size="560px" class="center-dialog">
      <template v-if="detail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="ID / 类型">{{ detail.id }} / {{ DATA_TYPE_LABELS[detail.dataType] }}</el-descriptions-item>
          <el-descriptions-item label="等级"><span class="level-badge" :class="detail.level">{{ detail.level }}</span></el-descriptions-item>
          <el-descriptions-item label="数据源 DID"><span class="mono">{{ detail.sourceDid }}</span></el-descriptions-item>
          <el-descriptions-item label="所有者 DID"><span class="mono">{{ detail.ownerDid }}</span></el-descriptions-item>
          <el-descriptions-item label="SM3 摘要"><span class="mono">{{ detail.hash }}</span></el-descriptions-item>
          <el-descriptions-item label="链上交易 / 存证">{{ detail.chainTxId }} / {{ detail.evidenceId }}</el-descriptions-item>
          <el-descriptions-item label="授权状态">{{ detail.authStatus }}</el-descriptions-item>
          <el-descriptions-item label="描述">{{ detail.description || '--' }}</el-descriptions-item>
          <el-descriptions-item label="登记时间">{{ fmtDateTime(detail.createdAt) }}</el-descriptions-item>
        </el-descriptions>
        <div style="margin-top: 12px"><JsonViewer :value="detail.payload" title="payload（原始数据，存 MySQL）" /></div>
      </template>
    </el-drawer>

    <!-- 溯源抽屉 -->
    <el-drawer v-model="lineageVisible" :title="`溯源链路 · 资产 #${lineage?.assetId || ''}`" size="720px" class="center-dialog">
      <template v-if="lineage">
        <p class="muted" style="margin-bottom: 12px">traceId <span class="mono">{{ lineage.traceId || '--' }}</span> · 共 {{ (lineage.chain || []).length }} 个环节</p>
        <div class="lineage">
          <div v-for="stage in STAGES" :key="stage.key" class="lineage-stage" :class="{ present: stageItems(stage.key).length }">
            <div class="lineage-node">
              <div class="lineage-icon">{{ stage.icon }}</div>
              <div class="lineage-name">{{ stage.label }}</div>
              <div class="lineage-count">{{ stageItems(stage.key).length ? `${stageItems(stage.key).length} 次` : '未发生' }}</div>
            </div>
            <div class="lineage-items">
              <div v-for="(it, i) in stageItems(stage.key)" :key="i" class="lineage-item">
                <div class="muted">{{ fmtDateTime(it.at) }}</div>
                <div>actor <HashText :value="it.actorDid" :head="20" /></div>
                <div>存证 <span class="mono">{{ it.evidenceId || '--' }}</span></div>
                <div v-if="it.hash">hash <HashText :value="it.hash" /></div>
                <div v-if="it.detail" class="muted">{{ it.detail }}</div>
              </div>
            </div>
          </div>
        </div>
        <div class="panel-title" style="margin-top: 16px">链路图</div>
        <EChart :option="lineageGraphOption" height="200px" />
      </template>
    </el-drawer>

    <!-- 申请授权 -->
    <el-dialog v-model="applyVisible" :title="`申请授权 · 资产 #${applyForm.resourceId}`" width="520px" class="center-dialog">
      <el-form :model="applyForm" label-width="90px">
        <el-form-item label="资源">asset / {{ applyForm.resourceId }} <span class="muted">{{ applyForm.name }}</span></el-form-item>
        <el-form-item label="操作 action">
          <el-radio-group v-model="applyForm.action">
            <el-radio-button value="read">read</el-radio-button>
            <el-radio-button value="write">write</el-radio-button>
            <el-radio-button value="execute">execute</el-radio-button>
            <el-radio-button value="export">export</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="申请理由"><el-input v-model="applyForm.reason" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="有效期至">
          <el-date-picker v-model="applyForm.expireAt" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss+08:00" style="width: 100%" popper-class="center-popper" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="applyVisible = false">取消</el-button>
        <el-button type="primary" :loading="applying" @click="submitApply">提交申请</el-button>
      </template>
    </el-dialog>
  </CenterPage>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { registerAsset, listAssets, getAsset, getAssetLineage, classifyAssets, getAssetStats, listDids, applyPermission } from '@/api'
import { useLogStore } from '@/stores/logs'
import { LEVEL_LABELS, DATA_TYPE_LABELS, fmtDateTime, shortDid, toIso8 } from '@/utils/format'
import CenterPage from '@/components/center/CenterPage.vue'
import StatCard from '@/components/center/StatCard.vue'
import HashText from '@/components/center/HashText.vue'
import JsonViewer from '@/components/center/JsonViewer.vue'
import EChart from '@/components/center/EChart.vue'
import { LEVEL_COLORS, CHART_COLORS, AXIS, TOOLTIP } from '@/components/center/chartTheme.js'

const logStore = useLogStore()

const LEVEL_ICON = { L1: '🟢', L2: '🔵', L3: '🟠', L4: '🔴' }
const LEVEL_TONE = { L1: 'success', L2: 'primary', L3: 'warning', L4: 'danger' }
const STAGES = [
  { key: 'register', icon: '📝', label: '登记 register' },
  { key: 'authorize', icon: '🔑', label: '授权 authorize' },
  { key: 'access', icon: '👁️', label: '访问 access' },
  { key: 'compute', icon: '🧮', label: '计算 compute' }
]

/* ---------- 统计 ---------- */
const stats = reactive({ total: null, authorized: null, onChain: null, byLevel: [], byType: [] })
const statsLoading = ref(false)
async function loadStats() {
  statsLoading.value = true
  try {
    Object.assign(stats, await getAssetStats())
  } catch { /* 拦截器已提示 */ } finally { statsLoading.value = false }
}
const levelOption = computed(() => ({
  tooltip: TOOLTIP,
  legend: { bottom: 0, textStyle: { color: '#8892B0' } },
  series: [{
    type: 'pie', radius: ['40%', '68%'], center: ['50%', '45%'],
    label: { color: '#fff', formatter: '{b}\n{c} 条' },
    data: (stats.byLevel || []).map(l => ({ name: l.level, value: l.count, itemStyle: { color: LEVEL_COLORS[l.level] } }))
  }]
}))
const typeOption = computed(() => ({
  tooltip: { ...TOOLTIP, trigger: 'axis' },
  grid: { left: 40, right: 16, top: 20, bottom: 30 },
  xAxis: { type: 'category', data: (stats.byType || []).map(t => t.dataType), ...AXIS },
  yAxis: { type: 'value', ...AXIS },
  series: [{
    type: 'bar', barWidth: 28,
    data: (stats.byType || []).map((t, i) => ({ value: t.count, name: t.dataType, itemStyle: { color: CHART_COLORS[i % CHART_COLORS.length], borderRadius: [4, 4, 0, 0] } })),
    label: { show: true, position: 'top', color: '#fff', formatter: p => `${DATA_TYPE_LABELS[p.name] || p.name} ${p.value}` }
  }]
}))

/* ---------- 数据源 DID ---------- */
const sourceOptions = ref([])
async function loadSources() {
  try {
    const [dev, edge] = await Promise.all([listDids({ subjectType: 'device', status: 'active', size: 200 }), listDids({ subjectType: 'edge', status: 'active', size: 200 })])
    sourceOptions.value = [...(dev.items || []), ...(edge.items || [])]
  } catch { /* 忽略 */ }
}

/* ---------- 列表 ---------- */
const query = reactive({ dataType: '', level: '', sourceDid: '', keyword: '', page: 1, size: 10 })
const list = reactive({ items: [], total: 0, loading: false })
async function load() {
  list.loading = true
  try {
    const data = await listAssets({ page: query.page, size: query.size, dataType: query.dataType || undefined, level: query.level || undefined, sourceDid: query.sourceDid || undefined, keyword: query.keyword || undefined })
    list.items = data.items || []; list.total = data.total || 0
  } catch { /* 拦截器已提示 */ } finally { list.loading = false }
}
function reload() { query.page = 1; load() }
function filterLevel(level) { query.level = query.level === level ? '' : level; reload() }
function filterType(t) { query.dataType = query.dataType === t ? '' : t; reload() }

/* ---------- 登记 ---------- */
const registerVisible = ref(false)
const formRef = ref()
const submitting = ref(false)
const classifying = ref(false)
const classifyResult = ref(null)
const clusterCenters = ref([])
const form = reactive({ name: '', dataType: 'pv', sourceDid: '', freq: 'minute', volume: 1440, payloadText: '', description: '分钟级采集', level: '' })
const rules = {
  name: [{ required: true, message: '请输入资产名称', trigger: 'blur' }],
  sourceDid: [{ required: true, message: '请选择数据源 DID', trigger: 'change' }],
  payloadText: [{ validator: (_, v, cb) => { try { JSON.parse(v); cb() } catch { cb(new Error('payload 须为合法 JSON')) } }, trigger: 'blur' }]
}
function openRegister() {
  const ts = toIso8(new Date())
  Object.assign(form, { name: `节点A光伏出力-${ts.slice(0, 10).replace(/-/g, '')}`, dataType: 'pv', sourceDid: sourceOptions.value[0]?.did || '', freq: 'minute', volume: 1440, payloadText: JSON.stringify({ pvOutput: 45.3, voltage: 380.2, ts }, null, 2), description: '分钟级采集', level: '' })
  classifyResult.value = null
  registerVisible.value = true
}
function parsePayload() {
  try { return JSON.parse(form.payloadText || '{}') } catch { ElMessage.warning('payload 不是合法 JSON'); return null }
}
async function autoClassify() {
  const payload = parsePayload()
  if (!payload) return
  classifying.value = true
  try {
    const data = await classifyAssets({ records: [{ dataType: form.dataType, fields: Object.keys(payload), freq: form.freq, volume: form.volume }] })
    const r = (data.results || [])[0]
    if (r) {
      classifyResult.value = r
      clusterCenters.value = data.clusterCenters || []
      form.level = r.level
      logStore.addLog(`自动分级：${form.dataType} → ${r.level}（score ${r.score}，${r.reason}）`, 'INFO', 'ASSET')
    }
  } catch { /* 拦截器已提示 */ } finally { classifying.value = false }
}
const radarOption = computed(() => {
  const f = classifyResult.value?.factors || {}
  const centers = clusterCenters.value || []
  const series = [{ value: [f.sensitivity ?? 0, f.granularity ?? 0, f.volume ?? 0], name: '本条数据', areaStyle: { color: 'rgba(0,180,216,.35)' }, lineStyle: { color: '#00B4D8' }, itemStyle: { color: '#00B4D8' } }]
  const c = centers[classifyResult.value?.cluster ?? -1]
  if (c) series.push({ value: c, name: `簇中心 #${classifyResult.value.cluster}`, lineStyle: { color: '#F39C12', type: 'dashed' }, itemStyle: { color: '#F39C12' } })
  return {
    tooltip: TOOLTIP,
    legend: { bottom: 0, textStyle: { color: '#8892B0' } },
    radar: { indicator: [{ name: '敏感度', max: 1 }, { name: '粒度', max: 1 }, { name: '数据量', max: 1 }], radius: '62%', axisName: { color: '#8892B0' }, splitLine: { lineStyle: { color: 'rgba(0,180,216,.2)' } }, splitArea: { areaStyle: { color: ['rgba(0,180,216,.03)', 'rgba(0,180,216,.06)'] } } },
    series: [{ type: 'radar', data: series }]
  }
})
const resultVisible = ref(false)
const result = ref(null)
async function submitRegister() {
  try { await formRef.value?.validate() } catch { return }
  const payload = parsePayload()
  if (!payload) return
  submitting.value = true
  try {
    const data = await registerAsset({ name: form.name, dataType: form.dataType, sourceDid: form.sourceDid, level: form.level || undefined, payload, description: form.description, freq: form.freq, volume: form.volume })
    result.value = data
    resultVisible.value = true
    registerVisible.value = false
    logStore.addLog(`登记资产 #${data.id}「${form.name}」${data.level}，摘要 ${data.hash.slice(0, 18)}… 上链 ${data.chainTxId}，存证 ${data.evidenceId}`, 'INFO', 'ASSET', { traceId: data.traceId })
    reload(); loadStats()
  } catch { /* 拦截器已提示 */ } finally { submitting.value = false }
}

/* ---------- 详情 / 溯源 ---------- */
const detailVisible = ref(false)
const detail = ref(null)
async function viewDetail(row) {
  try { detail.value = await getAsset(row.id); detailVisible.value = true } catch { /* 拦截器已提示 */ }
}
const lineageVisible = ref(false)
const lineage = ref(null)
async function viewLineage(row) {
  try {
    lineage.value = await getAssetLineage(row.id)
    lineageVisible.value = true
    logStore.addLog(`查看资产 #${row.id} 溯源链路（${(lineage.value.chain || []).length} 环节）`, 'INFO', 'ASSET')
  } catch { /* 拦截器已提示 */ }
}
function stageItems(key) { return (lineage.value?.chain || []).filter(c => c.stage === key) }
const lineageGraphOption = computed(() => {
  const chain = lineage.value?.chain || []
  const nodes = chain.map((c, i) => ({ id: String(i), name: `${STAGES.find(s => s.key === c.stage)?.label.split(' ')[0] || c.stage}\n${c.evidenceId || ''}`, x: i * 120, y: (i % 2) * 40, symbolSize: 34, itemStyle: { color: CHART_COLORS[STAGES.findIndex(s => s.key === c.stage) % CHART_COLORS.length] }, label: { show: true, color: '#fff', fontSize: 11, position: 'bottom' } }))
  const links = chain.slice(1).map((_, i) => ({ source: String(i), target: String(i + 1), lineStyle: { color: '#00B4D8', width: 2, curveness: .15 } }))
  return { tooltip: TOOLTIP, series: [{ type: 'graph', layout: 'none', data: nodes, links, edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 8, roam: true, left: 30, right: 30 }] }
})

/* ---------- 申请授权 ---------- */
const applyVisible = ref(false)
const applying = ref(false)
const applyForm = reactive({ resourceId: '', name: '', action: 'read', reason: '', expireAt: '' })
function openApply(row) {
  Object.assign(applyForm, { resourceId: String(row.id), name: row.name || '', action: 'read', reason: `联合建模需要读取「${row.name || row.id}」`, expireAt: toIso8(new Date(Date.now() + 30 * 86400000)) })
  applyVisible.value = true
}
async function submitApply() {
  applying.value = true
  try {
    const data = await applyPermission({ resourceType: 'asset', resourceId: applyForm.resourceId, action: applyForm.action, reason: applyForm.reason, expireAt: applyForm.expireAt || undefined })
    ElMessage.success(`申请已提交 #${data.id}，等待审批`)
    logStore.addLog(`提交权限申请 #${data.id}：asset:${applyForm.action}（资产 ${applyForm.resourceId}），存证 ${data.evidenceId}`, 'INFO', 'PERMISSION')
    applyVisible.value = false
  } catch { /* 拦截器已提示 */ } finally { applying.value = false }
}

function refreshAll() { loadStats(); load(); loadSources() }
onMounted(refreshAll)
</script>

<style scoped>
.level-badge { display: inline-block; min-width: 34px; text-align: center; padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 700; font-family: Consolas, monospace; border: 1px solid; }
.level-badge.big { font-size: 26px; padding: 6px 16px; border-radius: 12px; }
.level-badge.L1 { color: #2ECC71; border-color: #2ECC71; background: rgba(46, 204, 113, .12); }
.level-badge.L2 { color: #00B4D8; border-color: #00B4D8; background: rgba(0, 180, 216, .12); }
.level-badge.L3 { color: #F39C12; border-color: #F39C12; background: rgba(243, 156, 18, .12); }
.level-badge.L4 { color: #E63946; border-color: #E63946; background: rgba(230, 57, 70, .12); }
.reg-grid { display: grid; grid-template-columns: 1fr 340px; gap: 20px; }
.classify-box { padding: 12px; border-radius: 10px; border: 1px dashed rgba(0, 180, 216, .3); background: rgba(0, 180, 216, .04); }
.classify-head { display: flex; align-items: center; gap: 14px; margin-bottom: 8px; }
.lineage { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.lineage-stage { opacity: .45; }
.lineage-stage.present { opacity: 1; }
.lineage-node { position: relative; text-align: center; padding: 10px 6px; border-radius: 10px; border: 1px solid rgba(0, 180, 216, .3); background: rgba(0, 180, 216, .06); }
.lineage-stage.present .lineage-node { border-color: var(--color-primary); box-shadow: 0 0 14px rgba(0, 180, 216, .25); }
.lineage-stage:not(:last-child) .lineage-node::after { content: '➜'; position: absolute; right: -14px; top: 50%; transform: translateY(-50%); color: var(--color-primary); }
.lineage-icon { font-size: 22px; }
.lineage-name { font-size: 12px; margin-top: 4px; }
.lineage-count { font-size: 11px; color: var(--color-text-secondary); }
.lineage-items { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.lineage-item { padding: 8px; border-radius: 8px; background: rgba(0, 0, 0, .35); border: 1px solid rgba(0, 180, 216, .15); font-size: 12px; line-height: 1.7; word-break: break-all; }
@media (max-width: 1000px) { .reg-grid { grid-template-columns: 1fr; } }
</style>
