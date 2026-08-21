import { test, expect } from '@playwright/test'
import { attachGuards, login, assertNoHorizontalOverflow, assertChartsHaveSize, shot } from './helpers.js'

test.describe('首页驾驶舱 / 流程横幅 / 视角切换', () => {
  test('TrustFlowBanner 六环节计数非空且点击跳转', async ({ page }) => {
    const g = attachGuards(page)
    await login(page, 'admin')
    const stages = page.locator('.trust-flow .tf-stage')
    await expect(stages).toHaveCount(6)
    for (let i = 0; i < 6; i++) {
      await expect(stages.nth(i).locator('.tf-count')).not.toHaveText(/^(--|0)?$/, { timeout: 15000 })
    }
    const counts = await stages.locator('.tf-count').allInnerTexts()
    expect(counts.every(c => Number(c.replace(/,/g, '')) > 0)).toBeTruthy()
    // 拓扑 4 节点
    await expect(page.getByText('Node-A').first()).toBeVisible()
    await expect(page.getByText('Node-D').first()).toBeVisible()
    await assertChartsHaveSize(page, '(topology)')
    await assertNoHorizontalOverflow(page, '(topology)')
    await shot(page, 'home-topology-1366')

    const targets = ['/identity', '/assets', '/permission', '/edge/privacy', '/cloud/aggregate', '/evidence']
    for (let i = 0; i < 6; i++) {
      await page.goto('/cloud/topology')
      await page.locator('.trust-flow .tf-stage').nth(i).click()
      await page.waitForURL(new RegExp(targets[i].replace(/\//g, '\\/') + '$'))
    }
    g.assertClean()
  })

  test('视角切换 云端⇄边端 改变侧栏菜单', async ({ page }) => {
    const g = attachGuards(page)
    await login(page, 'admin')
    await expect(page.locator('.sidebar .menu-title', { hasText: '云端聚合与调度' })).toBeVisible()
    await page.locator('.switch-container').click()
    await expect(page.locator('.sidebar .menu-title', { hasText: '本地感知与分级' })).toBeVisible()
    await expect(page.locator('.sidebar .node-selector')).toBeVisible()
    // 选择节点 B
    await page.locator('.sidebar .node-option', { hasText: 'Node-B' }).click()
    await page.locator('.sidebar .menu-title', { hasText: '动态隐私风险评估' }).click()
    await page.waitForURL('**/edge/risk')
    await page.locator('.switch-container').click()
    await expect(page.locator('.sidebar .menu-title', { hasText: '云端聚合与调度' })).toBeVisible()
    g.assertClean()
  })

  test('WS node_status 推送：节点指标时间戳在 12s 内刷新', async ({ page }) => {
    const g = attachGuards(page)
    await login(page, 'admin')
    const before = await page.locator('body').innerText()
    await page.waitForTimeout(11000)
    const after = await page.locator('body').innerText()
    expect(after).not.toBe(before)
    g.assertClean()
  })
})
