import { test, expect } from '@playwright/test'
import { attachGuards, login, nav, assertChartsHaveSize, assertNoHorizontalOverflow, shot } from './helpers.js'

test('云端调度：运行 DQN → 策略表 → AI 解释 → 签名下发 → 终端响应 ack', async ({ page }) => {
  test.setTimeout(150000)
  const g = attachGuards(page)
  await login(page, 'admin')
  await page.goto('/cloud/aggregate')
  await page.getByRole('button', { name: /生成调度策略/ }).click()
  await expect(page.getByRole('button', { name: /签名下发/ })).toBeEnabled({ timeout: 30000 })
  const body1 = await page.locator('body').innerText()
  expect(body1).toMatch(/charge|discharge|idle|充电|放电|待机/)
  expect(body1).toMatch(/SOC|约束/)
  await shot(page, 'dispatch-strategy')
  expect(await assertChartsHaveSize(page, '(aggregate)')).toBeGreaterThanOrEqual(1)

  // AI 解释
  await page.getByRole('button', { name: '为什么选择该节点放电？' }).click()
  await expect(page.locator('.source-badge.live, .source-badge.cache, .source-badge.rule').first()).toBeVisible({ timeout: 30000 })
  await expect(page.locator('body')).toContainText(/reasoning|推理|解释|answer|回答/i)

  // 签名下发
  await page.getByRole('button', { name: '生成', exact: true }).click()
  await page.getByRole('button', { name: /签名下发/ }).click()
  await expect(page.locator('body')).toContainText(/已下发|issued|下发成功/i, { timeout: 30000 })
  await shot(page, 'dispatch-issued')

  // 终端响应
  await nav(page, '/edge/response')
  await expect(page.locator('.el-select').first()).toBeVisible()
  // 若当前节点不是指令目标节点，页面给出一键切换提示
  const switchBtn = page.getByRole('button', { name: /切换到该节点查看/ })
  // 真后端数据持久：并发跑测时页面可能落在一条已回执的指令上，两种状态都算「指令已到达边端」
  const issuedChip = page.locator('.command-panel .status-chip', { hasText: /已下发|issued|已回执|acked/ })
  await expect(switchBtn.or(issuedChip)).toBeVisible()
  if (await switchBtn.count()) await switchBtn.click()
  await expect(issuedChip).toBeVisible()
  // 选中指令后页面自动执行 DID 验签（按钮变为「重新校验」）；若未自动触发则手动点
  const verifyBtn = page.getByRole('button', { name: /开始校验|重新校验/ })
  if ((await verifyBtn.innerText()).includes('开始校验')) await verifyBtn.click()
  await expect(page.locator('.verify-panel')).toHaveClass(/passed/, { timeout: 30000 })
  const execBtn = page.getByRole('button', { name: /确认执行并回执/ })
  if (await execBtn.count()) await execBtn.click()
  await expect(page.locator('.command-panel .status-chip')).toHaveText(/已回执|acked|已执行/, { timeout: 60000 })
  // mock 回执会返回 evidenceId；真后端 ack 响应无 evidenceId（BACKEND-ISSUES），两种模式都成立：有执行完成时间即可
  await expect(page.locator('.receipt-summary')).toContainText(/ev-|evidence|执行完成/i)
  await expect(page.getByRole('button', { name: /^已回执$/ })).toBeDisabled()
  await shot(page, 'terminal-ack')
  await assertNoHorizontalOverflow(page, '(response)')
  g.assertClean()
})

test('云端调度：无效签名触发 1004 验签失败提示', async ({ page }) => {
  const g = attachGuards(page)
  await login(page, 'admin')
  await page.goto('/cloud/aggregate')
  await page.getByRole('button', { name: /生成调度策略/ }).click()
  await expect(page.getByRole('button', { name: /签名下发/ })).toBeEnabled({ timeout: 30000 })
  await page.getByPlaceholder(/签发者 SM2 签名/).fill('invalid')
  await page.getByRole('button', { name: /签名下发/ }).click()
  await expect(page.locator('.el-message').filter({ hasText: /验签失败|1004/ }).first()).toBeVisible()
  // 唯一允许的 4xx：验签失败的 403（这正是被测现象）
  expect(g.errors.filter(e => !/403/.test(e))).toEqual([])
})
