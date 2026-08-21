/** 冒烟：AuditLog 挂载不抛错、关键标题渲染 */
import { describe, it, expect, beforeAll } from 'vitest'
import { mockEcharts, setupEnv, mountPage } from './_helper'

mockEcharts()
beforeAll(setupEnv)

describe('AuditLog 冒烟', () => {
  it('挂载后渲染标题「系统审计与日志中心」且无运行时错误', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/views/AuditLog.vue'))
    expect(errors, errors.map(e => e?.message).join('\n')).toEqual([])
    expect(wrapper.text()).toContain('系统审计与日志中心')
    expect(wrapper.text()).toContain('全流程追踪')
    expect(wrapper.text()).toMatch(/tr-\d{8}-[0-9a-f]{8}/)
    wrapper.unmount()
  })
})
