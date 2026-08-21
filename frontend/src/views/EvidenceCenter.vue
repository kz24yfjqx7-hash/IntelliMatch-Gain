<template>
  <CenterPage icon="⛓️" title="区块链存证中心" desc="本地哈希链 · SM3 摘要 · 完整性校验 · 篡改演示 · 凭证导出 · 业务链路追踪">
    <template #actions>
      <el-button v-if="userStore.hasRole('sys_admin')" type="danger" plain @click="openTamper">⚠ 篡改演示</el-button>
      <el-button @click="refreshAll" :loading="statusLoading">刷新</el-button>
    </template>

    <!-- ① 链状态 -->
    <div class="panel" :class="{ danger: chain && !chain.intact }">
      <div class="panel-title">
        <span>链状态 <span class="sub">GET /evidence/chain/status · block_hash = H(prev + payloadHash + ts)</span></span>
        <span v-if="chain" class="chain-flag" :class="chain.intact ? 'ok' : 'bad'">{{ chain.intact ? '✔ 链完整 intact' : `✘ 链断裂 brokenAt 高度 ${chain.brokenAt}` }}</span>
      </div>
      <div class="status-grid">
        <div class="stat-row">
          <StatCard icon="📦" label="链高度 height" :value="chain?.height" :loading="statusLoading" />
          <StatCard icon="🧾" label="存证总数" :value="chain?.totalRecords" :loading="statusLoading" />
          <StatCard icon="🛡️" :label="chain?.intact === false ? '断裂高度 brokenAt' : '完整性'" :value="chain ? (chain.intact ? 'INTACT' : chain.brokenAt) : '--'" :tone="chain?.intact === false ? 'danger' : 'success'" :loading="statusLoading" />
          <StatCard icon="⚠️" label="被篡改记录" :value="(chain?.tamperedIds || []).length" :tone="(chain?.tamperedIds || []).length ? 'danger' : 'primary'" :loading="statusLoading" />
          <div class="last-hash">
            <div class="muted">lastHash</div>
            <div class="mono">{{ chain?.lastHash || '--' }}</div>
            <div class="muted" style="margin-top: 6px">算法 {{ chain?.algorithm || 'SM3' }}</div>
          </div>
        </div>
        <EChart :option="categoryOption" height="170px" />
      </div>
    </div>

    <!-- ② 区块链可视化 -->
    <div class="panel">
      <div class="panel-title">
        <span>最近 {{ blocks.length }} 个区块 <span class="sub">实时：WS evidence_written 到达即追加</span></span>
        <span class="muted" style="font-size: 12px">红色闪烁 = 本地数据与链上摘要不一致；黄色 = 断裂点之后受影响</span>
      </div>
      <div class="chain-scroll" ref="chainScrollRef">
        <div class="chain-row">
          <template v-for="b in blocks" :key="b.evidenceId">
          <div v-if="b.gap" class="block-gap" title="中间区块省略">···</div>
          <div v-else class="block" :class="blockClass(b)" @click="viewDetail(b)">
            <div class="block-h">#{{ b.blockHeight }}</div>
            <div class="block-cat" :style="{ color: CATEGORY_COLORS[b.category] }">{{ CATEGORY_LABELS[b.category] || b.category }}</div>
            <div class="block-hash mono">{{ hash8(b.hash) }}</div>
            <div class="block-id mono">{{ b.evidenceId }}</div>
            <div class="block-link" />
          </div>
          </template>
          <div v-if="!blocks.length" class="empty-tip">暂无区块</div>
        </div>
      </div>
    </div>

    <div class="grid-2 evidence-grid">
      <!-- ③ 存证检索 -->
      <div class="panel">
        <div class="panel-title">存证检索</div>
        <div class="toolbar">
          <el-select v-model="query.category" placeholder="类别" clearable style="width: 110px" popper-class="center-popper" @change="reload">
            <el-option v-for="(label, v) in CATEGORY_LABELS" :key="v" :label="`${label} ${v}`" :value="v" />
          </el-select>
          <el-select v-model="query.dataType" placeholder="数据类型" clearable style="width: 110px" popper-class="center-popper" @change="reload">
            <el-option v-for="(label, v) in DATA_TYPE_LABELS" :key="v" :label="label" :value="v" />
          </el-select>
          <el-input v-model="query.did" placeholder="DID / refId" clearable style="width: 180px" @keyup.enter="reload" @clear="reload" />
          <el-date-picker v-model="range" type="datetimerange" value-format="YYYY-MM-DDTHH:mm:ss+08:00" start-placeholder="from" end-placeholder="to" style="width: 320px" popper-class="center-popper" @change="reload" />
          <el-button @click="reload">查询</el-button>
        </div>
        <el-table :data="list.items" v-loading="list.loading" size="small" stripe :row-class-name="rowClass" @row-click="row => selected = row">
          <el-table-column prop="evidenceId" label="存证 ID" width="100" />
          <el-table-column label="类别" width="70">
            <template #default="{ row }"><span :style="{ color: CATEGORY_COLORS[row.category] }">{{ CATEGORY_LABELS[row.category] }}</span></template>
          </el-table-column>
          <el-table-column prop="blockHeight" label="高度" width="60" />
          <el-table-column prop="refId" label="refId" min-width="110" show-overflow-tooltip />
          <el-table-column label="摘要" min-width="140">
            <template #default="{ row }"><HashText :value="row.hash" /></template>
          </el-table-column>
          <el-table-column label="actor" min-width="140">
            <template #default="{ row }"><HashText :value="row.actorDid" :head="18" /></template>
          </el-table-column>
          <el-table-column label="时间" width="130">
            <template #default="{ row }">{{ fmtTime(row.timestamp) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="70">
            <template #default="{ row }"><el-tag size="small" effect="dark" :type="isTampered(row) ? 'danger' : 'success'">{{ isTampered(row) ? '篡改' : '完整' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click.stop="viewDetail(row)">详情</el-button>
              <el-button link size="small" @click.stop="doVerify(row.evidenceId)">校验</el-button>
              <el-button link type="warning" size="small" @click.stop="exportCert(row)">凭证</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination v-model:current-page="query.page" v-model:page-size="query.size" :total="list.total" layout="total, prev, pager, next" @current-change="load" />
        </div>
      </div>

      <!-- ⑤ 业务链路追踪 -->
      <div class="panel">
        <div class="panel-title">业务链路追踪 <span class="sub">GET /evidence/trace/{traceId}</span></div>
        <div class="toolbar">
          <el-input v-model="traceId" placeholder="tr-YYYYMMDD-xxxxxxxx" class="mono" clearable @keyup.enter="doTrace">
            <template #append><el-button :loading="tracing" @click="doTrace">追踪</el-button></template>
          </el-input>
        </div>
        <div v-if="selected?.traceId && selected.traceId !== traceId" class="muted" style="margin-bottom: 8px; font-size: 12px">
          已选存证 {{ selected.evidenceId }} 的 traceId：<el-button link type="primary" size="small" @click="traceId = selected.traceId; doTrace()">{{ selected.traceId }}</el-button>
        </div>
        <template v-if="trace">
          <div class="muted" style="margin-bottom: 8px">共 {{ trace.total }} 步 · 存证 {{ (trace.evidences || []).length }} 条</div>
          <el-timeline class="trace-timeline">
            <el-timeline-item v-for="s in trace.steps" :key="s.seq" :timestamp="fmtDateTime(s.at)" placement="top" :type="s.result === 'denied' || s.result === 'failed' ? 'danger' : s.kind === 'evidence' ? 'primary' : 'success'" :hollow="s.kind !== 'evidence'">
              <div><b>#{{ s.seq }}</b> <span class="mono">{{ s.module }} · {{ s.action }}</span> <el-tag size="small" :type="s.result === 'denied' || s.result === 'failed' ? 'danger' : 'success'" effect="plain">{{ s.result }}</el-tag></div>
              <div class="muted" style="font-size: 12px">
                <span v-if="s.actorDid">actor {{ shortDid(s.actorDid) }} · </span>
                <span v-if="s.evidenceId">存证 {{ s.evidenceId }}</span>
                <span v-if="s.blockHeight"> · 高度 {{ s.blockHeight }}</span>
                <span v-if="s.detail"> · {{ s.detail }}</span>
              </div>
            </el-timeline-item>
          </el-timeline>
        </template>
        <div v-else class="empty-tip">输入 traceId（可从存证详情、审计日志或调度/FL 任务获取）查看登录→鉴权→业务→存证的完整链路</div>
      </div>
    </div>

    <!-- 详情 -->
    <el-drawer v-model="detailVisible" :title="`存证详情 · ${detail?.evidenceId || ''}`" size="560px" class="center-dialog">
      <template v-if="detail">
        <el-alert v-if="detail.tampered" type="error" :closable="false" show-icon title="该存证的本地数据已被篡改（演示），与链上摘要不一致" style="margin-bottom: 10px" />
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="类别 / refId">{{ CATEGORY_LABELS[detail.category] }} / {{ detail.refId }}</el-descriptions-item>
          <el-descriptions-item label="区块高度 / txId">{{ detail.blockHeight }} / {{ detail.txId }}</el-descriptions-item>
          <el-descriptions-item label="payload 摘要"><span class="mono">{{ detail.hash }}</span></el-descriptions-item>
          <el-descriptions-item label="prevHash"><span class="mono">{{ detail.prevHash }}</span></el-descriptions-item>
          <el-descriptions-item label="blockHash"><span class="mono">{{ detail.blockHash }}</span></el-descriptions-item>
          <el-descriptions-item label="actorDid"><span class="mono">{{ detail.actorDid }}</span></el-descriptions-item>
          <el-descriptions-item label="traceId"><span class="mono">{{ detail.traceId || '--' }}</span></el-descriptions-item>
          <el-descriptions-item label="时间">{{ fmtDateTime(detail.timestamp) }}</el-descriptions-item>
        </el-descriptions>
        <div class="toolbar" style="margin: 12px 0">
          <el-button size="small" @click="doVerify(detail.evidenceId)">完整性校验</el-button>
          <el-button size="small" @click="exportCert(detail)">导出凭证</el-button>
          <el-button v-if="detail.traceId" size="small" @click="traceId = detail.traceId; doTrace(); detailVisible = false">追踪链路</el-button>
        </div>
        <JsonViewer :value="detail.payload" title="payload 快照（本地库）" />
      </template>
    </el-drawer>

    <!-- 校验结果 -->
    <el-dialog v-model="verifyVisible" title="完整性校验结果" width="600px" class="center-dialog">
      <div v-if="verifyResult" class="verify-box" :class="verifyResult.intact ? 'ok' : 'bad'">
        <div class="verify-big">{{ verifyResult.intact ? '✔ INTACT 数据完整' : '✘ TAMPERED 数据已被篡改' }}</div>
        <div class="muted">{{ verifyResult.message }}</div>
        <div class="hash-cmp">
          <div class="hash-line"><span class="muted">localHash（重算）</span><span class="mono" :class="{ red: !verifyResult.intact }">{{ verifyResult.localHash }}</span></div>
          <div class="hash-eq">{{ verifyResult.intact ? '＝' : '≠' }}</div>
          <div class="hash-line"><span class="muted">chainHash（链上）</span><span class="mono green">{{ verifyResult.chainHash }}</span></div>
        </div>
        <div v-if="verifyResult.tamperedAt" class="muted">篡改时间 tamperedAt：{{ fmtDateTime(verifyResult.tamperedAt) }}</div>
        <div class="muted">存证 {{ verifyResult.evidenceId }} · 区块高度 {{ verifyResult.blockHeight || '--' }}</div>
      </div>
    </el-dialog>

    <!-- ④ 篡改演示 -->
    <el-dialog v-model="tamperVisible" title="⚠ 篡改演示（仅 sys_admin，答辩现场用）" width="600px" class="center-dialog">
      <el-alert type="warning" :closable="false" show-icon title="直接改写一条 data 类存证的本地快照，链上摘要不变。随后自动调用 verify 与 chain/status，页面将标红断裂点。" style="margin-bottom: 12px" />
      <el-form label-width="90px">
        <el-form-item label="目标存证">
          <el-select v-model="tamperForm.evidenceId" filterable style="width: 100%" popper-class="center-popper">
            <el-option v-for="e in dataEvidences" :key="e.evidenceId" :label="`${e.evidenceId} · 高度 ${e.blockHeight} · ref ${e.refId} · ${fmtTime(e.timestamp)}`" :value="e.evidenceId" />
          </el-select>
        </el-form-item>
        <el-form-item label="newValue">
          <el-input v-model="tamperForm.newValueText" type="textarea" :rows="3" class="mono" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="tamperVisible = false">取消</el-button>
        <el-button type="danger" :loading="tampering" @click="submitTamper">执行篡改并校验</el-button>
      </template>
    </el-dialog>
  </CenterPage>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { listEvidence, getEvidence, verifyEvidence, getChainStatus, traceEvidence, tamperEvidence, getCertificate, saveBlob, wsClient, WS_TYPES } from '@/api'
import { useUserStore } from '@/stores/user'
import { useLogStore } from '@/stores/logs'
import { fmtDateTime, fmtTime, shortDid, DATA_TYPE_LABELS } from '@/utils/format'
import CenterPage from '@/components/center/CenterPage.vue'
import StatCard from '@/components/center/StatCard.vue'
import HashText from '@/components/center/HashText.vue'
import JsonViewer from '@/components/center/JsonViewer.vue'
import EChart from '@/components/center/EChart.vue'
import { CATEGORY_COLORS, CATEGORY_LABELS, TOOLTIP } from '@/components/center/chartTheme.js'

const userStore = useUserStore()
const logStore = useLogStore()
const BLOCK_COUNT = 16

/* ---------- 链状态 ---------- */
const chain = ref(null)
const statusLoading = ref(false)
async function loadStatus() {
  statusLoading.value = true
  try { chain.value = await getChainStatus() } catch { /* 拦截器已提示 */ } finally { statusLoading.value = false }
}
const categoryOption = computed(() => ({
  tooltip: TOOLTIP,
  legend: { orient: 'vertical', right: 10, top: 'middle', textStyle: { color: '#8892B0' } },
  series: [{
    type: 'pie', radius: ['45%', '75%'], center: ['35%', '50%'], label: { show: false },
    data: Object.entries(chain.value?.byCategory || {}).map(([k, v]) => ({ name: `${CATEGORY_LABELS[k] || k} ${v}`, value: v, itemStyle: { color: CATEGORY_COLORS[k] } }))
  }]
}))

/* ---------- 区块链条 ---------- */
const blocks = ref([])
const chainScrollRef = ref(null)
async function loadBlocks() {
  try {
    const data = await listEvidence({ size: BLOCK_COUNT })
    const recent = (data.items || []).slice().reverse()
    // 被篡改的块若不在最近 N 块窗口内，钉到链条最前面（带省略标记），保证演示时"标红"可见
    const tamperedIds = (chain.value?.tamperedIds || []).filter(id => !recent.some(b => b.evidenceId === id))
    const pinned = []
    for (const id of tamperedIds) {
      try { pinned.push({ ...(await getEvidence(id)), tampered: true, pinned: true }) } catch { /* 忽略 */ }
    }
    pinned.sort((a, b) => (a.blockHeight || 0) - (b.blockHeight || 0))
    blocks.value = pinned.length ? [...pinned, { gap: true, evidenceId: '__gap__' }, ...recent] : recent
    await nextTick()
    // 有被篡改块时停在链条起点让红块可见，否则滚到最新块
    if (chainScrollRef.value) chainScrollRef.value.scrollLeft = pinned.length ? 0 : chainScrollRef.value.scrollWidth
  } catch { /* 拦截器已提示 */ }
}
function isTampered(b) { return Boolean(b.tampered) || (chain.value?.tamperedIds || []).includes(b.evidenceId) }
function blockClass(b) {
  if (isTampered(b)) return 'tampered'
  if (chain.value && !chain.value.intact && b.blockHeight > chain.value.brokenAt) return 'affected'
  return ''
}
function hash8(h) { return (h || '').replace(/^sm3:/, '').slice(0, 8) }

/* ---------- 检索 ---------- */
const query = reactive({ category: '', dataType: '', did: '', page: 1, size: 10 })
const range = ref([])
const list = reactive({ items: [], total: 0, loading: false })
const selected = ref(null)
async function load() {
  list.loading = true
  try {
    const [from, to] = range.value || []
    const data = await listEvidence({ page: query.page, size: query.size, category: query.category || undefined, dataType: query.dataType || undefined, did: query.did || undefined, from: from || undefined, to: to || undefined })
    list.items = data.items || []; list.total = data.total || 0
  } catch { /* 拦截器已提示 */ } finally { list.loading = false }
}
function reload() { query.page = 1; load() }
function rowClass({ row }) {
  if (isTampered(row)) return 'tampered-row'
  if (chain.value && !chain.value.intact && row.blockHeight > chain.value.brokenAt) return 'affected-row'
  return ''
}

/* ---------- 详情 / 校验 / 凭证 ---------- */
const detailVisible = ref(false)
const detail = ref(null)
async function viewDetail(row) {
  try { detail.value = await getEvidence(row.evidenceId); detailVisible.value = true } catch { /* 拦截器已提示 */ }
}
const verifyVisible = ref(false)
const verifyResult = ref(null)
async function doVerify(evidenceId) {
  try {
    const r = await verifyEvidence({ evidenceId })
    verifyResult.value = r
    verifyVisible.value = true
    logStore.addLog(`存证 ${evidenceId} 完整性校验：${r.intact ? '一致 intact' : '不一致，已被篡改'}`, r.intact ? 'INFO' : 'ERROR', 'CHAIN')
    return r
  } catch { return null }
}
async function exportCert(row) {
  try {
    const cert = await getCertificate(row.evidenceId)
    const blob = new Blob([JSON.stringify(cert, null, 2)], { type: 'application/json' })
    saveBlob(blob, `certificate-${row.evidenceId}.json`)
    logStore.addLog(`导出存证凭证 ${cert.certificateId || row.evidenceId}（intact=${cert.integrity?.intact}）`, 'INFO', 'CHAIN')
  } catch { /* 拦截器已提示 */ }
}

/* ---------- 篡改演示 ---------- */
const tamperVisible = ref(false)
const tampering = ref(false)
const dataEvidences = ref([])
const tamperForm = reactive({ evidenceId: '', newValueText: '{"pvOutput":999.9}' })
async function openTamper() {
  try {
    const data = await listEvidence({ category: 'data', size: 50 })
    dataEvidences.value = data.items || []
    tamperForm.evidenceId = (selected.value?.category === 'data' ? selected.value.evidenceId : '') || dataEvidences.value[0]?.evidenceId || ''
    tamperVisible.value = true
  } catch { /* 拦截器已提示 */ }
}
async function submitTamper() {
  let newValue
  try { newValue = JSON.parse(tamperForm.newValueText) } catch { ElMessage.warning('newValue 不是合法 JSON'); return }
  if (!tamperForm.evidenceId) { ElMessage.warning('请选择存证'); return }
  tampering.value = true
  try {
    const t = await tamperEvidence({ evidenceId: tamperForm.evidenceId, newValue })
    logStore.addLog(`【演示】篡改存证 ${t.evidenceId}（高度 ${t.blockHeight || '--'}）的本地数据：${tamperForm.newValueText}`, 'ERROR', 'CHAIN')
    tamperVisible.value = false
    const r = await doVerify(t.evidenceId)
    // 让被篡改记录出现在检索表首页：按其 DID / refId 过滤
    const target = dataEvidences.value.find(e => e.evidenceId === t.evidenceId)
    if (target?.did || target?.refId) { query.did = target.did || target.refId; query.page = 1 }
    await loadStatus()
    await Promise.all([loadBlocks(), load()])
    selected.value = list.items.find(e => e.evidenceId === t.evidenceId) || selected.value
    if (chain.value && !chain.value.intact) logStore.addLog(`链状态：断裂于高度 ${chain.value.brokenAt}，之后 ${Math.max(0, chain.value.height - chain.value.brokenAt)} 个区块受影响`, 'ERROR', 'CHAIN')
    if (r && !r.intact) ElMessage.error(`校验失败：${r.message}`)
  } catch { /* 拦截器已提示 */ } finally { tampering.value = false }
}

/* ---------- 追踪 ---------- */
const traceId = ref('')
const tracing = ref(false)
const trace = ref(null)
async function doTrace() {
  if (!traceId.value) return
  tracing.value = true
  try {
    trace.value = await traceEvidence(traceId.value.trim())
    logStore.addLog(`追踪 traceId ${traceId.value}：${trace.value.total} 步`, 'INFO', 'CHAIN', { traceId: traceId.value })
  } catch { trace.value = null } finally { tracing.value = false }
}

/* ---------- WS 实时追加 ---------- */
let offWs = null
let wsTimer = null
function onEvidenceWritten(p) {
  // 先乐观追加占位块，再节流刷新真实数据
  if (p?.evidenceId && !blocks.value.find(b => b.evidenceId === p.evidenceId)) {
    blocks.value.push({ evidenceId: p.evidenceId, category: p.category, blockHeight: p.blockHeight, hash: '', fresh: true })
    if (blocks.value.length > BLOCK_COUNT) blocks.value.shift()
    nextTick(() => { if (chainScrollRef.value) chainScrollRef.value.scrollLeft = chainScrollRef.value.scrollWidth })
  }
  if (wsTimer) return
  wsTimer = setTimeout(() => { wsTimer = null; loadStatus(); loadBlocks(); if (query.page === 1) load() }, 1500)
}

function refreshAll() { loadStatus(); loadBlocks(); load() }
onMounted(() => {
  refreshAll()
  offWs = wsClient.on(WS_TYPES.EVIDENCE_WRITTEN, onEvidenceWritten)
})
onBeforeUnmount(() => { if (offWs) offWs(); if (wsTimer) clearTimeout(wsTimer) })
</script>

<style scoped>
.chain-flag { font-weight: 700; padding: 3px 12px; border-radius: 12px; border: 1px solid; font-size: 13px; }
.chain-flag.ok { color: var(--color-success); border-color: var(--color-success); background: rgba(46, 204, 113, .1); }
.chain-flag.bad { color: #fff; border-color: var(--color-danger); background: var(--color-danger); animation: pulse 1s infinite; }
.status-grid { display: grid; grid-template-columns: 1fr 320px; gap: 12px; align-items: start; }
.last-hash { grid-column: 1 / -1; padding: 10px 14px; border-radius: 10px; border: 1px solid rgba(0, 180, 216, .2); background: rgba(0, 0, 0, .3); word-break: break-all; font-size: 12px; }
.chain-scroll { overflow-x: auto; padding: 10px 4px 14px; }
.chain-row { display: flex; align-items: stretch; gap: 34px; min-width: max-content; }
.block {
  position: relative; width: 122px; padding: 10px 8px; border-radius: 8px; cursor: pointer; text-align: center;
  background: linear-gradient(160deg, rgba(0, 180, 216, .18), rgba(0, 119, 182, .08)); border: 1px solid rgba(0, 180, 216, .5);
  box-shadow: 0 0 12px rgba(0, 180, 216, .2), inset 0 1px 0 rgba(255, 255, 255, .08); transition: transform .2s; animation: fadeIn .5s;
}
.block:hover { transform: translateY(-3px); }
.block-h { font-weight: 700; color: var(--color-primary); font-family: Consolas, monospace; }
.block-cat { font-size: 12px; margin: 2px 0; }
.block-hash { font-size: 13px; color: #fff; letter-spacing: 1px; }
.block-id { font-size: 10px; color: var(--color-text-secondary); }
.block-link { position: absolute; right: -34px; top: 50%; width: 34px; height: 2px; background: linear-gradient(90deg, var(--color-primary), rgba(0, 180, 216, .3)); }
.block-link::after { content: ''; position: absolute; right: 0; top: -3px; border: 4px solid transparent; border-left-color: var(--color-primary); }
.block:last-child .block-link { display: none; }
.block.tampered { border-color: var(--color-danger); background: linear-gradient(160deg, rgba(230, 57, 70, .35), rgba(230, 57, 70, .1)); animation: blinkRed 1s infinite; }
.block.tampered .block-h, .block.tampered .block-hash { color: #ff8a8a; }
.block.affected { border-color: var(--color-warning); background: linear-gradient(160deg, rgba(243, 156, 18, .25), rgba(243, 156, 18, .06)); }
.block.affected .block-h { color: var(--color-warning); }
@keyframes blinkRed { 0%, 100% { box-shadow: 0 0 10px rgba(230, 57, 70, .4); } 50% { box-shadow: 0 0 26px rgba(230, 57, 70, 1); } }
.evidence-grid { grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); }
.block-gap { flex: 0 0 auto; align-self: center; padding: 0 6px; color: var(--color-text-secondary); font-size: 18px; letter-spacing: 2px; }
.trace-timeline { max-height: 520px; overflow: auto; padding-right: 6px; }
.verify-box { padding: 16px; border-radius: 10px; border: 1px solid; text-align: center; display: flex; flex-direction: column; gap: 8px; }
.verify-box.ok { border-color: var(--color-success); background: rgba(46, 204, 113, .08); }
.verify-box.bad { border-color: var(--color-danger); background: rgba(230, 57, 70, .1); }
.verify-big { font-size: 24px; font-weight: 700; letter-spacing: 2px; }
.verify-box.ok .verify-big { color: var(--color-success); }
.verify-box.bad .verify-big { color: var(--color-danger); animation: pulse 1s infinite; }
.hash-cmp { display: flex; flex-direction: column; gap: 4px; text-align: left; padding: 10px; border-radius: 8px; background: rgba(0, 0, 0, .35); }
.hash-line { display: flex; flex-direction: column; gap: 2px; word-break: break-all; font-size: 12px; }
.hash-eq { text-align: center; font-size: 22px; font-weight: 700; color: var(--color-text-secondary); }
.mono.red { color: #ff8a8a; }
.mono.green { color: #a6e3a1; }
@media (max-width: 1100px) { .status-grid, .evidence-grid { grid-template-columns: 1fr; } }
</style>
