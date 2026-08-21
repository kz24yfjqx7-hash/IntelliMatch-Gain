/** 认证与用户（契约 §2.1） */
import { http } from 'msw'
import { db, nextId, createDid } from '../db.js'
import { BASE, handle, ok, body, query, paginate, requireAuth, requirePerm, issueToken, userDto, writeAudit, MockError, clientIp, now } from '../helpers.js'

export const authHandlers = [
  http.post(`${BASE}/auth/login`, handle(async ({ request, traceId }) => {
    const { username, password } = await body(request)
    const user = db.users.find(u => u.username === username)
    if (!user || user.password !== password) {
      writeAudit({ traceId, user: user || { realName: username }, module: 'auth', action: 'login', result: 'failed', riskLevel: 'medium', detail: '用户名或密码错误', ip: clientIp(request) })
      throw new MockError(1002, '用户名或密码错误')
    }
    if (user.status === 'disabled') throw new MockError(1003, '账号已禁用')
    const token = issueToken(user)
    writeAudit({ traceId, user, module: 'auth', action: 'login', result: 'success', detail: `${user.realName} 登录`, ip: clientIp(request) })
    return ok({ token, expiresIn: 28800, user: userDto(user) }, traceId)
  })),

  http.post(`${BASE}/auth/logout`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const auth = request.headers.get('Authorization') || ''
    db.sessions.delete(auth.replace(/^Bearer\s+/i, ''))
    writeAudit({ traceId, user, module: 'auth', action: 'logout', detail: '退出登录' })
    return ok({ loggedOut: true }, traceId)
  })),

  http.get(`${BASE}/auth/me`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    return ok(userDto(user, true), traceId)
  })),

  http.get(`${BASE}/users`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    requirePerm(user, 'user:manage', { request, traceId, resourceType: 'user', detail: '非管理员访问用户列表' })
    const q = query(request)
    let list = db.users.slice()
    if (q.keyword) list = list.filter(u => u.username.includes(q.keyword) || u.realName.includes(q.keyword))
    if (q.role) list = list.filter(u => u.roles.includes(q.role))
    const page = paginate(list, q)
    return ok({ ...page, items: page.items.map(u => userDto(u, true)) }, traceId)
  })),

  http.post(`${BASE}/users`, handle(async ({ request, traceId }) => {
    const admin = requireAuth(request)
    requirePerm(admin, 'user:manage', { request, traceId, resourceType: 'user' })
    const data = await body(request)
    if (!data.username || !data.password) throw new MockError(1001, 'username/password 必填')
    if (db.users.find(u => u.username === data.username)) throw new MockError(1006, '用户名已存在')
    const roles = Array.isArray(data.roles) && data.roles.length ? data.roles : ['energy_subject']
    if (roles.some(r => !db.roles.find(x => x.code === r))) throw new MockError(1001, '角色不存在')
    const { rec } = createDid({ subjectType: 'user', subjectName: data.realName || data.username, orgName: data.orgName || '', traceId, actorDid: admin.did, metadata: { username: data.username } })
    const user = { id: nextId('user'), username: data.username, password: data.password, realName: data.realName || data.username, roles, did: rec.did, orgName: data.orgName || '', status: 'active', createdAt: now(), updatedAt: now() }
    db.users.push(user)
    writeAudit({ traceId, user: admin, module: 'auth', action: 'user:create', resourceType: 'user', resourceId: user.id, detail: `新建用户 ${user.username}（${roles.join(',')}）` })
    return ok(userDto(user), traceId)
  })),

  http.put(`${BASE}/users/:id`, handle(async ({ request, params, traceId }) => {
    const admin = requireAuth(request)
    requirePerm(admin, 'user:manage', { request, traceId, resourceType: 'user' })
    const user = db.users.find(u => u.id === Number(params.id))
    if (!user) throw new MockError(1005, '用户不存在')
    const data = await body(request)
    if (data.realName) user.realName = data.realName
    if (data.orgName !== undefined) user.orgName = data.orgName
    if (data.password) user.password = data.password
    if (data.status) user.status = data.status
    if (Array.isArray(data.roles) && data.roles.length) user.roles = data.roles
    user.updatedAt = now()
    writeAudit({ traceId, user: admin, module: 'auth', action: 'user:update', resourceType: 'user', resourceId: user.id, riskLevel: 'medium', detail: `修改用户 ${user.username}` })
    return ok(userDto(user), traceId)
  })),

  http.delete(`${BASE}/users/:id`, handle(async ({ request, params, traceId }) => {
    const admin = requireAuth(request)
    requirePerm(admin, 'user:manage', { request, traceId, resourceType: 'user' })
    const idx = db.users.findIndex(u => u.id === Number(params.id))
    if (idx < 0) throw new MockError(1005, '用户不存在')
    if (db.users[idx].id === admin.id) throw new MockError(1006, '不能删除当前登录用户')
    const [removed] = db.users.splice(idx, 1)
    writeAudit({ traceId, user: admin, module: 'auth', action: 'user:delete', resourceType: 'user', resourceId: removed.id, riskLevel: 'medium', detail: `删除用户 ${removed.username}` })
    return ok({ deleted: true, id: removed.id }, traceId)
  }))
]
