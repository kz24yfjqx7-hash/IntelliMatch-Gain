import { test, expect } from '@playwright/test'
import { attachGuards, login, pickSelect, assertNoHorizontalOverflow, assertChartsHaveSize, shot } from './helpers.js'

test('资产中心：自动分级 → 登记上链 → 详情 → 溯源', async ({ page }) => {
  const g = attachGuards(page)
  await login(page, 'admin')
  await page.goto('/assets')
  await expect(page.locator('.el-table__row').first()).toBeVisible()
  expect(await assertChartsHaveSize(page, '(assets)')).toBeGreaterThanOrEqual(2)

  await page.getByRole('button', { name: /登记资产/ }).click()
  const dlg = page.locator('.el-dialog', { hasText: '数据资产登记' })
  await expect(dlg).toBeVisible()
  await dlg.getByPlaceholder(/光伏出力/).fill('E2E 光伏出力-答辩')
  await dlg.locator('.el-form-item', { hasText: '数据类型' }).locator('.el-radio-button', { hasText: /pv|光伏/ }).first().click()
  await pickSelect(page, dlg.locator('.el-form-item', { hasText: '数据源 DID' }).locator('.el-select'), /did:vpp:/)
  await dlg.getByRole('button', { name: /自动分级/ }).click()
  await expect(dlg).toContainText(/L[1-4]/)
  await expect(dlg).toContainText(/score|得分|原因|reason/i)
  await shot(page, 'assets-classify')
  await dlg.getByRole('button', { name: '登记并上链' }).click()

  const result = page.locator('.el-dialog', { hasText: '登记成功' })
  await expect(result).toBeVisible()
  await expect(result).toContainText(/sm3:|[0-9a-f]{16}/)
  await expect(result).toContainText(/ev-|evidence/i)
  await result.getByRole('button', { name: '查看溯源' }).click()
  const lineage = page.locator('.el-drawer', { hasText: '溯源链路' })
  await expect(lineage).toBeVisible()
  await expect(lineage).toContainText(/登记|register/i)
  await expect(lineage.locator('[_echarts_instance_]')).toHaveCount(1)
  await shot(page, 'assets-lineage')
  await page.keyboard.press('Escape')
  await expect(lineage).toBeHidden()

  // 列表查到
  await page.getByPlaceholder('名称 / ID / 哈希').fill('E2E 光伏出力')
  await page.getByRole('button', { name: '查询' }).click()
  const row = page.locator('.el-table__row', { hasText: 'E2E 光伏出力' }).first()
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: '详情' }).click()
  const detail = page.locator('.el-drawer', { hasText: '资产详情' })
  await expect(detail).toBeVisible()
  await expect(detail).toContainText(/payload/i)
  await page.keyboard.press('Escape')

  // 饼图点击筛选不报错
  await page.locator('[_echarts_instance_]').first().click({ position: { x: 110, y: 110 } })
  await page.waitForTimeout(500)
  await assertNoHorizontalOverflow(page, '(assets)')
  g.assertClean()
})
