/** 冒烟：DataClassification 挂载不抛错、关键标题渲染 */
import { describe, it, expect, beforeAll } from 'vitest'
import { mockEcharts, setupEnv, mountPage } from './_helper'

mockEcharts()
beforeAll(setupEnv)

describe('DataClassification 冒烟', () => {
  it('挂载后渲染标题「本地感知与分级」且无运行时错误', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/views/DataClassification.vue'))
    expect(errors, errors.map(e => e?.message).join('\n')).toEqual([])
    expect(wrapper.text()).toContain('本地感知与分级')
    expect(wrapper.text()).toContain('开始分级')
    expect(wrapper.text()).toContain('光伏发电功率')
    wrapper.unmount()
  })
})
