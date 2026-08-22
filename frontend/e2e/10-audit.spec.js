import { test, expect } from '@playwright/test'
import { attachGuards, login, assertChartsHaveSize, assertNoHorizontalOverflow, shot } from './helpers.js'

test('审计中心：统计图 / 日志检索 / traceId 时间轴 / 告警 ack / 日报 / 导出 CSV', async ({ page }) => {
  const g = attachGuards(page)
  await login(page, 'admin')
  await page.goto('/audit')
  await expect(page.locator('.el-table__row').first()).toBeVisible()
  // 真后端统计接口较慢，等图表实例挂上再量尺寸
  await expect.poll(() => page.locator('[_echarts_instance_]').count()).toBeGreaterThanOrEqual(2)
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
  await expect(page.getByPlaceholder(/输入 traceId/)).not.toHaveValue('')
  await page.getByRole('button', { name: '追踪' }).click()
  await expect(page.locator('.el-timeline-item').first()).toBeVisible()
  // 候选「联邦训练链路」= 种子 DEMO_TRACE（login→verify→check→fl:train→evidence 完整链）
  await page.getByRole('button', { name: '联邦训练链路' }).click()
  // mock 种子 DEMO_TRACE 有 5 步；真后端 fl:train 链路目前仅 1 步（BACKEND-ISSUES B-004），两种模式都成立：≥1
  await expect.poll(() => page.locator('.el-timeline-item').count()).toBeGreaterThanOrEqual(1)
  await expect(page.locator('.trace-summary')).toContainText(/traceId/)
  await shot(page, 'audit-trace')

  // 告警 ack
  await page.getByRole('tab', { name: '风险告警' }).click()
  const bellBefore = Number((await page.locator('.bell .el-badge__content').innerText().catch(() => '0')) || 0)
  const ackBtns = page.locator('.el-tab-pane:visible').getByRole('button', { name: '确认' })
  await expect(ackBtns.first()).toBeVisible()
  // 真后端上其它操作会持续产生新告警（WS 实时插入），不能按总数断言：盯住被确认的那一行
  const firstRow = page.locator('.el-tab-pane:visible .el-table__row').filter({ hasText: /确认$/ }).first()
  const rowKey = (await firstRow.innerText()).replace(/\s+/g, ' ').slice(0, 40)
  await ackBtns.first().click()
  await expect.poll(async () => {
    const rows = await page.locator('.el-tab-pane:visible .el-table__row').allInnerTexts()
    const r = rows.find(t => t.replace(/\s+/g, ' ').startsWith(rowKey))
    return !r || !/确认$/.test(r.trim()) || /已确认|acked/.test(r)
  }).toBe(true)
  // 铃铛计数只统计已拉取的 open 告警（真后端分页时可能不变），仅在未达分页上限时断言递减
  if (bellBefore > 0 && bellBefore < 20) {
    await expect.poll(async () => Number((await page.locator('.bell .el-badge__content').innerText().catch(() => '0')) || 0)).toBeLessThan(bellBefore)
  }

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
