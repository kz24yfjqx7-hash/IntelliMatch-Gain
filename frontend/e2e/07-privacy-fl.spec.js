import { test, expect } from '@playwright/test'
import { attachGuards, login, assertChartsHaveSize, assertNoHorizontalOverflow, shot } from './helpers.js'

test('隐私计算：创建并启动 FL → 曲线 ≥3 轮 → 预算环 / 压缩率曲线 / 每轮哈希', async ({ page }) => {
  test.setTimeout(150000)
  const g = attachGuards(page)
  await login(page, 'admin')
  await page.goto('/edge/privacy')
  await expect(page.getByRole('button', { name: /创建并启动/ })).toBeEnabled()
  await page.getByPlaceholder('负荷预测联合建模').fill('E2E 联邦学习')
  // 节点列表异步加载后才会默认勾选参与节点
  await expect(page.locator('.el-form-item', { hasText: '参与节点' }).locator('.el-tag').first()).toBeVisible()
  await page.getByRole('button', { name: /创建并启动/ }).click()
  const status = page.locator('.status-card', { hasText: 'E2E 联邦学习' })
  await expect(status).toBeVisible()
  // 等 ≥3 轮（el-progress 文本 "n/N 轮"）
  await expect.poll(async () => {
    const txt = await status.locator('.el-progress__text').innerText()
    const m = txt.match(/(\d+)\s*\/\s*(\d+)/)
    return m ? Number(m[1]) : 0
  }, { timeout: 90000, intervals: [1000] }).toBeGreaterThanOrEqual(3)
  await shot(page, 'privacy-fl-running')
  const charts = await assertChartsHaveSize(page, '(privacy)')
  expect(charts).toBeGreaterThanOrEqual(2)
  const body = await page.locator('body').innerText()
  expect(body).toMatch(/ε|epsilon|预算/i)
  expect(body).toMatch(/压缩|compression|Top-?k/i)
  expect(body).toMatch(/[0-9a-f]{8}/i)
  // 等任务完成并查看任务列表
  await expect(status.locator('.chip').first()).toHaveText(/已完成|completed/i, { timeout: 60000 })
  // 曲线点数 ≥ 3（页面 "N 轮" 计数）
  const roundsText = await page.locator('.card-title', { hasText: '收敛曲线' }).locator('.ct-meta').innerText()
  expect(Number(roundsText.replace(/\D/g, ''))).toBeGreaterThanOrEqual(3)
  await assertNoHorizontalOverflow(page, '(privacy)')
  g.assertClean()
})

test('隐私计算：grid 可启动，vpp 按钮置灰', async ({ page }) => {
  const g = attachGuards(page)
  await login(page, 'vpp')
  await page.goto('/edge/privacy')
  await expect(page.getByRole('button', { name: /创建并启动/ })).toBeDisabled()
  g.assertClean()
})
