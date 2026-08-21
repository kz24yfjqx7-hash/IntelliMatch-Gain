import { test, expect } from '@playwright/test'
import { attachGuards, login, switchUser, nav, shot } from './helpers.js'

test('越权演示：vpp 模拟越权下发 → 1003 横幅 → admin 右上角铃铛 R01 告警', async ({ page }) => {
  const g = attachGuards(page)
  // 先用 admin 生成一条策略，供 vpp 越权下发（mock 内存共享于同一页面）
  await login(page, 'admin')
  await page.goto('/cloud/aggregate')
  await page.getByRole('button', { name: /生成调度策略/ }).click()
  await expect(page.getByRole('button', { name: /签名下发/ })).toBeEnabled({ timeout: 30000 })

  await switchUser(page, 'vpp')
  await nav(page, '/cloud/aggregate')
  // vpp 无 dispatch:issue：签名下发按钮被 v-permission 移除
  await expect(page.getByRole('button', { name: /签名下发/ })).toHaveCount(0)
  // 选中任务（列表中最近一条）
  const taskItem = page.locator('.upload-item').first()
  if (await taskItem.count()) await taskItem.click()
  const denyBtn = page.getByRole('button', { name: /模拟越权下发/ })
  await expect(denyBtn).toBeEnabled({ timeout: 20000 })
  await denyBtn.click()
  await expect(page.locator('.el-message').filter({ hasText: /越权/ }).first()).toBeVisible()
  await expect(page.locator('body')).toContainText(/1003/)
  await shot(page, 'dispatch-deny-1003')
  // 铃铛未确认计数出现（audit_alert 推送给当前页面）
  await expect(page.locator('.bell .el-badge__content')).toBeVisible()
  const badge = await page.locator('.bell .el-badge__content').innerText()
  expect(Number(badge)).toBeGreaterThan(0)
  await page.locator('.bell').click()
  await expect(page.locator('.alert-popper')).toContainText('R01')
  await shot(page, 'dispatch-deny-bell')
  await page.keyboard.press('Escape')

  // 审计中心出现 high 日志 + traceId 时间轴
  await nav(page, '/audit')
  await page.getByRole('tab', { name: '风险告警' }).click()
  await expect(page.locator('.el-tab-pane:visible')).toContainText('R01')
  await page.getByRole('tab', { name: '全流程追踪' }).click()
  await page.getByRole('button', { name: '追踪' }).click()
  await expect(page.locator('.el-timeline-item').first()).toBeVisible()
  // 唯一允许的 4xx：越权 issue 的 403（这正是被测现象）
  expect(g.errors.filter(e => !/403/.test(e))).toEqual([])
})
