import { test, expect } from '@playwright/test'
import { attachGuards, login, assertChartsHaveSize, assertNoHorizontalOverflow, shot } from './helpers.js'

test('存证中心：篡改演示 → verify intact:false 标红 → 链状态 brokenAt', async ({ page }) => {
  const g = attachGuards(page)
  await login(page, 'admin')
  await page.goto('/evidence')
  await expect(page.locator('.block').first()).toBeVisible()
  await expect(page.locator('.el-table__row').first()).toBeVisible()
  await assertChartsHaveSize(page, '(evidence)')
  await assertNoHorizontalOverflow(page, '(evidence)')

  // 校验一条（篡改前应 intact）
  await page.locator('.el-table__row').first().getByRole('button', { name: '校验' }).click()
  const vdlg = page.locator('.el-dialog', { hasText: '完整性校验结果' })
  await expect(vdlg).toBeVisible()
  await expect(vdlg).toContainText(/完整|intact|一致/i)
  await vdlg.locator('.el-dialog__headerbtn').click()

  await page.getByRole('button', { name: /篡改演示/ }).click()
  const tdlg = page.locator('.el-dialog', { hasText: '篡改演示' })
  await expect(tdlg).toBeVisible()
  await tdlg.getByRole('button', { name: '执行篡改并校验' }).click()
  await expect(page.locator('.el-dialog', { hasText: '完整性校验结果' })).toBeVisible({ timeout: 20000 })
  await expect(page.locator('.el-dialog', { hasText: '完整性校验结果' })).toContainText(/不一致|false|篡改|≠/i)
  await shot(page, 'evidence-tampered-verify')
  await page.locator('.el-dialog:visible .el-dialog__headerbtn').first().click()
  await expect(page.locator('.el-dialog', { hasText: '完整性校验结果' })).toBeHidden()
  // 链状态断裂
  await expect(page.locator('body')).toContainText(/断裂|brokenAt/i)
  // 标红块 / 行
  await expect(page.locator('.block.tampered').first()).toBeInViewport()
  await expect(page.locator('.el-table__row.tampered-row').first()).toBeVisible()
  expect(await page.locator('.block.affected').count()).toBeGreaterThan(0)
  await shot(page, 'evidence-chain-broken')

  // 详情 + 凭证导出 + traceId 追踪
  await page.locator('.el-table__row').first().getByRole('button', { name: '详情' }).click()
  const drawer = page.locator('.el-drawer', { hasText: '存证详情' })
  await expect(drawer).toBeVisible()
  const dl = page.waitForEvent('download', { timeout: 10000 }).catch(() => null)
  await drawer.getByRole('button', { name: '导出凭证' }).click()
  await dl
  const hasTrace = await drawer.getByRole('button', { name: '追踪链路' }).count()
  if (hasTrace) {
    await drawer.getByRole('button', { name: '追踪链路' }).click()
    await expect(page.locator('.el-timeline-item').first()).toBeVisible()
  } else {
    await page.keyboard.press('Escape')
  }
  g.assertClean()

  // 收尾：把本用例的篡改演示还原，避免把链留在 broken 状态污染后续用例与演示环境
  // （mock 模式下该接口不存在，忽略失败即可）
  await page.evaluate(async () => {
    try {
      const token = localStorage.getItem('energy-tds-token')
      await fetch('/api/v1/evidence/demo/restore-all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: '{}'
      })
    } catch { /* 忽略 */ }
  })
})
