/**
 * frontend-pages 自用冒烟测试：四个中心页 + TrustFlowBanner 挂载即有内容。
 * - 依赖 tests/setup.js 启动的 MSW server（src/mocks/node.js）
 * - echarts 用桩替代（jsdom 无 canvas）
 * - 以 admin 登录后挂载，flushPromises 后断言无抛错且渲染了关键标题
 */
import { describe, it, expect, beforeAll, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import { permission } from '@/directives/permission'
import { useUserStore } from '@/stores/user'

vi.mock('echarts', () => {
  const chart = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), on: vi.fn() }
  return { init: vi.fn(() => chart), default: { init: vi.fn(() => chart) } }
})

const mswReady = () => Boolean(globalThis.__mswServer)

let pinia
let router
beforeAll(async () => {
  pinia = createPinia()
  setActivePinia(pinia)
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/identity', component: { template: '<div />' } },
      { path: '/assets', component: { template: '<div />' } },
      { path: '/permission', component: { template: '<div />' } },
      { path: '/evidence', component: { template: '<div />' } },
      { path: '/edge/privacy', component: { template: '<div />' } },
      { path: '/cloud/aggregate', component: { template: '<div />' } }
    ]
  })
  await router.push('/')
  await router.isReady()
  expect(mswReady(), 'src/mocks/node.js 未就绪').toBe(true)
  await useUserStore().login({ username: 'admin', password: 'admin123' })
})

async function mountPage(loader) {
  const { default: Comp } = await loader()
  const errors = []
  const wrapper = mount(Comp, {
    global: {
      plugins: [pinia, router, ElementPlus],
      directives: { permission },
      config: { errorHandler: e => errors.push(e) }
    },
    attachTo: document.body
  })
  await flushPromises()
  await new Promise(r => setTimeout(r, 400))
  await flushPromises()
  return { wrapper, errors }
}

describe('四个中心页冒烟', () => {
  it('IdentityCenter：渲染标题、DID 表格有数据', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/views/IdentityCenter.vue'))
    expect(errors).toEqual([])
    expect(wrapper.text()).toContain('统一身份与可信接入中心')
    expect(wrapper.text()).toContain('did:vpp:')
    expect(wrapper.text()).toContain('DID 总数')
    wrapper.unmount()
  })

  it('AssetsCenter：渲染标题、资产列表有数据', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/views/AssetsCenter.vue'))
    expect(errors).toEqual([])
    expect(wrapper.text()).toContain('能源数据资产中心')
    expect(wrapper.text()).toContain('资产总数')
    expect(wrapper.findAll('.el-table__row').length).toBeGreaterThan(0)
    wrapper.unmount()
  })

  it('PermissionCenter：渲染标题、角色卡片', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/views/PermissionCenter.vue'))
    expect(errors).toEqual([])
    expect(wrapper.text()).toContain('权限控制中心')
    expect(wrapper.text()).toContain('sys_admin')
    expect(wrapper.findAll('.role-card').length).toBeGreaterThanOrEqual(6)
    wrapper.unmount()
  })

  it('EvidenceCenter：渲染标题、链状态与区块', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/views/EvidenceCenter.vue'))
    expect(errors).toEqual([])
    expect(wrapper.text()).toContain('区块链存证中心')
    expect(wrapper.text()).toContain('链完整')
    expect(wrapper.findAll('.block').length).toBeGreaterThan(0)
    expect(wrapper.find('.el-button--danger').exists()).toBe(true) // 篡改演示按钮（admin）
    wrapper.unmount()
  })

  it('TrustFlowBanner：六环节计数均非 --', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/components/TrustFlowBanner.vue'))
    expect(errors).toEqual([])
    const counts = wrapper.findAll('.tf-count').map(n => n.text())
    expect(counts.length).toBe(6)
    counts.forEach(c => expect(c).not.toBe('--'))
    wrapper.unmount()
  })
})
