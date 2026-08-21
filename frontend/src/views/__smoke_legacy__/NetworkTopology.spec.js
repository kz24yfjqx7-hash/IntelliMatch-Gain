/** 冒烟：NetworkTopology 挂载不抛错、关键标题渲染 */
import { describe, it, expect, beforeAll } from 'vitest'
import { mockEcharts, setupEnv, mountPage } from './_helper'

mockEcharts()
beforeAll(setupEnv)

describe('NetworkTopology 冒烟', () => {
  it('挂载后渲染标题「全网设备状态图」且无运行时错误', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/views/NetworkTopology.vue'))
    expect(errors, errors.map(e => e?.message).join('\n')).toEqual([])
    expect(wrapper.text()).toContain('全网设备状态图')
    expect(wrapper.text()).toContain('Node-A')
    expect(wrapper.text()).toMatch(/DID (活跃|冻结|注销)/)
    wrapper.unmount()
  })
})
