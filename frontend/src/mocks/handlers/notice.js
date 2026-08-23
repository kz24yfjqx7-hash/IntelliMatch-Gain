/** 站内消息（铃铛）。后端 modules/notice 的离线对应实现。 */
import { http } from 'msw'
import { db, nextId } from '../db.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, now } from '../helpers.js'
import { wsMock } from '../wsMock.js'

/** 供 permission handler 调用：给一组 DID 各写一条消息并推 WS。 */
export function pushNotice(dids, { category, level = 'info', title, content, link, refType, refId, actor }) {
  const targets = [...new Set((dids || []).filter(Boolean))]
  const created = []
  for (const did of targets) {
    const n = {
      id: nextId('notice'),
      recipientDid: did,
      category, level, title, content: content || null, link: link || null,
      refType: refType || null, refId: refId != null ? String(refId) : null,
      actorDid: actor?.did || null, actorName: actor?.realName || actor?.username || null,
      status: 'unread', readAt: null, createdAt: now(), traceId: null
    }
    db.notices.unshift(n)
    created.push(n)
    // 真后端是定向推送；mock 是单用户会话，直接 emit 即可
    wsMock.emit('notice', n)
  }
  return created
}

const mine = user => db.notices.filter(n => n.recipientDid === user.did)

export const noticeHandlers = [
  // 具体路径必须排在 /notices 之前，否则会被前缀吃掉
  http.get(`${BASE}/notices/unread-count`, handle(({ request, traceId }) => {
    const user = requireAuth(request)
    return ok({ count: mine(user).filter(n => n.status === 'unread').length }, traceId)
  })),

  http.get(`${BASE}/notices`, handle(({ request, traceId }) => {
    const user = requireAuth(request)
    const q = query(request)
    let items = mine(user)
    if (q.status) items = items.filter(n => n.status === q.status)
    if (q.category) items = items.filter(n => n.category === q.category)
    return ok(paginate(items, q), traceId)
  })),

  http.post(`${BASE}/notices/read-all`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    let updated = 0
    mine(user).forEach(n => { if (n.status === 'unread') { n.status = 'read'; n.readAt = now(); updated++ } })
    return ok({ updated, count: 0 }, traceId)
  })),

  http.post(`${BASE}/notices/read`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const { ids } = await body(request)
    const list = Array.isArray(ids) ? ids : []
    let updated = 0
    // ids 为空 = 全部已读，与后端一致
    const targets = list.length
      ? mine(user).filter(n => list.includes(n.id))
      : mine(user)
    targets.forEach(n => { if (n.status === 'unread') { n.status = 'read'; n.readAt = now(); updated++ } })
    return ok({ updated, count: mine(user).filter(n => n.status === 'unread').length }, traceId)
  }))
]

export default noticeHandlers
