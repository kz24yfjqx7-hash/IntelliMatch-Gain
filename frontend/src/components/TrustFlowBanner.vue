<template>
  <!--
    首页「能源可信数据空间流程展示」横幅：六环节流程 + 实时计数 + 点击跳转。
    自包含：自己拉计数、自己订阅 WS（evidence_written / log 到达后节流 3s 刷新）。
    在 NetworkTopology.vue 顶部插入 <TrustFlowBanner /> 即可。
  -->
  <div class="trust-flow tech-card">
    <div class="tf-head">
      <span class="tf-title">能源可信数据空间 · 可信闭环</span>
      <span class="tf-sub">身份 → 数据 → 权限 → 计算 → 调度 → 存证，计数来自后端实时统计</span>
      <span class="tf-refresh" :class="{ spinning: loading }" title="刷新" @click="refresh">⟳</span>
    </div>
    <div class="tf-row">
      <template v-for="(s, i) in stages" :key="s.key">
        <div class="tf-stage" :class="{ bump: s.bump }" :style="{ '--accent': s.color }" @click="go(s)">
          <div class="tf-icon">{{ s.icon }}</div>
          <div class="tf-count">{{ display(s) }}</div>
          <div class="tf-name">{{ s.label }}</div>
          <div class="tf-metric">{{ s.metric }}</div>
        </div>
        <div v-if="i < stages.length - 1" class="tf-link">
          <svg viewBox="0 0 100 20" preserveAspectRatio="none">
            <line x1="0" y1="10" x2="100" y2="10" class="tf-line-bg" />
            <line x1="0" y1="10" x2="100" y2="10" class="tf-line-flow" />
          </svg>
          <span class="tf-arrow">▶</span>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { listDids, getAssetStats, listGrants, listFlTasks, listDispatchTasks, getChainStatus, wsClient, WS_TYPES } from '@/api'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()
const loading = ref(false)

/** 六环节：计数来源见 DEV-PLAN §4 */
const stages = reactive([
  { key: 'identity', icon: '🪪', label: '身份接入', metric: 'DID 数', path: '/identity', color: '#00B4D8', value: null },
  { key: 'assets', icon: '🗂️', label: '数据登记', metric: '资产数', path: '/assets', color: '#2ECC71', value: null, perm: 'asset:read' },
  { key: 'permission', icon: '🛡️', label: '权限授权', metric: '授权数', path: '/permission', color: '#F39C12', value: null },
  { key: 'privacy', icon: '🧮', label: '隐私计算', metric: 'FL 任务', path: '/edge/privacy', color: '#9b59b6', value: null, perm: 'model:read' },
  { key: 'dispatch', icon: '⚡', label: '智能调度', metric: '调度任务', path: '/cloud/aggregate', color: '#e67e22', value: null, perm: 'dispatch:read' },
  { key: 'evidence', icon: '⛓️', label: '存证审计', metric: '链高度', path: '/evidence', color: '#1abc9c', value: null, perm: 'evidence:read' }
])

const fetchers = {
  identity: async () => (await listDids({ size: 1 })).total,
  assets: async () => (await getAssetStats()).total,
  permission: async () => (await listGrants({ size: 1 })).total,
  privacy: async () => (await listFlTasks({ size: 1 })).total,
  dispatch: async () => (await listDispatchTasks({ size: 1 })).total,
  evidence: async () => (await getChainStatus()).height
}

function display(s) {
  if (s.value === null || s.value === undefined) return '--'
  return Number(s.value).toLocaleString('zh-CN')
}

async function refresh() {
  if (!userStore.isLoggedIn) return
  loading.value = true
  await Promise.allSettled(stages.map(async s => {
    // 无权限的环节不请求，避免 1003 提示刷屏
    if (s.perm && !userStore.hasPermission(s.perm)) { s.value = null; return }
    try {
      const v = await fetchers[s.key]()
      if (s.value !== null && v !== s.value) {
        s.bump = true
        setTimeout(() => { s.bump = false }, 900)
      }
      s.value = v
    } catch {
      // 拦截器已提示；保留旧值
    }
  }))
  loading.value = false
}

function go(s) {
  router.push(s.path)
}

/* WS 到达后节流 3s 刷新 */
let timer = null
let lastAt = 0
const offs = []
function scheduleRefresh() {
  const wait = Math.max(0, 3000 - (Date.now() - lastAt))
  if (timer) return
  timer = setTimeout(() => { timer = null; lastAt = Date.now(); refresh() }, wait)
}
onMounted(() => {
  refresh()
  offs.push(wsClient.on(WS_TYPES.EVIDENCE_WRITTEN, scheduleRefresh))
  offs.push(wsClient.on(WS_TYPES.LOG, scheduleRefresh))
})
onBeforeUnmount(() => {
  offs.forEach(fn => fn && fn())
  if (timer) clearTimeout(timer)
})
</script>

<style scoped>
.trust-flow { position: relative; padding: 12px 18px 14px; border-radius: 12px; margin-bottom: 16px; }
.tf-head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.tf-title { font-weight: 600; letter-spacing: 2px; color: var(--color-primary); text-shadow: 0 0 10px rgba(0, 180, 216, .5); }
.tf-sub { font-size: 12px; color: var(--color-text-secondary); flex: 1; }
.tf-refresh { cursor: pointer; color: var(--color-text-secondary); font-size: 16px; transition: transform .3s; }
.tf-refresh:hover { color: var(--color-primary); }
.tf-refresh.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.tf-row { display: flex; align-items: center; }
.tf-stage {
  flex: 0 0 auto; width: 15%; min-width: 120px; padding: 10px 6px; border-radius: 10px; text-align: center; cursor: pointer;
  border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent); background: color-mix(in srgb, var(--accent) 8%, transparent);
  transition: all .25s;
}
.tf-stage:hover { transform: translateY(-3px); border-color: var(--accent); box-shadow: 0 0 18px color-mix(in srgb, var(--accent) 45%, transparent); }
.tf-stage.bump .tf-count { animation: bump .8s ease-out; }
@keyframes bump { 0% { transform: scale(1); } 30% { transform: scale(1.35); color: #fff; } 100% { transform: scale(1); } }
.tf-icon { font-size: 22px; }
.tf-count { font-size: 24px; font-weight: 700; font-family: Consolas, 'Courier New', monospace; color: var(--accent); line-height: 1.2; }
.tf-name { font-size: 13px; color: var(--color-text); margin-top: 2px; }
.tf-metric { font-size: 11px; color: var(--color-text-secondary); }
.tf-link { flex: 1 1 0; position: relative; height: 20px; min-width: 24px; }
.tf-link svg { width: 100%; height: 100%; overflow: visible; }
.tf-line-bg { stroke: rgba(0, 180, 216, .2); stroke-width: 2; }
.tf-line-flow { stroke: var(--color-primary); stroke-width: 2; stroke-dasharray: 6 8; animation: dash 1.2s linear infinite; filter: drop-shadow(0 0 3px var(--color-primary)); }
@keyframes dash { to { stroke-dashoffset: -28; } }
.tf-arrow { position: absolute; right: -4px; top: 50%; transform: translateY(-50%); font-size: 9px; color: var(--color-primary); }
@media (max-width: 1000px) {
  .tf-row { flex-wrap: wrap; gap: 8px; }
  .tf-link { display: none; }
  .tf-stage { width: calc(33% - 8px); }
}
</style>
