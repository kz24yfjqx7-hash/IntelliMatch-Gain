/**
 * v-permission 指令：无权限元素被移除；.disable 修饰符置 disabled；数组任一满足。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, withDirectives, resolveDirective } from 'vue'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('element-plus', () => ({ ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }))

import { permission } from '@/directives/permission'
import { useUserStore } from '@/stores/user'

function mountWith(perms, value, modifiers = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useUserStore()
  store.permissions = perms
  const Comp = defineComponent({
    directives: { permission },
    render() {
      const dir = resolveDirective('permission')
      return h('div', [
        withDirectives(h('button', { id: 'btn' }, '下发'), [[dir, value, '', modifiers]]),
        h('span', { id: 'keep' }, 'keep')
      ])
    }
  })
  return mount(Comp, { global: { plugins: [pinia] } })
}

describe('v-permission', () => {
  beforeEach(() => localStorage.clear())

  it('有权限：元素保留', () => {
    const w = mountWith(['dispatch:issue', 'asset:read'], 'dispatch:issue')
    expect(w.find('#btn').exists()).toBe(true)
    expect(w.find('#keep').exists()).toBe(true)
  })

  it('无权限：元素被移除', () => {
    const w = mountWith(['asset:read'], 'dispatch:issue')
    expect(w.find('#btn').exists()).toBe(false)
    expect(w.find('#keep').exists()).toBe(true)
  })

  it('.disable 修饰符：无权限时置 disabled 而不移除', () => {
    const w = mountWith(['asset:read'], 'dispatch:issue', { disable: true })
    const btn = w.find('#btn')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('数组：任一满足即可', () => {
    expect(mountWith(['asset:export'], ['asset:write', 'asset:export']).find('#btn').exists()).toBe(true)
    expect(mountWith(['asset:read'], ['asset:write', 'asset:export']).find('#btn').exists()).toBe(false)
  })

  it('空权限集合下 vpp_operator 不应看到 dispatch:issue 按钮', () => {
    const w = mountWith(['asset:read', 'asset:write', 'model:read', 'dispatch:read', 'evidence:read'], 'dispatch:issue')
    expect(w.find('#btn').exists()).toBe(false)
  })
})
