import { test, expect } from '@playwright/test'
import { attachGuards, login, assertChartsHaveSize, assertNoHorizontalOverflow, shot } from './helpers.js'

test('审计中心：统计图 / 日志检索 / traceId 时间轴 / 告警 ack / 日报 / 导出 CSV', async ({ page }) => {
  const g = attachGuards(page)
  await login(page, 'admin')
  await page.goto('/audit')
  await expect(page.locator('.el-table__row').first()).toBeVisible()
  expect(await assertChartsHaveSize(page, '(audit)')).toBeGreaterThanOrEqual(2)

  // 导出 CSV
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 15000 }),
    page.getByRole('button', { name: '导出 CSV' }).click()
  ])
  expect(download.suggestedFilename()).toMatch(/\.csv$/)

  // 日志筛选 riskLevel=high
  await page.locator('.el-select', { hasText: /riskLevel/ }).first().click()
  await page.locator('.el-select-dropdown:visible .el-select-dropdown__item', { hasText: /high|高/ }).first().click()
  await page.getByRole('button', { name: '查询' }).click()
  await page.waitForTimeout(500)
  await expect(page.locator('.el-tab-pane:visible .el-table__row').first()).toBeVisible()

  // traceId 时间轴（默认最近高风险）
  await page.getByRole('tab', { name: '全流程追踪' }).click()
  await page.getByRole('button', { name: '追踪' }).click()
  await expect(page.locator('.el-timeline-item').first()).toBeVisible()
  // 候选「联邦训练链路」= 种子 DEMO_TRACE（login→verify→check→fl:train→evidence 完整链）
  await page.getByRole('button', { name: '联邦训练链路' }).click()
  await expect.poll(() => page.locator('.el-timeline-item').count()).toBeGreaterThanOrEqual(3)
  await expect(page.locator('.trace-summary')).toContainText(/traceId/)
  await shot(page, 'audit-trace')

  // 告警 ack
  await page.getByRole('tab', { name: '风险告警' }).click()
  const bellBefore = Number((await page.locator('.bell .el-badge__content').innerText().catch(() => '0')) || 0)
  const ackBtn = page.locator('.el-tab-pane:visible').getByRole('button', { name: '确认' }).first()
  await expect(ackBtn).toBeVisible()
  await ackBtn.click()
  await expect.poll(async () => Number((await page.locator('.bell .el-badge__content').innerText().catch(() => '0')) || 0)).toBeLessThan(bellBefore)

  // 日报
  await page.getByRole('tab', { name: '审计报告' }).click()
  await page.getByRole('button', { name: '生成报告' }).click()
  await expect(page.locator('.el-tab-pane:visible')).toContainText(/narrative|解读|日报/i, { timeout: 20000 })
  await expect(page.locator('.el-tab-pane:visible .source-badge').first()).toBeVisible()
  await shot(page, 'audit-report')
  await page.getByRole('tab', { name: '前端操作记录' }).click()
  await assertNoHorizontalOverflow(page, '(audit)')
  g.assertClean()
})
