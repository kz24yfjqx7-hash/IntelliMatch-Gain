/**
 * 路由：/login 无布局；其余在 AppLayout 布局下。
 * meta.requiresAuth（默认 true）、meta.permission（可选，形如 'asset:read'）。
 * 全局守卫：未登录跳 /login?redirect=；无权限跳首页并 toast。
 */
import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { requiresAuth: false, title: '登录' }
  },
  {
    path: '/',
    component: () => import('@/layouts/AppLayout.vue'),
    redirect: '/cloud/topology',
    children: [
      {
        path: 'cloud/topology',
        name: 'NetworkTopology',
        component: () => import('@/views/NetworkTopology.vue'),
        meta: { perspective: 'cloud', title: '全网设备状态图' }
      },
      {
        path: 'cloud/aggregate',
        name: 'CloudAggregate',
        component: () => import('@/views/CloudAggregate.vue'),
        meta: { perspective: 'cloud', title: '云端聚合与调度', permission: 'dispatch:read' }
      },
      {
        path: 'edge/classification',
        name: 'DataClassification',
        component: () => import('@/views/DataClassification.vue'),
        meta: { perspective: 'edge', title: '本地感知与分级' }
      },
      {
        path: 'edge/risk',
        name: 'RiskAssessment',
        component: () => import('@/views/RiskAssessment.vue'),
        meta: { perspective: 'edge', title: '动态隐私风险评估' }
      },
      {
        path: 'edge/privacy',
        name: 'PrivacyCompute',
        component: () => import('@/views/PrivacyCompute.vue'),
        meta: { perspective: 'edge', title: '边缘隐私保护计算' }
      },
      {
        path: 'edge/response',
        name: 'TerminalResponse',
        component: () => import('@/views/TerminalResponse.vue'),
        meta: { perspective: 'edge', title: '终端响应与执行' }
      },
      {
        path: 'audit',
        name: 'AuditLog',
        component: () => import('@/views/AuditLog.vue'),
        meta: { perspective: 'global', title: '系统审计与日志中心' }
      },
      // ---- 可信数据空间四个中心（frontend-pages 负责页面实现）----
      {
        path: 'identity',
        name: 'IdentityCenter',
        component: () => import('@/views/IdentityCenter.vue'),
        meta: { perspective: 'center', title: '统一身份与可信接入中心' }
      },
      {
        path: 'assets',
        name: 'AssetsCenter',
        component: () => import('@/views/AssetsCenter.vue'),
        meta: { perspective: 'center', title: '能源数据资产中心', permission: 'asset:read' }
      },
      {
        path: 'permission',
        name: 'PermissionCenter',
        component: () => import('@/views/PermissionCenter.vue'),
        meta: { perspective: 'center', title: '权限控制中心' }
      },
      {
        path: 'evidence',
        name: 'EvidenceCenter',
        component: () => import('@/views/EvidenceCenter.vue'),
        meta: { perspective: 'center', title: '区块链存证中心', permission: 'evidence:read' }
      }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/cloud/topology' }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

let restored = false

router.beforeEach(async to => {
  const userStore = useUserStore()
  const requiresAuth = to.meta.requiresAuth !== false

  // 首次进入且本地有 token：恢复会话（拉 /auth/me 与权限）
  if (!restored && userStore.token) {
    restored = true
    await userStore.restore()
  }

  if (to.path === '/login') {
    if (userStore.isLoggedIn) return { path: userStore.homePath() }
    return true
  }
  if (requiresAuth && !userStore.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.permission && !userStore.hasPermission(to.meta.permission)) {
    ElMessage.warning(`无权访问「${to.meta.title || to.path}」，需要权限 ${to.meta.permission}`)
    return { path: userStore.homePath() }
  }
  return true
})

router.afterEach(to => {
  if (typeof document !== 'undefined') {
    document.title = to.meta.title ? `${to.meta.title} · 能源可信数据空间平台` : '能源可信数据空间平台'
  }
})

export default router
