/** 冒烟：CloudAggregate 挂载不抛错、关键标题渲染 */
import { describe, it, expect, beforeAll } from 'vitest'
import { mockEcharts, setupEnv, mountPage } from './_helper'

mockEcharts()
beforeAll(setupEnv)

describe('CloudAggregate 冒烟', () => {
  it('挂载后渲染标题「云端聚合与调度」且无运行时错误', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/views/CloudAggregate.vue'))
    expect(errors, errors.map(e => e?.message).join('\n')).toEqual([])
    expect(wrapper.text()).toContain('云端聚合与调度')
    expect(wrapper.text()).toContain('DQN 调度策略')
    expect(wrapper.text()).toContain('模拟越权下发')
    wrapper.unmount()
  })
})
