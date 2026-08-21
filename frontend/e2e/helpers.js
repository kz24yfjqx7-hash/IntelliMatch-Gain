// E2E 公共工具：登录、错误收集、断言"页面干净"
import { expect } from '@playwright/test'

export const ACCOUNTS = {
  admin: { username: 'admin', password: 'admin123', role: '系统管理员', home: '/cloud/topology' },
  grid: { username: 'grid', password: 'grid123', role: '电网调度员', home: '/cloud/topology' },
  vpp: { username: 'vpp', password: 'vpp123', role: '虚拟电厂运营商', home: '/cloud/topology' },
  subject: { username: 'subject', password: 'subject123', role: '能源主体', home: '/cloud/topology' },
  regulator: { username: 'regulator', password: 'reg123', role: '监管方', home: '/cloud/topology' },
  edge: { username: 'edge', password: 'edge123', role: '边缘节点', home: '/edge/classification' }
}

// 已知可忽略的噪音（第三方库 / 开发期告警），其余 console.error 均视为失败
const IGNORE_PATTERNS = [
  /\[MSW\]/i,
  /Download the Vue Devtools/i,
  /\[Vue warn\]: .*ResizeObserver/i,
  /ResizeObserver loop/i,
  /favicon\.ico/i
]

/**
 * 给 page 挂上错误收集器：console.error、pageerror、>=400 的资源请求。
 * 返回 { errors, reset, assertClean }
 */
export function attachGuards(page) {
  const errors = []
  page.on('console', msg => {
    if (msg.type() !== 'error') return
    const text = msg.text()
    if (IGNORE_PATTERNS.some(r => r.test(text))) return
    errors.push(`[console.error] ${text.slice(0, 500)}`)
  })
  page.on('pageerror', err => errors.push(`[pageerror] ${err.message}`))
  page.on('response', res => {
    if (res.status() >= 400 && !IGNORE_PATTERNS.some(r => r.test(res.url()))) {
      errors.push(`[http ${res.status()}] ${res.url()}`)
    }
  })
  page.on('requestfailed', req => {
    const f = req.failure()?.errorText || ''
    // 页面关闭导致的 abort 不算
    if (/net::ERR_ABORTED/.test(f)) return
    errors.push(`[requestfailed ${f}] ${req.url()}`)
  })
  return {
    errors,
    reset: () => errors.splice(0, errors.length),
    assertClean: (label = '') => expect(errors, `页面应无 console.error / 未捕获异常 / 4xx 资源 ${label}`).toEqual([])
  }
}

/** 通过登录页 UI 登录（SPA 内，不刷新，保证 MSW 内存状态延续） */
export async function login(page, key, { viaUi = true } = {}) {
  const acc = ACCOUNTS[key]
  if (!page.url().includes('/login')) {
    await page.goto('/login')
  }
  await page.waitForSelector('input')
  const inputs = page.locator('.login-card input, input')
  await inputs.nth(0).fill(acc.username)
  await inputs.nth(1).fill(acc.password)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 15000 })
  await expect(page.locator('.user-name')).toHaveText(new RegExp(acc.username + '|' + acc.role))
  return acc
}

/**
 * SPA 内导航（不刷新页面）。MSW 的内存数据库随页面刷新重置，
 * 跨角色 / 跨页面延续状态的用例必须用它而不是 page.goto。
 */
export async function nav(page, path) {
  await page.evaluate(p => {
    history.pushState({}, '', p)
    dispatchEvent(new PopStateEvent('popstate', { state: {} }))
  }, path)
  await page.waitForURL(url => url.pathname === path, { timeout: 15000 })
  await page.waitForTimeout(300)
}

/** 通过右上角下拉退出登录（SPA 内） */
export async function logout(page) {
  await page.locator('.user-box').click()
  await page.getByText('退出登录').click()
  await page.waitForURL(/\/login/, { timeout: 15000 })
}

/** 同一页面切换账号（不刷新） */
export async function switchUser(page, key) {
  await logout(page)
  return login(page, key)
}

/** 等待 Element Plus 的 message 出现并返回文本 */
export async function waitMessage(page, pattern, timeout = 10000) {
  const loc = page.locator('.el-message').filter({ hasText: pattern })
  await expect(loc.first()).toBeVisible({ timeout })
  return loc.first().innerText()
}

/** 选择 el-select 下拉中的一项 */
export async function pickSelect(page, selectLocator, optionText) {
  await selectLocator.click()
  const opt = page.locator('.el-select-dropdown:visible .el-select-dropdown__item').filter({ hasText: optionText }).first()
  await opt.click()
}

/** 断言横向不溢出：document 与 .content-area 均无横向滚动条 */
export async function assertNoHorizontalOverflow(page, label = '') {
  const m = await page.evaluate(() => {
    const de = document.documentElement
    const ca = document.querySelector('.content-area')
    return {
      doc: [de.scrollWidth, de.clientWidth],
      content: ca ? [ca.scrollWidth, ca.clientWidth] : null
    }
  })
  expect(m.doc[0], `document 横向溢出 ${label}`).toBeLessThanOrEqual(m.doc[1] + 1)
  if (m.content) expect(m.content[0], `.content-area 横向溢出 ${label}`).toBeLessThanOrEqual(m.content[1] + 1)
}

/** 断言页面内所有 ECharts 容器尺寸非 0 */
export async function assertChartsHaveSize(page, label = '') {
  const sizes = await page.evaluate(() =>
    Array.from(document.querySelectorAll('[_echarts_instance_]')).map(el => {
      const r = el.getBoundingClientRect()
      return [Math.round(r.width), Math.round(r.height)]
    })
  )
  for (const [w, h] of sizes) {
    expect(w, `ECharts 容器宽度为 0 ${label}`).toBeGreaterThan(0)
    expect(h, `ECharts 容器高度为 0 ${label}`).toBeGreaterThan(0)
  }
  return sizes.length
}

export const SHOT_DIR = 'e2e/screenshots'
export async function shot(page, name) {
  // jpeg 控制体积：全套 ≤ 20 张、总大小 < 5MB
  await page.screenshot({ path: `${SHOT_DIR}/${name}.jpg`, fullPage: false, type: 'jpeg', quality: 55 })
}
