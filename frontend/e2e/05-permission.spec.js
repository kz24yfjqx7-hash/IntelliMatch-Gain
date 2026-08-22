import { test, expect } from '@playwright/test'
import { attachGuards, login, switchUser, nav, assertNoHorizontalOverflow, shot } from './helpers.js'

/** 申请审批 tab 只看 pending（真后端分页时保证目标行在首页） */
async function filterPending(page) {
  const pane = page.locator('.el-tab-pane:visible')
  await pane.locator('.el-radio-button', { hasText: /^pending$/ }).click()
  await pane.getByRole('button', { name: '查询' }).click()
  await page.waitForTimeout(300)
}

test('权限中心：subject 申请 → admin 审批 → 授权生效 → 校验器', async ({ page }) => {
  const g = attachGuards(page)
  await login(page, 'subject')
  await page.goto('/permission')
  await page.getByRole('button', { name: /申请权限/ }).click()
  const dlg = page.locator('.el-dialog', { hasText: '申请权限' })
  await expect(dlg).toBeVisible()
  await dlg.locator('.el-form-item', { hasText: 'resourceType' }).locator('.el-radio-button', { hasText: /^asset$/ }).click()
  await dlg.getByPlaceholder(/资产 ID/).fill('1001')
  await dlg.locator('.el-form-item').filter({ has: page.locator('label', { hasText: /^action$/ }) }).locator('.el-radio-button', { hasText: /^read$/ }).click()
  await dlg.locator('textarea').fill('用于负荷预测（E2E）')
  await dlg.getByRole('button', { name: '提交', exact: true }).click()
  // 真后端数据持久：上次运行留下的待审批同类申请会返回 1006「重复提交」，此时关掉对话框沿用旧申请
  const dup = page.locator('.el-message').filter({ hasText: /重复/ })
  await expect(dlg.locator('visible=true').first().or(dup.first())).toBeVisible()
  await expect.poll(async () => (await dup.count()) > 0 || !(await dlg.isVisible())).toBe(true)
  if (await dlg.isVisible()) await dlg.getByRole('button', { name: '取消' }).click()
  await expect(dlg).toBeHidden()
  await page.getByRole('tab', { name: '申请审批' }).click()
  // 真后端数据持久化：列表按时间倒序分页，本申请可能不在首页 → 只看 pending；且历史上可能已有同名申请 → 取第一条
  await filterPending(page)
  await expect(page.locator('.el-tab-pane:visible .el-table__row', { hasText: '用于负荷预测（E2E）' }).first()).toBeVisible()
  // subject 无审批按钮
  await expect(page.getByRole('button', { name: '通过' })).toHaveCount(0)

  // admin 审批（同一页面切换账号，MSW 内存状态延续）
  await switchUser(page, 'admin')
  await nav(page, '/permission')
  await page.getByRole('tab', { name: '申请审批' }).click()
  await filterPending(page)
  const row = page.locator('.el-tab-pane:visible .el-table__row', { hasText: '用于负荷预测（E2E）' }).first()
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: '通过' }).click()
  const review = page.locator('.el-dialog', { hasText: '审批通过' })
  await review.locator('textarea').fill('同意（E2E）')
  await review.getByRole('button', { name: '通过并生成授权' }).click()
  await expect(review).toBeHidden()
  // 审批后该行离开 pending 列表 → 切到 approved 再核对
  await page.locator('.el-tab-pane:visible .el-radio-button', { hasText: /^approved$/ }).click()
  await page.locator('.el-tab-pane:visible').getByRole('button', { name: '查询' }).click()
  await expect(page.locator('.el-tab-pane:visible .el-table__row', { hasText: '用于负荷预测（E2E）' }).first()).toContainText(/approved|已通过/)
  await shot(page, 'permission-approved')

  await page.getByRole('tab', { name: '已授权管理' }).click()
  await expect(page.locator('.el-tab-pane:visible .el-table__row').first()).toBeVisible()
  await expect(page.locator('.el-tab-pane:visible')).toContainText('1001')

  // 矩阵
  await page.getByRole('tab', { name: '权限矩阵' }).click()
  await expect(page.locator('.el-tab-pane:visible td.cell').first()).toBeVisible()

  // 校验器
  await page.getByRole('tab', { name: '权限校验测试器' }).click()
  await page.getByRole('button', { name: /vpp 尝试 dispatch:issue/ }).click()
  await page.getByRole('button', { name: /校\s*验/ }).click()
  await expect(page.locator('.el-tab-pane:visible')).toContainText('DENIED')
  await page.getByRole('button', { name: /admin 读取 asset 1001/ }).click()
  await page.getByRole('button', { name: /校\s*验/ }).click()
  await expect(page.locator('.el-tab-pane:visible')).toContainText('ALLOWED')
  await shot(page, 'permission-check')
  await assertNoHorizontalOverflow(page, '(permission)')
  g.assertClean()
})
