/**
 * stores/user（登录 / 权限判断 / 持久化）与 stores/perspective（fetchNodes 后 currentNodeData 兼容字段）。
 * 依赖项目 MSW handlers（src/mocks/node.js）；handlers 未就绪时 login 相关用例会失败并提示。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('element-plus', () => ({ ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }))

import { useUserStore } from '@/stores/user'
import { usePerspectiveStore } from '@/stores/perspective'

const mswReady = () => Boolean(globalThis.__mswServer)

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('stores/user 纯逻辑', () => {
  it('初始未登录', () => {
    const s = useUserStore()
    expect(s.isLoggedIn).toBe(false)
    expect(s.permissions).toEqual([])
  })

  it('hasPermission / hasRole 判断', () => {
    const s = useUserStore()
    s.permissions = ['asset:read', 'dispatch:issue']
    s.user = { roles: ['grid_dispatcher'] }
    expect(s.hasPermission('asset:read')).toBe(true)
    expect(s.hasPermission('asset:write')).toBe(false)
    expect(s.hasPermission(['asset:write', 'dispatch:issue'])).toBe(true)
    expect(s.hasPermission()).toBe(true)
    expect(s.hasRole('grid_dispatcher')).toBe(true)
    expect(s.hasRole('sys_admin')).toBe(false)
  })

  it('clearSession 清空 token 并移除 localStorage', () => {
    const s = useUserStore()
    s.token = 'x'
    localStorage.setItem('energy-tds-token', 'x')
    s.clearSession()
    expect(s.token).toBe('')
    expect(localStorage.getItem('energy-tds-token')).toBeNull()
    expect(s.isLoggedIn).toBe(false)
  })
})

describe('stores/user 走 MSW 登录', () => {
  it('admin/admin123 登录成功：token 持久化、permissions 含 dispatch:issue', async () => {
    expect(mswReady(), 'src/mocks/node.js 未就绪').toBe(true)
    const s = useUserStore()
    await s.login({ username: 'admin', password: 'admin123' })
    expect(s.isLoggedIn).toBe(true)
    expect(s.token.split('.').length).toBe(3)
    expect(localStorage.getItem('energy-tds-token')).toBe(s.token)
    expect(s.roles).toContain('sys_admin')
    expect(s.hasPermission('dispatch:issue')).toBe(true)
    expect(s.hasPermission('algo:execute')).toBe(true)
    expect(s.user.did).toMatch(/^did:vpp:user:0x[0-9a-f]{32}$/)
  })

  it('vpp/vpp123 登录：无 dispatch:issue 权限', async () => {
    expect(mswReady(), 'src/mocks/node.js 未就绪').toBe(true)
    const s = useUserStore()
    await s.login({ username: 'vpp', password: 'vpp123' })
    expect(s.roles).toContain('vpp_operator')
    expect(s.hasPermission('dispatch:issue')).toBe(false)
    expect(s.hasPermission('asset:read')).toBe(true)
  })

  it('错误密码：reject，仍未登录', async () => {
    expect(mswReady(), 'src/mocks/node.js 未就绪').toBe(true)
    const s = useUserStore()
    await expect(s.login({ username: 'admin', password: 'wrong' })).rejects.toBeTruthy()
    expect(s.isLoggedIn).toBe(false)
  })

  it('logout 后清空', async () => {
    expect(mswReady(), 'src/mocks/node.js 未就绪').toBe(true)
    const s = useUserStore()
    await s.login({ username: 'admin', password: 'admin123' })
    await s.logout()
    expect(s.isLoggedIn).toBe(false)
    expect(s.permissions).toEqual([])
  })
})

describe('stores/perspective', () => {
  it('初始有 4 个种子节点且带 data 别名', () => {
    const p = usePerspectiveStore()
    expect(p.nodes.length).toBe(4)
    expect(p.currentNodeData).toMatchObject({ pvOutput: expect.any(Number), load: expect.any(Number), soc: expect.any(Number) })
  })

  it('fetchNodes 后 currentNodeData 兼容字段 pvOutput/load/soc，节点含 did/metrics', async () => {
    expect(mswReady(), 'src/mocks/node.js 未就绪').toBe(true)
    const u = useUserStore()
    await u.login({ username: 'admin', password: 'admin123' })
    const p = usePerspectiveStore()
    const nodes = await p.fetchNodes()
    expect(nodes.length).toBe(4)
    expect(nodes.map(n => n.id).sort()).toEqual(['Node-A', 'Node-B', 'Node-C', 'Node-D'])
    for (const n of nodes) {
      expect(n.did).toMatch(/^did:vpp:edge:0x[0-9a-f]{32}$/)
      expect(n.didStatus).toBe('active')
      expect(['online', 'warning', 'offline']).toContain(n.status)
      expect(n.metrics).toMatchObject({ pvOutput: expect.any(Number), storageOutput: expect.any(Number), load: expect.any(Number), soc: expect.any(Number) })
      expect(n.data).toMatchObject({ ...n.metrics, model: n.model })
    }
    p.setCurrentNode('Node-C')
    expect(p.currentNodeData.load).toBe(150)
    expect(p.currentNodeData.soc).toBe(42)
    expect(p.currentNodeInfo.name).toBe('虚拟电厂节点C')
    expect(p.getNodeById('Node-B').data.pvOutput).toBe(32.1)
  })

  it('cloud 视角菜单含四个中心；togglePerspective 切换', () => {
    const p = usePerspectiveStore()
    const paths = p.centerMenus.map(m => m.path)
    expect(paths).toEqual(expect.arrayContaining(['/identity', '/assets', '/permission', '/evidence']))
    expect(p.isCloud).toBe(true)
    p.togglePerspective()
    expect(p.isEdge).toBe(true)
    expect(p.currentMenus.map(m => m.path)).toContain('/audit')
  })

  it('applyNodeStatus 同步 metrics 与 data 别名', () => {
    const p = usePerspectiveStore()
    p.applyNodeStatus({ nodeId: 'Node-A', status: 'warning', metrics: { soc: 50 } })
    const n = p.getNodeById('Node-A')
    expect(n.status).toBe('warning')
    expect(n.metrics.soc).toBe(50)
    expect(n.data.soc).toBe(50)
  })
})
