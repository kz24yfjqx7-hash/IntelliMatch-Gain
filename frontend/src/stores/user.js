/**
 * 登录态 store：token / user / roles / permissions。
 * token 持久化到 localStorage('energy-tds-token')，user 同步缓存以便刷新后立即可用。
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as authApi from '@/api/auth'
import { TOKEN_KEY } from '@/api/request'
import { wsClient } from '@/api/ws'
import { ROLE_LABELS } from '@/utils/format'

const USER_KEY = 'energy-tds-user'

function readJson(key) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export const useUserStore = defineStore('user', () => {
  const token = ref((() => { try { return localStorage.getItem(TOKEN_KEY) || '' } catch { return '' } })())
  const user = ref(readJson(USER_KEY))
  const permissions = ref(user.value?.permissions || [])
  const loading = ref(false)

  const roles = computed(() => user.value?.roles || [])
  const isLoggedIn = computed(() => Boolean(token.value))
  const primaryRole = computed(() => roles.value[0] || '')
  const roleLabel = computed(() => ROLE_LABELS[primaryRole.value] || primaryRole.value || '未登录')
  const displayName = computed(() => user.value?.realName || user.value?.username || '')
  const did = computed(() => user.value?.did || '')

  function persist() {
    try {
      if (token.value) localStorage.setItem(TOKEN_KEY, token.value)
      else localStorage.removeItem(TOKEN_KEY)
      if (user.value) localStorage.setItem(USER_KEY, JSON.stringify({ ...user.value, permissions: permissions.value }))
      else localStorage.removeItem(USER_KEY)
    } catch {
      // 隐私模式等场景忽略
    }
  }

  /** 仅清理本地状态（request.js 收到 1002 时调用） */
  function clearSession() {
    token.value = ''
    user.value = null
    permissions.value = []
    persist()
    wsClient.disconnect()
  }

  /** 登录：POST /auth/login → 保存 token → GET /auth/me 拉权限 → 建 WS */
  async function login(form) {
    loading.value = true
    try {
      const data = await authApi.login(form)
      token.value = data.token
      user.value = data.user
      persist()
      await fetchMe()
      wsClient.connect(token.value)
      return data
    } finally {
      loading.value = false
    }
  }

  /** 拉取当前用户与扁平权限列表 */
  async function fetchMe() {
    const me = await authApi.getMe()
    user.value = { ...(user.value || {}), ...me }
    permissions.value = me.permissions || []
    persist()
    return me
  }

  async function logout() {
    try {
      if (token.value) await authApi.logout()
    } catch {
      // 后端不可达也要能退出
    } finally {
      clearSession()
    }
  }

  /** 是否拥有权限，格式 `resource:action`；支持数组（任一满足） */
  function hasPermission(perm) {
    if (!perm) return true
    const list = Array.isArray(perm) ? perm : [perm]
    return list.some(p => permissions.value.includes(p))
  }

  function hasRole(role) {
    const list = Array.isArray(role) ? role : [role]
    return list.some(r => roles.value.includes(r))
  }

  /** 应用启动时恢复会话：有 token 则拉 me 并连 WS */
  async function restore() {
    if (!token.value) return false
    try {
      await fetchMe()
      wsClient.connect(token.value)
      return true
    } catch {
      return false
    }
  }

  /** 登录后按角色决定落地页 */
  function homePath() {
    if (hasRole('edge_node')) return '/edge/classification'
    return '/cloud/topology'
  }

  return {
    token, user, roles, permissions, isLoggedIn, loading,
    primaryRole, roleLabel, displayName, did,
    login, logout, fetchMe, restore, clearSession, hasPermission, hasRole, homePath
  }
})
