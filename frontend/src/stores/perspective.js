/**
 * 视角 / 节点 store。
 * - nodes 改为 ref([])，通过 fetchNodes() 从 GET /nodes 拉取，并订阅 WS node_status 实时更新
 * - node 对象：{id,name,status,model,did,didStatus,metrics:{pvOutput,storageOutput,load,soc},lastSeenAt,data:{...metrics,model}}
 *   其中 data 是兼容旧页面的别名
 * - 保持 currentNodeData/currentNodeInfo/getNodeById/buildEdgeReport/cloudMenus/edgeMenus/globalMenus/currentMenus
 *   /togglePerspective/setCurrentNode 不变
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { createEdgeReport } from '../services/dispatchTask.js'
import * as nodeApi from '@/api/node'
import { wsClient, WS_TYPES } from '@/api/ws'
import { useUserStore } from './user.js'

/** 菜单按角色过滤（user store 尚未建好时放行，便于单测） */
function userStoreHasRole(roles) {
  try { return useUserStore().hasRole(roles) } catch { return true }
}

/** 种子节点：后端不可达时的兜底，字段与契约 GET /nodes 对齐 */
const SEED_NODES = [
  { id: 'Node-A', name: '虚拟电厂节点A', status: 'online', model: 'VPP-2000', metrics: { pvOutput: 45.3, storageOutput: -12.0, load: 120, soc: 65 } },
  { id: 'Node-B', name: '虚拟电厂节点B', status: 'online', model: 'VPP-2000', metrics: { pvOutput: 32.1, storageOutput: 8.5, load: 85, soc: 78 } },
  { id: 'Node-C', name: '虚拟电厂节点C', status: 'warning', model: 'VPP-3000', metrics: { pvOutput: 28.7, storageOutput: -25.3, load: 150, soc: 42 } },
  { id: 'Node-D', name: '虚拟电厂节点D', status: 'online', model: 'VPP-2000', metrics: { pvOutput: 38.9, storageOutput: 5.2, load: 95, soc: 82 } }
]

/** 把后端节点规范化为带 data 别名的对象 */
export function normalizeNode(raw) {
  const metrics = {
    pvOutput: Number(raw.metrics?.pvOutput ?? raw.data?.pvOutput ?? 0),
    storageOutput: Number(raw.metrics?.storageOutput ?? raw.data?.storageOutput ?? 0),
    load: Number(raw.metrics?.load ?? raw.data?.load ?? 0),
    soc: Number(raw.metrics?.soc ?? raw.data?.soc ?? 0)
  }
  const model = raw.model || raw.data?.model || ''
  return {
    id: raw.id,
    name: raw.name,
    status: raw.status || 'online',
    model,
    did: raw.did || '',
    didStatus: raw.didStatus || '',
    metrics,
    lastSeenAt: raw.lastSeenAt || null,
    data: { ...metrics, model }
  }
}

export const usePerspectiveStore = defineStore('perspective', () => {
  const currentPerspective = ref('cloud')
  const currentNode = ref('Node-A')
  const nodes = ref(SEED_NODES.map(normalizeNode))
  const nodesLoaded = ref(false)
  const nodesLoading = ref(false)
  let wsDetach = null

  const isCloud = computed(() => currentPerspective.value === 'cloud')
  const isEdge = computed(() => currentPerspective.value === 'edge')

  const currentNodeData = computed(() => {
    const node = nodes.value.find(n => n.id === currentNode.value)
    return node?.data || nodes.value[0]?.data || normalizeNode(SEED_NODES[0]).data
  })

  const currentNodeInfo = computed(() => {
    return nodes.value.find(n => n.id === currentNode.value) || nodes.value[0] || normalizeNode(SEED_NODES[0])
  })

  const dispatchReports = computed(() => {
    return nodes.value.map(node => createEdgeReport(node)).filter(Boolean)
  })

  const cloudMenus = [
    { path: '/cloud/topology', title: '全网设备状态图', icon: 'grid' },
    { path: '/cloud/aggregate', title: '云端聚合与调度', icon: 'connection' }
  ]

  const edgeMenus = [
    { path: '/edge/classification', title: '本地感知与分级', icon: 'document' },
    { path: '/edge/risk', title: '动态隐私风险评估', icon: 'warning' },
    { path: '/edge/privacy', title: '边缘隐私保护计算', icon: 'lock', permission: 'model:read' },
    { path: '/edge/response', title: '终端响应与执行', icon: 'finished', permission: 'dispatch:read' }
  ]

  /** 可信数据空间四个中心：两种视角都显示 */
  const centerMenus = [
    { path: '/identity', title: '身份与可信接入', icon: 'user', permission: null },
    { path: '/assets', title: '能源数据资产', icon: 'coin', permission: 'asset:read' },
    { path: '/permission', title: '权限控制中心', icon: 'key', permission: null },
    { path: '/evidence', title: '区块链存证', icon: 'link', permission: 'evidence:read' }
  ]

  const globalMenus = [
    // 真后端审计接口仅 sys_admin/regulator 可读（契约未定义 audit 资源权限），其它角色不显示入口
    { path: '/audit', title: '系统审计与日志中心', icon: 'list', roles: ['sys_admin', 'regulator'] }
  ]

  const currentMenus = computed(() => {
    const menus = currentPerspective.value === 'cloud' ? cloudMenus : edgeMenus
    return [...menus, ...globalMenus.filter(m => !m.roles || userStoreHasRole(m.roles))]
  })

  function togglePerspective() {
    currentPerspective.value = currentPerspective.value === 'cloud' ? 'edge' : 'cloud'
  }

  function setCurrentNode(nodeId) {
    currentNode.value = nodeId
  }

  function getNodeById(nodeId) {
    return nodes.value.find(node => node.id === nodeId) || null
  }

  function buildEdgeReport(nodeId = currentNode.value, overrides = {}) {
    const node = getNodeById(nodeId)
    return createEdgeReport(node, overrides)
  }

  /** 最近一次收到 node_status 的时间戳（0 表示从未收到），用于决定要不要轮询兜底 */
  let lastNodeStatusAt = 0
  let pollTimer = null

  /** 用 WS node_status 增量更新节点指标 */
  function applyNodeStatus(payload) {
    lastNodeStatusAt = Date.now()
    if (!payload?.nodeId) return
    const node = nodes.value.find(n => n.id === payload.nodeId)
    if (!node) return
    if (payload.status) node.status = payload.status
    if (payload.metrics) {
      Object.assign(node.metrics, payload.metrics)
      Object.assign(node.data, payload.metrics)
    }
    node.lastSeenAt = new Date().toISOString()
  }

  /** 从后端拉取节点列表；失败时保留种子数据 */
  async function fetchNodes() {
    nodesLoading.value = true
    try {
      const data = await nodeApi.listNodes({ size: 50 })
      const items = (data?.items || []).map(normalizeNode)
      if (items.length) {
        nodes.value = items
        if (!items.find(n => n.id === currentNode.value)) currentNode.value = items[0].id
      }
      nodesLoaded.value = true
      if (!wsDetach) wsDetach = wsClient.on(WS_TYPES.NODE_STATUS, applyNodeStatus)
      startNodePolling()
      return nodes.value
    } finally {
      nodesLoading.value = false
    }
  }

  /**
   * 节点指标轮询兜底：契约 2.13 要求 node_status 每 5 秒推一次，
   * 真后端目前只在 /nodes/{id}/online 时推（见 BACKEND-ISSUES B-020），
   * 于是这里每 15 秒检查一次——期间没收到过 node_status 才重新拉列表，
   * 收得到推送时不会产生任何多余请求（mock 模式即如此）。
   */
  function startNodePolling(intervalMs = 15000) {
    if (pollTimer) return
    pollTimer = setInterval(() => {
      if (Date.now() - lastNodeStatusAt < intervalMs) return
      nodeApi.listNodes({ size: 50 }).then(data => {
        const items = (data?.items || []).map(normalizeNode)
        if (items.length) nodes.value = items
      }).catch(() => { /* 未登录 / 无权限：静默 */ })
    }, intervalMs)
  }

  function stopNodePolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  }

  return {
    currentPerspective,
    currentNode,
    nodes,
    nodesLoaded,
    nodesLoading,
    currentNodeData,
    currentNodeInfo,
    dispatchReports,
    isCloud,
    isEdge,
    cloudMenus,
    edgeMenus,
    centerMenus,
    globalMenus,
    currentMenus,
    togglePerspective,
    setCurrentNode,
    getNodeById,
    buildEdgeReport,
    fetchNodes,
    startNodePolling,
    stopNodePolling,
    applyNodeStatus
  }
})
