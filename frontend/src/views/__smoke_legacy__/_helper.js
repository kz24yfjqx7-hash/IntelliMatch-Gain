/**
 * frontend-legacy 冒烟测试公共挂载器：
 * - 依赖 tests/setup.js 启动的 MSW server（src/mocks/node.js）
 * - echarts 在 jsdom 无 canvas，用桩替代（含 graphic.LinearGradient/RadialGradient）
 * - admin 登录后挂载，flushPromises + 等待 600ms，收集 errorHandler 捕获的错误
 */
import { expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import { permission } from '@/directives/permission'
import { useUserStore } from '@/stores/user'
import { usePerspectiveStore } from '@/stores/perspective'

export function mockEcharts() {
  vi.mock('echarts', () => {
    const chart = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), on: vi.fn() }
    class Gradient { constructor() { /* 桩 */ } }
    const api = { init: vi.fn(() => chart), graphic: { LinearGradient: Gradient, RadialGradient: Gradient } }
    return { ...api, default: api }
  })
}

let pinia
let router

export async function setupEnv() {
  expect(Boolean(globalThis.__mswServer), 'src/mocks/node.js 未就绪').toBe(true)
  pinia = createPinia()
  setActivePinia(pinia)
  router = createRouter({
    history: createMemoryHistory(),
    routes: ['/', '/cloud/topology', '/cloud/aggregate', '/edge/classification', '/edge/risk', '/edge/privacy', '/edge/response', '/audit', '/identity', '/assets', '/permission', '/evidence']
      .map(path => ({ path, component: { template: '<div />' } }))
  })
  await router.push('/')
  await router.isReady()
  await useUserStore().login({ username: 'admin', password: 'admin123' })
  await usePerspectiveStore().fetchNodes()
  return { pinia, router }
}

export async function mountPage(loader, waitMs = 600) {
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
  await new Promise(r => setTimeout(r, waitMs))
  await flushPromises()
  return { wrapper, errors }
}
