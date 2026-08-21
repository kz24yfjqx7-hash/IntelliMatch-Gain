/** 冒烟：PrivacyCompute 挂载不抛错、关键标题渲染 */
import { describe, it, expect, beforeAll } from 'vitest'
import { mockEcharts, setupEnv, mountPage } from './_helper'

mockEcharts()
beforeAll(setupEnv)

describe('PrivacyCompute 冒烟', () => {
  it('挂载后渲染标题「边缘隐私保护计算」且无运行时错误', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/views/PrivacyCompute.vue'))
    expect(errors, errors.map(e => e?.message).join('\n')).toEqual([])
    expect(wrapper.text()).toContain('边缘隐私保护计算')
    expect(wrapper.text()).toContain('隐私预算仪表盘')
    expect(wrapper.text()).toContain('节点本地训练')
    wrapper.unmount()
  })
})
