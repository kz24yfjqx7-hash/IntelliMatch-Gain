/** 冒烟：TerminalResponse 挂载不抛错、关键标题渲染 */
import { describe, it, expect, beforeAll } from 'vitest'
import { mockEcharts, setupEnv, mountPage } from './_helper'

mockEcharts()
beforeAll(setupEnv)

describe('TerminalResponse 冒烟', () => {
  it('挂载后渲染标题「终端响应与执行」且无运行时错误', async () => {
    const { wrapper, errors } = await mountPage(() => import('@/views/TerminalResponse.vue'))
    expect(errors, errors.map(e => e?.message).join('\n')).toEqual([])
    expect(wrapper.text()).toContain('终端响应与执行')
    expect(wrapper.text()).toContain('DID 签名校验')
    wrapper.unmount()
  })
})
