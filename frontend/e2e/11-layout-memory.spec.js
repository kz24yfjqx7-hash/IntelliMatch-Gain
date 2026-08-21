import { test, expect } from '@playwright/test'
import { attachGuards, login, assertNoHorizontalOverflow, assertChartsHaveSize, shot } from './helpers.js'

const ROUTES = ['/cloud/topology', '/cloud/aggregate', '/edge/classification', '/edge/risk', '/edge/privacy', '/edge/response', '/audit', '/identity', '/assets', '/permission', '/evidence']

for (const vp of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
  test(`全部路由在 ${vp.width}×${vp.height} 下无横向滚动且图表尺寸非 0`, async ({ page }) => {
    test.setTimeout(180000)
    await page.setViewportSize(vp)
    const g = attachGuards(page)
    await login(page, 'admin')
    for (const r of ROUTES) {
      await page.goto(r)
      await page.waitForTimeout(1800)
      await assertNoHorizontalOverflow(page, `${r} @${vp.width}`)
      await assertChartsHaveSize(page, `${r} @${vp.width}`)
      // 关键区域不重叠：header / sidebar / content / logbar 的矩形互不相交
      const rects = await page.evaluate(() => {
        const q = s => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom] }
        return { header: q('header, .app-header'), sidebar: q('.sidebar'), content: q('.content-area'), logbar: q('.log-bar, .app-log-bar, footer') }
      })
      const overlap = (a, b) => a && b && a[0] < b[2] - 1 && b[0] < a[2] - 1 && a[1] < b[3] - 1 && b[1] < a[3] - 1
      expect(overlap(rects.sidebar, rects.content), `sidebar 与 content 重叠 ${r}`).toBeFalsy()
      expect(overlap(rects.header, rects.content), `header 与 content 重叠 ${r}`).toBeFalsy()
      expect(overlap(rects.logbar, rects.content), `logbar 与 content 重叠 ${r}`).toBeFalsy()
      if (vp.width === 1920 && ['/cloud/topology', '/evidence', '/edge/privacy'].includes(r)) await shot(page, `${r.replace(/\//g, '_').slice(1)}-1920`)
    }
    g.assertClean()
  })
}

test('切换路由 20 次：无重复监听日志、无错误、JS 堆不持续膨胀', async ({ page }) => {
  test.setTimeout(240000)
  const g = attachGuards(page)
  const logCount = { total: 0 }
  page.on('console', () => logCount.total++)
  await login(page, 'admin')
  const cycle = ['/edge/privacy', '/cloud/aggregate', '/evidence', '/audit', '/cloud/topology']
  const heaps = []
  const client = await page.context().newCDPSession(page)
  await client.send('HeapProfiler.enable')
  for (let i = 0; i < 20; i++) {
    await page.goto(cycle[i % cycle.length])
    await page.waitForTimeout(700)
    if (i % 5 === 4) {
      await client.send('HeapProfiler.collectGarbage')
      const { usedSize } = await client.send('Runtime.getHeapUsage')
      heaps.push(usedSize)
    }
  }
  // 等 WS mock 推几轮 node_status，统计 console 条数是否随路由切换次数线性增长（监听器泄漏会放大）
  const before = logCount.total
  await page.waitForTimeout(11000)
  const perTick = logCount.total - before
  expect(perTick, 'WS 推送期间 console 输出异常增多（疑似监听器未注销）').toBeLessThan(40)
  // 堆：最后一次不应超过第一次的 2.5 倍
  expect(heaps.at(-1), `heap ${heaps.map(h => (h / 1e6).toFixed(1)).join(' → ')} MB`).toBeLessThan(heaps[0] * 2.5)
  g.assertClean()
})
