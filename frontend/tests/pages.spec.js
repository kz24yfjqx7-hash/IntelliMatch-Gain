/**
 * qa 页面回归：11 个页面（4 新中心 + 7 旧页）+ TrustFlowBanner 在 MSW 下挂载无抛错、有内容；
 * vpp_operator 登录时 CloudAggregate 的「签名下发」按钮被 v-permission 移除。
 * 复用 frontend-legacy 的挂载器 src/views/__smoke_legacy__/_helper.js。
 */
import { describe, it, expect, beforeAll } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { mockEcharts, setupEnv, mountPage } from '@/views/__smoke_legacy__/_helper.js'
import { useUserStore } from '@/stores/user'

mockEcharts()

const PAGES = [
  ['NetworkTopology', () => import('@/views/NetworkTopology.vue')],
  ['CloudAggregate', () => import('@/views/CloudAggregate.vue')],
  ['DataClassification', () => import('@/views/DataClassification.vue')],
  ['RiskAssessment', () => import('@/views/RiskAssessment.vue')],
  ['PrivacyCompute', () => import('@/views/PrivacyCompute.vue')],
  ['TerminalResponse', () => import('@/views/TerminalResponse.vue')],
  ['AuditLog', () => import('@/views/AuditLog.vue')],
  ['IdentityCenter', () => import('@/views/IdentityCenter.vue')],
  ['AssetsCenter', () => import('@/views/AssetsCenter.vue')],
  ['PermissionCenter', () => import('@/views/PermissionCenter.vue')],
  ['EvidenceCenter', () => import('@/views/EvidenceCenter.vue')],
  ['TrustFlowBanner', () => import('@/components/TrustFlowBanner.vue')]
]

describe('11 个页面 + TrustFlowBanner（admin）', () => {
  beforeAll(async () => { await setupEnv() }, 30000)

  for (const [name, loader] of PAGES) {
    it(`${name} 挂载无抛错且有内容`, async () => {
      const { wrapper, errors } = await mountPage(loader)
      expect(errors, `${name} 抛错: ${errors.map(e => e?.message).join(' | ')}`).toEqual([])
      expect(wrapper.text().trim().length).toBeGreaterThan(20)
      wrapper.unmount()
    }, 20000)
  }

  it('router 四个中心路由指向真实组件，无 _Placeholder 引用', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const src = fs.readFileSync(path.resolve(__dirname, '../src/router/index.js'), 'utf8')
    expect(src).not.toMatch(/_Placeholder/)
    for (const v of ['IdentityCenter', 'AssetsCenter', 'PermissionCenter', 'EvidenceCenter']) expect(src).toContain(`views/${v}.vue`)
  })

  it('假实现文件已删除且无引用', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const root = path.resolve(__dirname, '../src')
    expect(fs.existsSync(path.join(root, 'services/aiReportGenerator.js'))).toBe(false)
    expect(fs.existsSync(path.join(root, 'services/deepseekAdapter.js'))).toBe(false)
    const walk = d => fs.readdirSync(d).flatMap(n => { const p = path.join(d, n); return fs.statSync(p).isDirectory() ? walk(p) : [p] })
    const refs = walk(root).filter(f => /\.(js|vue|mjs)$/.test(f)).filter(f => /import[^\n]*(aiReportGenerator|deepseekAdapter)['"]/.test(fs.readFileSync(f, 'utf8')))
    expect(refs).toEqual([])
    const pyodide = walk(root).filter(f => /\.(js|vue|mjs)$/.test(f)).filter(f => /jsdelivr|pyodide\.js|loadPyodide/i.test(fs.readFileSync(f, 'utf8')))
    expect(pyodide).toEqual([])
  })
})

describe('vpp_operator 视角权限裁剪', () => {
  it('CloudAggregate 下「签名下发」按钮被 v-permission 移除；admin 可见', async () => {
    await setupEnv()
    const loader = () => import('@/views/CloudAggregate.vue')
    const issueButtons = w => w.findAll('button').filter(b => b.text().includes('签名下发'))
    let { wrapper } = await mountPage(loader)
    expect(issueButtons(wrapper).length).toBe(1)
    wrapper.unmount()
    const u = useUserStore()
    await u.logout()
    await u.login({ username: 'vpp', password: 'vpp123' })
    expect(u.hasPermission('dispatch:issue')).toBe(false)
    ;({ wrapper } = await mountPage(loader))
    expect(issueButtons(wrapper).length).toBe(0)
    wrapper.unmount()
  }, 40000)
})
