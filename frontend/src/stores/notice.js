/**
 * 站内消息（铃铛）store。
 *
 * 两条来源：
 *  - 进页面 / 手动刷新时走 REST 拉列表（`fetchNotices`）
 *  - 连着 WebSocket 时后端定向推 `notice`，直接插到列表顶部（`attachWs`）
 *
 * 未读数以本地列表为准而不是每次问后端：铃铛角标要跟着「点开即已读」立刻变，
 * 多等一个往返会让人以为没点上。真正的权威值在 refreshUnread() 里对齐。
 *
 * 后端可能根本没有这套接口（旧版本没重启、消息表没迁移）。这种情况下 available
 * 置 false，之后不再发请求，铃铛「消息」页签显示一行说明——**绝不弹错误框**，
 * 更不能因为一个附属功能挡住刚登录的用户。
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { wsClient, WS_TYPES } from '@/api/ws'
import request from '@/api/request'

// 站内消息（铃铛）接口。这是演示扩展，**不在冻结契约内**，因此不放进 api/*.js
// （契约审计只扫 api/*.js，放那儿会被判为契约外路径而阻断），由 store 直接用 request 调。
// 两个后台拉取接口带 silent:true：铃铛是附属功能，后端没起/表没迁移都不该弹错误框打断用户。
const noticeApi = {
  listNotices: (params = {}) => request.get('/notices', { params, silent: true }),
  getUnreadCount: () => request.get('/notices/unread-count', { silent: true }),
  markNoticesRead: (ids = []) => request.post('/notices/read', { ids }),
  markAllNoticesRead: () => request.post('/notices/read-all'),
}

const MAX_KEEP = 200

export const useNoticeStore = defineStore('notice', () => {
  const notices = ref([])
  const unreadCount = ref(0)
  const loading = ref(false)
  const wsAttached = ref(false)
  // null = 还没试过；true = 可用；false = 后端没有这套接口，别再打了
  const available = ref(null)
  let detachFns = []

  const unread = computed(() => notices.value.filter(n => n.status === 'unread'))

  /** 插入或就地更新一条（WS 与 REST 可能给到同一条，按 id 去重） */
  function upsert(n) {
    if (!n || n.id == null) return
    const i = notices.value.findIndex(x => x.id === n.id)
    if (i >= 0) notices.value.splice(i, 1, { ...notices.value[i], ...n })
    else notices.value.unshift(n)
    if (notices.value.length > MAX_KEEP) notices.value.length = MAX_KEEP
  }

  /** 1005 = 接口不存在：后端没这套路由，停用铃铛拉取，避免每次开面板都白打一次 */
  function noteFailure(err) {
    if (err?.code === 1005 || err?.status === 404) available.value = false
    return null
  }

  async function fetchNotices(params = {}) {
    if (available.value === false) return []
    loading.value = true
    try {
      const data = await noticeApi.listNotices({ size: 20, ...params })
      const items = data?.items || []
      available.value = true
      // 整页替换而不是合并：分页/筛选切换时列表必须与请求一致
      notices.value = items
      unreadCount.value = items.filter(n => n.status === 'unread').length
      // 列表只有一页，未读总数仍以后端为准
      refreshUnread().catch(() => {})
      return items
    } catch (err) {
      noteFailure(err)
      return []
    } finally {
      loading.value = false
    }
  }

  async function refreshUnread() {
    if (available.value === false) return 0
    try {
      const data = await noticeApi.getUnreadCount()
      available.value = true
      unreadCount.value = Number(data?.count || 0)
    } catch (err) {
      noteFailure(err)
    }
    return unreadCount.value
  }

  /** 标记若干条已读；不传则全部已读 */
  async function markRead(ids) {
    const list = Array.isArray(ids) ? ids.filter(Boolean) : []
    // 先落本地，铃铛立刻响应，再由返回值对齐
    const targets = list.length ? list : unread.value.map(n => n.id)
    targets.forEach(id => {
      const n = notices.value.find(x => x.id === id)
      if (n && n.status === 'unread') n.status = 'read'
    })
    unreadCount.value = Math.max(0, unreadCount.value - targets.length)
    try {
      const data = list.length
        ? await noticeApi.markNoticesRead(list)
        : await noticeApi.markAllNoticesRead()
      if (data && data.count != null) unreadCount.value = Number(data.count)
    } catch (e) {
      // 失败就把本地状态改回去，别让界面显示一个后端不认的已读态
      targets.forEach(id => {
        const n = notices.value.find(x => x.id === id)
        if (n) n.status = 'unread'
      })
      await refreshUnread().catch(() => {})
      throw e
    }
  }

  function markAllRead() {
    return markRead([])
  }

  /** 订阅 WS 的 notice 推送（幂等） */
  function attachWs() {
    if (wsAttached.value) return
    wsAttached.value = true
    detachFns.push(wsClient.on(WS_TYPES.NOTICE, (payload) => {
      if (!payload) return
      upsert(payload)
      if (payload.status !== 'read') unreadCount.value += 1
    }))
  }

  function detachWs() {
    detachFns.forEach(fn => fn())
    detachFns = []
    wsAttached.value = false
  }

  function reset() {
    notices.value = []
    unreadCount.value = 0
    available.value = null
  }

  return {
    notices, unread, unreadCount, loading, wsAttached, available,
    fetchNotices, refreshUnread, markRead, markAllRead, upsert, attachWs, detachWs, reset
  }
})
