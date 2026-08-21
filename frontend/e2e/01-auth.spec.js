import { test, expect } from '@playwright/test'
import { ACCOUNTS, attachGuards, login, logout, assertNoHorizontalOverflow, shot } from './helpers.js'

test.describe('登录 / 角色跳转 / 权限 / 退出', () => {
  for (const key of Object.keys(ACCOUNTS)) {
    test(`账号 ${key} 登录后按角色落地并显示角色标签`, async ({ page }) => {
      const g = attachGuards(page)
      const acc = await login(page, key)
      expect(new URL(page.url()).pathname).toBe(acc.home)
      await expect(page.locator('.role-tag')).toHaveText(acc.role)
      // 侧栏：身份 / 权限中心无权限要求，所有角色可见；资产 / 存证按 asset:read / evidence:read 过滤
      for (const t of ['身份与可信接入', '权限控制中心']) {
        await expect(page.locator('.sidebar .menu-title', { hasText: t })).toBeVisible()
      }
      if (key === 'edge') {
        await expect(page.locator('.sidebar .menu-title', { hasText: '区块链存证' })).toHaveCount(0)
      } else {
        await expect(page.locator('.sidebar .menu-title', { hasText: '区块链存证' })).toBeVisible()
      }
      await page.waitForTimeout(1500)
      g.assertClean(`(${key})`)
    })
  }

  test('按钮级权限：admin 可见篡改演示 / 签名下发，vpp 不可见', async ({ page }) => {
    const g = attachGuards(page)
    await login(page, 'admin')
    await page.goto('/evidence')
    await expect(page.getByRole('button', { name: /篡改演示/ })).toBeVisible()
    await page.goto('/cloud/aggregate')
    await expect(page.getByRole('button', { name: /签名下发/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /模拟越权下发/ })).toBeVisible()
    await page.goto('/identity')
    await page.getByRole('tab', { name: '用户管理' }).click()
    await expect(page.getByRole('button', { name: '新建用户' })).toBeVisible()
    g.assertClean('(admin)')

    await logout(page)
    await login(page, 'vpp')
    await page.goto('/evidence')
    await expect(page.getByRole('button', { name: /篡改演示/ })).toHaveCount(0)
    await page.goto('/cloud/aggregate')
    await expect(page.getByRole('button', { name: /签名下发/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /模拟越权下发/ })).toBeVisible()
    await page.goto('/identity')
    await page.getByRole('tab', { name: '用户管理' }).click()
    await expect(page.getByRole('button', { name: '新建用户' })).toHaveCount(0)
    await page.goto('/permission')
    await page.getByRole('tab', { name: '申请审批' }).click()
    await expect(page.getByRole('button', { name: '通过' })).toHaveCount(0)
    g.assertClean('(vpp)')
  })

  test('subject 访问受限路由 /cloud/aggregate 被守卫拦回首页并提示', async ({ page }) => {
    const g = attachGuards(page)
    await login(page, 'subject')
    await page.goto('/cloud/aggregate')
    await page.waitForURL('**/cloud/topology')
    g.assertClean()
  })

  test('edge 账号落地 /edge/classification 且 sidebar 为边端视角', async ({ page }) => {
    const g = attachGuards(page)
    await login(page, 'edge')
    await expect(page.locator('.sidebar .node-selector')).toBeVisible()
    await expect(page.locator('.sidebar .menu-title', { hasText: '本地感知与分级' })).toBeVisible()
    g.assertClean()
  })

  test('退出登录后访问受保护路由被重定向到 /login?redirect=', async ({ page }) => {
    const g = attachGuards(page)
    await login(page, 'admin')
    await logout(page)
    await page.goto('/evidence')
    await page.waitForURL(/\/login\?redirect=%2Fevidence|\/login\?redirect=\/evidence/)
    // 直接新开（无 token）
    await page.goto('/audit')
    await expect(page).toHaveURL(/\/login/)
    await assertNoHorizontalOverflow(page, '(login)')
    await shot(page, 'login-1366')
    g.assertClean()
  })

  test('错误密码提示且停留登录页', async ({ page }) => {
    const g = attachGuards(page)
    await page.goto('/login')
    const inputs = page.locator('input')
    await inputs.nth(0).fill('admin')
    await inputs.nth(1).fill('wrong')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await expect(page.locator('.el-message, .login-error, .el-form-item__error').first()).toBeVisible()
    await expect(page).toHaveURL(/\/login/)
    // 401 是预期响应（密码错误），其它错误不允许
    expect(g.errors.filter(e => !/401/.test(e))).toEqual([])
  })
})
