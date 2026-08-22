import { test, expect } from '@playwright/test'
import { attachGuards, login, pickSelect, assertNoHorizontalOverflow, shot } from './helpers.js'

test('身份中心：注册 DID → 文档 → 验签通过 / 无效签名失败', async ({ page }) => {
  const g = attachGuards(page)
  await login(page, 'admin')
  await page.goto('/identity')
  await expect(page.getByRole('tab', { name: 'DID 管理' })).toBeVisible()
  await page.getByRole('tab', { name: 'DID 管理' }).click()
  // 真后端统计加载较慢，等到出现数字再取基线
  const didTotal = page.locator('.stat-card', { hasText: 'DID 总数' }).locator('.stat-value, .value').first()
  await expect(didTotal).toHaveText(/\d/)
  const totalBefore = Number((await didTotal.innerText()).replace(/\D/g, ''))

  await page.getByRole('button', { name: /注册 DID/ }).click()
  const dlg = page.locator('.el-dialog', { hasText: '注册 DID' })
  await expect(dlg).toBeVisible()
  await dlg.locator('.el-form-item', { hasText: '主体类型' }).locator('.el-radio-button', { hasText: /device|设备/ }).click()
  await dlg.getByPlaceholder(/光伏逆变器/).fill('答辩现场逆变器-01')
  await dlg.getByPlaceholder(/园区/).fill('E2E 测试园区')
  await dlg.getByRole('button', { name: '签发并上链' }).click()

  const result = page.locator('.el-dialog', { hasText: /注册成功|签发成功|DID/ }).filter({ hasText: /私钥/ })
  await expect(result).toBeVisible()
  const didText = await result.locator('text=/did:vpp:device:[0-9a-fx]+/').first().innerText()
  expect(didText).toMatch(/^did:vpp:device:/)
  await shot(page, 'identity-register-result')
  await result.getByRole('button', { name: '关闭', exact: true }).click()

  // 列表出现新 DID
  await page.getByPlaceholder('DID / 名称 / 机构').fill('答辩现场逆变器-01')
  await page.getByRole('button', { name: '查询' }).first().click()
  const row = page.locator('.el-table__row', { hasText: '答辩现场逆变器-01' }).first()
  await expect(row).toBeVisible()

  // 文档
  await row.getByRole('button', { name: '文档' }).click()
  const drawer = page.locator('.el-drawer', { hasText: 'DID 文档' })
  await expect(drawer).toBeVisible()
  await expect(drawer).toContainText('w3id.org/did/v1')
  await expect(drawer).toContainText('verificationMethod')
  await page.keyboard.press('Escape')
  await expect(drawer).toBeHidden()

  // 验签
  await row.getByRole('button', { name: '验签' }).click()
  await expect(page.getByRole('tab', { name: '验签演示' })).toHaveAttribute('aria-selected', 'true')
  const vf = page.locator('.el-tab-pane:visible')
  await vf.getByPlaceholder(/签名后得到/).fill('')
  await vf.getByRole('button', { name: '使用刚注册的私钥模拟签名' }).click()
  await vf.getByRole('button', { name: /验\s*签/ }).click()
  await expect(page.locator('body')).toContainText(/验签通过|valid/i)

  await vf.getByRole('button', { name: '填入无效签名' }).click()
  await vf.getByRole('button', { name: /验\s*签/ }).click()
  await expect(page.locator('body')).toContainText(/验签失败|invalid|不通过/i)

  // 统计 +1
  await page.getByRole('button', { name: '刷新' }).first().click()
  await expect.poll(async () => Number((await page.locator('.stat-card', { hasText: 'DID 总数' }).locator('.stat-value, .value').first().innerText()).replace(/\D/g, ''))).toBe(totalBefore + 1)

  // 密钥管理 Tab 可渲染
  await page.getByRole('tab', { name: '密钥管理' }).click()
  await expect(page.locator('.el-tab-pane:visible .el-table__row').first()).toBeVisible()
  await page.getByRole('tab', { name: '用户管理' }).click()
  await expect(page.locator('.el-tab-pane:visible .el-table__row', { hasText: 'admin' })).toBeVisible()
  await assertNoHorizontalOverflow(page, '(identity)')
  g.assertClean()
})

test('身份中心：冻结 / 解冻 DID 与 轮换密钥', async ({ page }) => {
  const g = attachGuards(page)
  await login(page, 'admin')
  await page.goto('/identity')
  await page.getByRole('tab', { name: 'DID 管理' }).click()
  const row = page.locator('.el-tab-pane:visible .el-table__row').filter({ has: page.getByRole('button', { name: '冻结' }) }).first()
  const did = await row.locator('td').nth(0).innerText()
  await row.getByRole('button', { name: '冻结', exact: true }).click()
  const dlg = page.locator('.el-dialog:visible')
  await dlg.getByPlaceholder(/原因/).fill('E2E 冻结测试')
  await dlg.getByRole('button', { name: /确认/ }).click()
  await expect(dlg).toBeHidden()
  const frozenRow = page.locator('.el-tab-pane:visible .el-table__row', { hasText: did.slice(0, 20) }).first()
  await expect(frozenRow.getByRole('button', { name: '解冻' })).toBeVisible()
  await frozenRow.getByRole('button', { name: '解冻' }).click()
  await page.locator('.el-dialog:visible').getByRole('button', { name: /确认/ }).click()
  await expect(frozenRow.getByRole('button', { name: '冻结', exact: true })).toBeVisible()
  await frozenRow.getByRole('button', { name: '轮换密钥' }).click()
  await expect(page.locator('.el-dialog:visible, .el-message-box:visible').first()).toBeVisible()
  const confirm = page.locator('.el-message-box:visible .el-button--primary, .el-dialog:visible .el-button--primary').first()
  await confirm.click()
  await page.waitForTimeout(1000)
  g.assertClean()
})
