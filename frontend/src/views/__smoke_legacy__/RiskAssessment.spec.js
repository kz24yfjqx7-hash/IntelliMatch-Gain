/** 冒烟：RiskAssessment 挂载不抛错、关键标题渲染 */
import { describe, it, expect, beforeAll } from 'vitest'
import { mockEcharts, setupEnv, mountPage } from './_helper'

mockEcharts()
beforeAll(setupEnv)

describe('RiskAssessment 冒烟', () => {
  it('挂载后渲染标题「动态隐私风险评估」且无运行时错误', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/views/RiskAssessment.vue'))
    expect(errors, errors.map(e => e?.message).join('\n')).toEqual([])
    expect(wrapper.text()).toContain('动态隐私风险评估')
    expect(wrapper.text()).toContain('综合风险评分')
    expect(wrapper.text()).toMatch(/查询频率/)
    wrapper.unmount()
  })
})
