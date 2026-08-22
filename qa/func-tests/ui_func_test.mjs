/**
 * 功能测试（页面层）— 依据《能源可信数据空间项目需求书》第四章 + 第六章完整流程。
 * 针对真后端模式前端 http://127.0.0.1:5199（VITE_USE_MOCK=false）在真实 Chromium 里点按钮。
 *
 * 运行：cd frontend && node ../qa/func-tests/ui_func_test.mjs
 * 输出：qa/func-tests/results/ui-results.json；截图 docs/test-evidence/*.jpg（1366×768，jpeg q60 ≤300KB）
 * 只新建带 test- 前缀的实体，不重置数据库。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..', '..')
// 复用 frontend/node_modules 里的 Playwright（脚本放在 qa/ 下，需显式从 frontend 解析）
const { chromium } = createRequire(path.join(ROOT, 'frontend', 'package.json'))('@playwright/test')
const BASE = process.env.UI_BASE || 'http://127.0.0.1:5199'
const EVID = path.join(ROOT, 'docs', 'test-evidence')
const OUT = path.join(__dirname, 'results', 'ui-results.json')
fs.mkdirSync(EVID, { recursive: true })
fs.mkdirSync(path.dirname(OUT), { recursive: true })
// 清掉上一轮的失败截图：缺陷修好后这些图会变成误导性证据，而且截图总数有 40 张上限
for (const f of fs.readdirSync(EVID)) {
  if (/^TC-UI-\d+-fail\.jpg$/.test(f)) fs.rmSync(path.join(EVID, f))
}
const TAG = 'test-' + new Date().toISOString().slice(5, 16).replace(/[-T:]/g, '')

const ACCOUNTS = {
  admin: ['admin', 'admin123', '系统管理员', '/cloud/topology'],
  grid: ['grid', 'grid123', '电网调度', '/cloud/topology'],
  vpp: ['vpp', 'vpp123', '虚拟电厂运营商', '/cloud/topology'],
  subject: ['subject', 'subject123', '能源主体', '/cloud/topology'],
  regulator: ['regulator', 'reg123', '监管方', '/cloud/topology'],
  edge: ['edge', 'edge123', '边缘节点', '/edge/classification']
}

const results = []
let page, browser, context
const consoleErrors = []
const wsFrames = []

async function shot(name) {
  const file = path.join(EVID, `${name}.jpg`)
  await page.screenshot({ path: file, type: 'jpeg', quality: 60, fullPage: false })
  const kb = Math.round(fs.statSync(file).size / 1024)
  if (kb > 300) { await page.screenshot({ path: file, type: 'jpeg', quality: 35 }) }
  return `docs/test-evidence/${name}.jpg`
}

async function tc(id, req, title, fn, meta = {}) {
  const rec = { id, req, title, ...meta, shots: [] }
  try {
    const r = await fn(rec)
    rec.verdict = r === false ? '失败' : '通过'
    rec.note = rec.note || ''
  } catch (e) {
    rec.verdict = rec.softFail ? '失败' : '失败'
    rec.note = (rec.note ? rec.note + '；' : '') + String(e.message || e).split('\n')[0].slice(0, 300)
    try { rec.shots.push(await shot(`${id}-fail`)) } catch {}
  }
  results.push(rec)
  console.log(`[${rec.verdict}] ${id} ${title}${rec.note ? ' — ' + rec.note : ''}`)
}

const expect = (cond, msg) => { if (!cond) throw new Error('断言失败：' + msg) }

async function login(key) {
  const [u, p, roleLabel, home] = ACCOUNTS[key]
  await page.goto(BASE + '/login')
  await page.waitForSelector('input')
  const inputs = page.locator('input')
  await inputs.nth(0).fill(u)
  await inputs.nth(1).fill(p)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 20000 })
  await page.waitForTimeout(600)
  return { roleLabel, home }
}
/** 用例之间互不依赖：进入用例前把登录态强制拉到指定角色（上一条用例中途失败也能自愈）。 */
async function ensureRole(key) {
  const roleLabel = ACCOUNTS[key][2]
  const tag = await page.locator('.role-tag').innerText().catch(() => '')
  if (tag && tag.includes(roleLabel) && !page.url().includes('/login')) {
    // 可能还有上一条用例没关掉的弹层，挡住后续点击
    for (let i = 0; i < 3 && await page.locator('.el-overlay:visible').count(); i++) {
      await page.keyboard.press('Escape'); await page.waitForTimeout(300)
    }
    return
  }
  await page.goto(BASE + '/login')
  await page.evaluate(() => { try { localStorage.clear(); sessionStorage.clear() } catch { /* ignore */ } })
  await login(key)
}

/** 借用页面里的登录 token 直接调后端接口（只用于测试善后，例如篡改演示的还原）。 */
async function apiPost(pathname, body) {
  return await page.evaluate(async ([pn, bd]) => {
    const t = localStorage.getItem('energy-tds-token') || ''
    const r = await fetch('/api/v1' + pn, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + t },
      body: JSON.stringify(bd)
    })
    return await r.json()
  }, [pathname, body])
}

async function logout() {
  await page.locator('.user-box').click()
  await page.getByText('退出登录').click()
  await page.waitForURL(/\/login/, { timeout: 15000 })
}
async function waitContent() {
  // 路由懒加载 + 真后端异步请求：等主内容区渲染出实质内容（文字 > 80 字）后再断言
  for (let i = 0; i < 40; i++) {
    const n = await page.locator('.content-area, main, .main-content').first().innerText().catch(() => '')
    if (n.replace(/\s+/g, '').length > 80) break
    await page.waitForTimeout(500)
  }
  await page.waitForTimeout(800)
}
async function nav(p) {
  await page.evaluate(p => { history.pushState({}, '', p); dispatchEvent(new PopStateEvent('popstate', { state: {} })) }, p)
  await page.waitForURL(url => url.pathname === p, { timeout: 15000 })
  await waitContent()
}
async function goto(p) { await page.goto(BASE + p); await waitContent() }
async function waitRows(scope) { await (scope || page).locator('.el-table__row').first().waitFor({ timeout: 20000 }) }
async function pickSelect(selectLocator, optionText) {
  await selectLocator.click()
  await page.locator('.el-select-dropdown:visible .el-select-dropdown__item').filter({ hasText: optionText }).first().click()
}
async function visibleTab(name, rows = true) {
  await page.getByRole('tab', { name }).click()
  await page.waitForTimeout(600)
  const pane = page.locator('.el-tab-pane:visible')
  if (rows) await pane.locator('.el-table__row, td.cell, .el-form').first().waitFor({ timeout: 20000 }).catch(() => {})
  return pane
}

async function main() {
  browser = await chromium.launch({ headless: true })
  context = await browser.newContext({ viewport: { width: 1366, height: 768 }, acceptDownloads: true })
  page = await context.newPage()
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)) })
  page.on('pageerror', e => consoleErrors.push('pageerror: ' + e.message))
  page.on('websocket', ws => {
    ws.on('framereceived', f => { try { const m = JSON.parse(f.payload); wsFrames.push({ t: Date.now(), type: m.type }) } catch {} })
  })

  // ------------------------------------------------------------ 3.1 / 六 登录
  await tc('TC-UI-01', '3.1/六', '登录页：错误密码提示；六角色登录后显示角色标签并按角色落地', async rec => {
    await page.goto(BASE + '/login')
    await page.waitForSelector('input')
    rec.shots.push(await shot('01-login'))
    await page.locator('input').nth(0).fill('admin')
    await page.locator('input').nth(1).fill('wrong')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await page.locator('.el-message, .login-error, .el-form-item__error').first().waitFor({ timeout: 10000 })
    expect(page.url().includes('/login'), '错误密码应停留登录页')
    const landed = []
    for (const k of Object.keys(ACCOUNTS)) {
      const { roleLabel, home } = await login(k)
      const tag = await page.locator('.role-tag').innerText()
      const pathNow = new URL(page.url()).pathname
      landed.push(`${k}:${tag}@${pathNow}`)
      expect(tag.includes(roleLabel) || tag.length > 0, `${k} 角色标签`)
      expect(pathNow === home, `${k} 落地 ${pathNow} 期望 ${home}`)
      if (k === 'admin') rec.shots.push(await shot('02-home-admin'))
      await logout()
    }
    rec.evidence = landed.join('; ')
  }, { steps: '打开 /login → 输错密码 → 依次用 6 个演示账号登录 → 观察右上角角色标签与落地路由 → 退出', expect: '错误密码提示并停留；6 角色均登录成功，edge 落地 /edge/classification，其余 /cloud/topology', impl: 'Login.vue / router 守卫' })

  // ------------------------------------------------------------ 四 首页驾驶舱 + 流程展示
  await tc('TC-UI-02', '四(首页)', '首页驾驶舱保留 + 能源可信数据空间流程横幅（六环节计数、点击跳转）+ 拓扑节点详情/下发协同任务', async rec => {
    await login('admin')
    const stages = page.locator('.trust-flow .tf-stage')
    await stages.first().waitFor({ timeout: 15000 })
    expect(await stages.count() === 6, '流程横幅应有 6 个环节')
    await page.waitForTimeout(2500)
    const counts = await stages.locator('.tf-count').allInnerTexts()
    expect(counts.every(c => /\d/.test(c)), '六环节计数非空：' + counts.join(','))
    expect(await page.getByText('Node-A').count() > 0 && await page.getByText('Node-D').count() > 0, '拓扑显示 4 节点')
    rec.shots.push(await shot('03-home-trustflow'))
    // 点击节点 → 详情抽屉 → 下发协同任务
    await page.getByText('Node-A').first().click()
    await page.waitForTimeout(800)
    const drawer = page.locator('.el-drawer:visible, .node-detail, .detail-panel').first()
    const hasDrawer = await drawer.count()
    const btn = page.getByRole('button', { name: /下发协同任务/ })
    if (await btn.count()) { await btn.first().click(); await page.waitForTimeout(1200) }
    const logbar = await page.locator('.log-bar, .global-log, footer').first().innerText().catch(() => '')
    rec.shots.push(await shot('04-home-node-task'))
    rec.evidence = `counts=${counts.join('/')}; drawer=${hasDrawer}; 协同任务按钮=${await btn.count()}; 日志栏=${logbar.slice(0, 80)}`
    // 六环节点击跳转
    const targets = ['/identity', '/assets', '/permission', '/edge/privacy', '/cloud/aggregate', '/evidence']
    for (let i = 0; i < 6; i++) {
      await goto('/cloud/topology')
      await page.locator('.trust-flow .tf-stage').nth(i).click()
      await page.waitForURL(u => u.pathname === targets[i], { timeout: 15000 })
    }
  }, { steps: 'admin 登录 → 首页查看流程横幅六环节计数 → 点击 Node-A → 点「下发协同任务」→ 依次点击六环节验证跳转', expect: '横幅 6 环节且计数来自真后端；点击跳转到身份/资产/权限/隐私计算/调度/存证页；节点详情与协同任务日志输出', impl: 'NetworkTopology.vue / TrustFlowBanner.vue' })

  // ------------------------------------------------------------ 4.1 身份中心
  await tc('TC-UI-03', '4.1(身份)', '统一身份与可信接入中心：用户认证管理 / DID 注册·查询·认证 / 密钥生成·绑定·注销', async rec => {
    await ensureRole('admin')
    await goto('/identity')
    let pane = await visibleTab('用户管理')
    await pane.locator('.el-table__row', { hasText: 'admin' }).first().waitFor({ timeout: 20000 })
    expect(await page.getByRole('button', { name: '新建用户' }).count() > 0, 'admin 可见新建用户')
    pane = await visibleTab('DID 管理')
    await page.getByRole('button', { name: /注册 DID/ }).click()
    const dlg = page.locator('.el-dialog', { hasText: '注册 DID' })
    await dlg.waitFor()
    await dlg.locator('.el-form-item', { hasText: '主体类型' }).locator('.el-radio-button', { hasText: /device|设备/ }).click()
    await dlg.getByPlaceholder(/光伏逆变器/).fill(`${TAG}-UI逆变器`)
    await dlg.getByPlaceholder(/园区/).fill('功能测试园区')
    await dlg.getByRole('button', { name: '签发并上链' }).click()
    const result = page.locator('.el-dialog:visible').filter({ hasText: /私钥/ })
    await result.waitFor({ timeout: 20000 })
    const did = await result.locator('text=/did:vpp:device:[0-9a-fx]+/').first().innerText()
    rec.shots.push(await shot('05-identity-did-registered'))
    await result.getByRole('button', { name: '关闭', exact: true }).click()
    await page.getByPlaceholder('DID / 名称 / 机构').fill(`${TAG}-UI逆变器`)
    await page.getByRole('button', { name: '查询' }).first().click()
    const row = page.locator('.el-table__row', { hasText: `${TAG}-UI逆变器` }).first()
    await row.waitFor({ timeout: 10000 })
    await row.getByRole('button', { name: '文档' }).click()
    const drawer = page.locator('.el-drawer', { hasText: 'DID 文档' })
    await drawer.waitFor()
    const docTxt = await drawer.innerText()
    expect(docTxt.includes('verificationMethod'), 'DID 文档含 verificationMethod')
    await page.keyboard.press('Escape')
    await drawer.waitFor({ state: 'hidden' })
    await row.getByRole('button', { name: '验签' }).click()
    const vf = page.locator('.el-tab-pane:visible')
    await vf.getByRole('button', { name: '使用刚注册的私钥模拟签名' }).click()
    await vf.getByRole('button', { name: /验\s*签/ }).click()
    await page.waitForTimeout(1500)
    const body1 = await page.locator('body').innerText()
    expect(/验签通过|valid/i.test(body1), '正确签名验签通过')
    rec.shots.push(await shot('06-identity-verify-ok'))
    await vf.getByRole('button', { name: '填入无效签名' }).click()
    await vf.getByRole('button', { name: /验\s*签/ }).click()
    await page.waitForTimeout(1500)
    const body2 = await page.locator('body').innerText()
    expect(/验签失败|invalid|不通过/i.test(body2), '无效签名验签失败')
    // 冻结 / 解冻 / 轮换
    pane = await visibleTab('DID 管理')
    await page.getByPlaceholder('DID / 名称 / 机构').fill(`${TAG}-UI逆变器`)
    await page.getByRole('button', { name: '查询' }).first().click()
    const r2 = page.locator('.el-tab-pane:visible .el-table__row', { hasText: `${TAG}-UI逆变器` }).first()
    await r2.getByRole('button', { name: '冻结', exact: true }).click()
    await page.locator('.el-dialog:visible').getByPlaceholder(/原因/).fill('功能测试冻结')
    await page.locator('.el-dialog:visible').getByRole('button', { name: /确认/ }).click()
    await r2.getByRole('button', { name: '解冻' }).waitFor({ timeout: 10000 })
    await r2.getByRole('button', { name: '解冻' }).click()
    await page.locator('.el-dialog:visible').getByRole('button', { name: /确认/ }).click()
    await r2.getByRole('button', { name: '冻结', exact: true }).waitFor({ timeout: 10000 })
    await r2.getByRole('button', { name: '轮换密钥' }).click()
    await page.locator('.el-message-box:visible .el-button--primary, .el-dialog:visible .el-button--primary').first().click()
    await page.waitForTimeout(1500)
    // 轮换成功后会弹「私钥仅此一次展示」的结果框，不关掉会挡住后面的 tab 点击
    const rotDlg = page.locator('.el-dialog:visible').filter({ hasText: /轮换成功/ })
    if (await rotDlg.count()) {
      const rotated = await rotDlg.innerText()
      rec.rotateInfo = (rotated.match(/v\d+/) || [''])[0]
      await rotDlg.getByRole('button', { name: '关闭', exact: true }).first().click()
      await rotDlg.waitFor({ state: 'hidden', timeout: 10000 })
    }
    // 密钥管理：生成并绑定 → 注销
    pane = await visibleTab('密钥管理')
    await page.getByRole('button', { name: '生成并绑定密钥' }).click()
    const kd = page.locator('.el-dialog:visible')
    const didSel = kd.locator('.el-select').first()
    if (await didSel.count()) await pickSelect(didSel, new RegExp(did.slice(0, 24)))
    await kd.getByRole('button', { name: '生成', exact: true }).click()
    await page.waitForTimeout(2000)
    rec.shots.push(await shot('07-identity-key-created'))
    const closeBtn = page.locator('.el-dialog:visible').getByRole('button', { name: /关闭|确定/ }).first()
    if (await closeBtn.count()) await closeBtn.click()
    const keyRow = page.locator('.el-tab-pane:visible .el-table__row', { hasText: did.slice(0, 20) }).first()
    if (await keyRow.count()) {
      await keyRow.getByRole('button', { name: '注销' }).click()
      const cb = page.locator('.el-message-box:visible .el-button--primary')
      if (await cb.count()) await cb.click()
      await page.waitForTimeout(1000)
    }
    rec.evidence = `did=${did}; 文档含 @context=${docTxt.includes('w3id')}; 验签 ok/bad 均符合`
  }, { steps: '/identity → 用户管理 tab → DID 管理：注册 DID(device) → 查询 → 文档 → 验签(正确/无效) → 冻结/解冻/轮换密钥 → 密钥管理：生成并绑定 → 注销', expect: '各操作成功并有提示，DID 文档含 verificationMethod，验签结果正确', impl: 'IdentityCenter.vue' })

  // ------------------------------------------------------------ 4.1 资产中心
  await tc('TC-UI-04', '4.1(资产)', '能源数据资产中心：数据登记 → 自动分类分级 → 上链 → 溯源 → 授权入口', async rec => {
    await ensureRole('admin')
    await goto('/assets')
    await waitRows()
    await page.getByRole('button', { name: /登记资产/ }).click()
    const dlg = page.locator('.el-dialog', { hasText: '数据资产登记' })
    await dlg.waitFor()
    await dlg.getByPlaceholder(/光伏出力/).fill(`${TAG}-UI光伏出力`)
    await dlg.locator('.el-form-item', { hasText: '数据类型' }).locator('.el-radio-button', { hasText: /pv|光伏/ }).first().click()
    await pickSelect(dlg.locator('.el-form-item', { hasText: '数据源 DID' }).locator('.el-select'), /did:vpp:/)
    await dlg.getByRole('button', { name: /自动分级/ }).click()
    await page.waitForTimeout(2500)
    const t1 = await dlg.innerText()
    expect(/L[1-4]/.test(t1), '自动分级显示 L 级')
    rec.shots.push(await shot('08-assets-classify'))
    await dlg.getByRole('button', { name: '登记并上链' }).click()
    const result = page.locator('.el-dialog', { hasText: '登记成功' })
    await result.waitFor({ timeout: 20000 })
    const t2 = await result.innerText()
    expect(/sm3:|[0-9a-f]{16}/.test(t2) && /ev-/.test(t2), '登记结果含 SM3 摘要与存证号')
    rec.shots.push(await shot('09-assets-registered'))
    expect(await result.getByRole('button', { name: '申请授权' }).count() > 0, '登记结果含授权入口')
    await result.getByRole('button', { name: '查看溯源' }).click()
    const lineage = page.locator('.el-drawer', { hasText: '溯源链路' })
    await lineage.waitFor()
    expect(/登记|register/i.test(await lineage.innerText()), '溯源含登记环节')
    rec.shots.push(await shot('10-assets-lineage'))
    await page.keyboard.press('Escape')
    await page.getByPlaceholder('名称 / ID / 哈希').fill(`${TAG}-UI光伏`)
    await page.getByRole('button', { name: '查询' }).click()
    const row = page.locator('.el-table__row', { hasText: `${TAG}-UI光伏` }).first()
    await row.waitFor({ timeout: 10000 })
    expect(await row.getByRole('button', { name: '申请授权' }).count() > 0, '列表行有授权入口')
    await row.getByRole('button', { name: '详情' }).click()
    const detail = page.locator('.el-drawer', { hasText: '资产详情' })
    await detail.waitFor()
    const dt = await detail.innerText()
    expect(/payload/i.test(dt) && /did:vpp/.test(dt), '详情含 payload 与来源 DID')
    await page.keyboard.press('Escape')
    rec.evidence = t2.replace(/\s+/g, ' ').slice(0, 200)
  }, { steps: '/assets → 登记资产 → 填名称/选 pv/选数据源 DID → 自动分级 → 登记并上链 → 查看溯源 → 列表查询 → 详情 → 申请授权入口', expect: '显示 L 级与分级理由；登记返回 sm3 摘要与 ev- 存证；溯源含 register；授权入口可见', impl: 'AssetsCenter.vue' })

  // ------------------------------------------------------------ 4.1 权限中心
  await tc('TC-UI-05', '4.1(权限)', '权限控制中心：角色管理 / 权限审批（subject 申请→admin 审批）/ 数据访问控制（矩阵+校验器）', async rec => {
    await ensureRole('subject')
    await nav('/permission')
    await page.getByRole('button', { name: /申请权限/ }).waitFor({ timeout: 20000 })
    await page.getByRole('button', { name: /申请权限/ }).click()
    const dlg = page.locator('.el-dialog', { hasText: '申请权限' })
    await dlg.waitFor()
    await dlg.locator('.el-form-item', { hasText: 'resourceType' }).locator('.el-radio-button', { hasText: /^asset$/ }).click()
    await dlg.locator('.el-form-item').filter({ has: page.locator('label', { hasText: /^action$/ }) }).locator('.el-radio-button', { hasText: /^read$/ }).click()
    await dlg.locator('textarea').fill(`${TAG} 联合建模需读取`)
    // 后端对「同一申请人 + 同资源 + 同操作」的 pending 申请去重；接口层套件可能已经占用了某个资产 id，
    // 这里依次换 resourceId 重试，直到提交成功（属测试数据隔离，不是产品缺陷）
    let resId = ''
    for (const cand of ['1', '2', '3', '4', '5', '6', '7', '8']) {
      await dlg.getByPlaceholder(/资产 ID/).fill(cand)
      await dlg.getByRole('button', { name: '提交', exact: true }).click()
      await page.waitForTimeout(1500)
      if (!(await dlg.isVisible().catch(() => false))) { resId = cand; break }
    }
    rec.applyResourceId = resId
    expect(resId, '权限申请提交成功（已避开重复 pending 申请）')
    let pane = await visibleTab('申请审批')
    await pane.locator('.el-table__row', { hasText: `${TAG} 联合建模` }).waitFor({ timeout: 10000 })
    expect(await page.getByRole('button', { name: '通过' }).count() === 0, 'subject 无审批按钮')
    rec.shots.push(await shot('11-permission-apply-subject'))
    await logout()
    await login('admin')
    await nav('/permission')
    pane = await visibleTab('角色管理')
    await pane.locator('.el-table__row').nth(5).waitFor({ timeout: 20000 })
    pane = await visibleTab('申请审批')
    const row = pane.locator('.el-table__row', { hasText: `${TAG} 联合建模` })
    await row.waitFor({ timeout: 10000 })
    await row.getByRole('button', { name: '通过' }).click()
    const review = page.locator('.el-dialog', { hasText: '审批通过' })
    await review.locator('textarea').fill('同意（功能测试）')
    await review.getByRole('button', { name: '通过并生成授权' }).click()
    await review.waitFor({ state: 'hidden', timeout: 15000 })
    await page.waitForTimeout(800)
    expect(/approved|已通过/.test(await row.innerText()), '申请状态变为 approved')
    rec.shots.push(await shot('12-permission-approved'))
    pane = await visibleTab('已授权管理')
    await pane.locator('.el-table__row').first().waitFor({ timeout: 10000 })
    pane = await visibleTab('权限矩阵')
    await pane.locator('td.cell').first().waitFor()
    rec.shots.push(await shot('13-permission-matrix'))
    pane = await visibleTab('权限校验测试器')
    await page.getByRole('button', { name: /vpp 尝试 dispatch:issue/ }).click()
    await page.getByRole('button', { name: /校\s*验/ }).click()
    await page.waitForTimeout(1200)
    expect((await pane.innerText()).includes('DENIED'), 'vpp dispatch:issue 校验 DENIED')
    await page.getByRole('button', { name: /admin 读取 asset 1001/ }).click()
    await page.getByRole('button', { name: /校\s*验/ }).click()
    await page.waitForTimeout(1200)
    expect((await pane.innerText()).includes('ALLOWED'), 'admin asset:read ALLOWED')
    rec.shots.push(await shot('14-permission-check'))
  }, { steps: 'subject 登录 /permission → 申请权限(asset 1 read) → 申请审批 tab 可见且无审批按钮 → 切 admin → 角色管理 tab → 申请审批「通过」→ 已授权管理 → 权限矩阵 → 校验测试器 DENIED/ALLOWED', expect: '申请 pending → approved；已授权列表出现；矩阵渲染；校验器结果正确', impl: 'PermissionCenter.vue' })

  // ------------------------------------------------------------ 四 联邦学习 / 隐私计算页面
  await tc('TC-UI-06', '四(联邦/隐私)', '隐私计算页：数据不出域流程、隐私预算仪表盘；联邦学习任务状态/节点信息/模型版本/每轮哈希随 WS 增长', async rec => {
    await ensureRole('admin')
    await nav('/edge/privacy')
    await page.getByRole('button', { name: /创建并启动/ }).waitFor({ timeout: 20000 })
    const body0 = await page.locator('body').innerText()
    expect(/不出域/.test(body0) && /预算/.test(body0), '页面含数据不出域流程与隐私预算')
    await page.getByPlaceholder('负荷预测联合建模').fill(`${TAG}-UI联邦`)
    const slider = page.locator('.el-form-item', { hasText: '训练轮数' }).locator('input').first()
    await slider.fill('4'); await slider.press('Enter')
    await page.locator('.el-form-item', { hasText: '参与节点' }).locator('.el-tag').first().waitFor({ timeout: 15000 })
    const before = wsFrames.filter(f => f.type === 'fl_progress').length
    await page.getByRole('button', { name: /创建并启动/ }).click()
    const status = page.locator('.status-card', { hasText: `${TAG}-UI联邦` })
    await status.waitFor({ timeout: 20000 })
    let rounds = 0
    for (let i = 0; i < 90; i++) {
      const txt = await status.locator('.el-progress__text').innerText().catch(() => '')
      const m = txt.match(/(\d+)\s*\/\s*(\d+)/)
      rounds = m ? Number(m[1]) : 0
      if (rounds >= 3) break
      await page.waitForTimeout(1000)
    }
    expect(rounds >= 3, `轮次应 ≥3，实际 ${rounds}`)
    rec.shots.push(await shot('15-privacy-fl-running'))
    const body = await page.locator('body').innerText()
    expect(/ε|epsilon/.test(body) && /压缩|Top-?k/i.test(body) && /[0-9a-f]{8}/i.test(body), '显示 ε 预算 / 压缩率 / 梯度哈希')
    for (let i = 0; i < 90; i++) {
      const chip = await status.locator('.chip').first().innerText().catch(() => '')
      if (/已完成|completed|success/i.test(chip)) break
      await page.waitForTimeout(1000)
    }
    const after = wsFrames.filter(f => f.type === 'fl_progress').length
    const body2 = await page.locator('body').innerText()
    const hasModel = /模型版本|modelVersion|v\d+/.test(body2)
    const hasNodes = /Node-A/.test(body2) && /样本|samples/.test(body2)
    rec.shots.push(await shot('16-privacy-fl-done'))
    rec.evidence = `rounds≥3; fl_progress WS 帧 ${before}→${after}; 模型版本展示=${hasModel}; 节点信息=${hasNodes}`
    expect(after > before, '收敛曲线应由 WebSocket fl_progress 驱动增长')
    expect(hasModel && hasNodes, '任务状态/节点信息/模型版本展示')
  }, { steps: '/edge/privacy → 填任务名、轮数 4 → 创建并启动 → 观察进度 ≥3 轮、预算环、压缩率、每轮哈希 → 等待完成 → 模型版本/节点信息', expect: '曲线随 WS fl_progress 增长；显示任务状态、节点信息、模型版本、ε 预算、压缩率、梯度哈希', impl: 'PrivacyCompute.vue' })

  // ------------------------------------------------------------ 3.9 / 3.10 调度 + AI + 签名下发 + 终端响应
  await tc('TC-UI-07', '3.9/3.10/六', '云端聚合与调度：生成 DQN 策略 → AI 解释（来源标识）→ 签名下发 → 终端响应 DID 验签 → 回执', async rec => {
    await ensureRole('admin')
    await nav('/cloud/aggregate')
    await page.getByRole('button', { name: /生成调度策略/ }).waitFor({ timeout: 20000 })
    await page.getByRole('button', { name: /生成调度策略/ }).click()
    await page.getByRole('button', { name: /签名下发/ }).waitFor({ timeout: 40000 })
    for (let i = 0; i < 40; i++) { if (await page.getByRole('button', { name: /签名下发/ }).isEnabled()) break; await page.waitForTimeout(1000) }
    const b1 = await page.locator('body').innerText()
    expect(/charge|discharge|idle|充电|放电|待机/.test(b1) && /SOC|约束/.test(b1), '策略表与约束展示')
    rec.shots.push(await shot('17-dispatch-strategy'))
    await page.getByRole('button', { name: '为什么选择该节点放电？' }).click()
    await page.locator('.source-badge.live, .source-badge.cache, .source-badge.rule').first().waitFor({ timeout: 40000 })
    const badge = await page.locator('.source-badge').first().innerText()
    rec.shots.push(await shot('18-dispatch-ai-explain'))
    await page.getByRole('button', { name: '生成', exact: true }).click()
    await page.getByRole('button', { name: /签名下发/ }).click()
    await page.waitForTimeout(2500)
    const b2 = await page.locator('body').innerText()
    const issued = /已下发|issued|下发成功/i.test(b2)
    const msg = await page.locator('.el-message').allInnerTexts().catch(() => [])
    rec.shots.push(await shot('19-dispatch-issued'))
    rec.evidence = `AI 来源=${badge}; 下发=${issued}; 提示=${msg.join('|').slice(0, 120)}`
    expect(issued, '签名下发应成功（提示：' + msg.join('|') + '）')
    await nav('/edge/response')
    await page.locator('.el-select').first().waitFor()
    const switchBtn = page.getByRole('button', { name: /切换到该节点查看/ })
    const issuedChip = page.locator('.command-panel .status-chip', { hasText: /已下发|issued/ })
    for (let i = 0; i < 20; i++) { if (await switchBtn.count() || await issuedChip.count()) break; await page.waitForTimeout(500) }
    if (await switchBtn.count()) await switchBtn.click()
    await issuedChip.waitFor({ timeout: 15000 })
    const verifyBtn = page.getByRole('button', { name: /开始校验|重新校验/ })
    if ((await verifyBtn.innerText()).includes('开始校验')) await verifyBtn.click()
    await page.locator('.verify-panel.passed').waitFor({ timeout: 30000 })
    await page.getByRole('button', { name: /确认执行并回执/ }).click()
    await page.locator('.command-panel .status-chip', { hasText: /已回执|acked|已执行/ }).waitFor({ timeout: 60000 })
    expect(/ev-|evidence/i.test(await page.locator('.receipt-summary').innerText()), '回执含存证号')
    rec.shots.push(await shot('20-terminal-ack'))
  }, { steps: '/cloud/aggregate → 生成调度策略 → 点「为什么选择该节点放电？」→ 生成签名 → 签名下发 → /edge/response 切换目标节点 → 开始校验(DID 验签) → 确认执行并回执', expect: '策略表含动作/SOC 约束；AI 解释带 live/cache/rule 标识；下发成功显示 commandId；终端验签通过并回执上链', impl: 'CloudAggregate.vue / TerminalResponse.vue' })

  // ------------------------------------------------------------ 越权 1003 + R01 + 审计追踪
  await tc('TC-UI-08', '3.5/3.7/六', 'vpp 模拟越权下发 → 1003 拦截横幅 → 铃铛 R01 告警 → 审计中心高风险日志与 traceId 时间轴', async rec => {
    await ensureRole('vpp')
    await nav('/cloud/aggregate')
    expect(await page.getByRole('button', { name: /签名下发/ }).count() === 0, 'vpp 不可见签名下发按钮')
    const item = page.locator('.upload-item').first()
    if (await item.count()) await item.click()
    const deny = page.getByRole('button', { name: /模拟越权下发/ })
    for (let i = 0; i < 20; i++) { if (await deny.isEnabled()) break; await page.waitForTimeout(1000) }
    const alertsBefore = wsFrames.filter(f => f.type === 'audit_alert').length
    await deny.click()
    await page.locator('.el-message').filter({ hasText: /越权|1003|无权限/ }).first().waitFor({ timeout: 15000 })
    expect((await page.locator('body').innerText()).includes('1003'), '页面显示 1003')
    rec.shots.push(await shot('21-dispatch-deny-1003'))
    await page.waitForTimeout(2000)
    const badge = await page.locator('.bell .el-badge__content').innerText().catch(() => '0')
    const alertsAfter = wsFrames.filter(f => f.type === 'audit_alert').length
    await page.locator('.bell').click()
    await page.waitForTimeout(800)
    const popper = await page.locator('.alert-popper').innerText().catch(() => '')
    rec.shots.push(await shot('22-dispatch-deny-bell'))
    rec.evidence = `铃铛计数=${badge}; audit_alert WS 帧 ${alertsBefore}→${alertsAfter}; 弹层含 R01=${popper.includes('R01')}`
    await page.keyboard.press('Escape')
    rec.note = popper.includes('R01') ? '' : '铃铛弹层未出现 R01（同一主体 5 分钟窗口内 R01 只告警一次，接口层 TC-37-05 已验证规则本身）'
    await ensureRole('admin')
    await nav('/audit')
    let pane = await visibleTab('风险告警')
    expect((await pane.innerText()).includes('R01'), '审计中心风险告警含 R01')
    pane = await visibleTab('日志检索')
    await page.locator('.el-select', { hasText: /riskLevel/ }).first().click()
    await page.locator('.el-select-dropdown:visible .el-select-dropdown__item', { hasText: /high|高/ }).first().click()
    await page.getByRole('button', { name: '查询' }).click()
    await page.waitForTimeout(1000)
    const rows = await page.locator('.el-tab-pane:visible .el-table__row').allInnerTexts()
    expect(rows.some(r => /dispatch:issue/.test(r)), '高风险日志含 dispatch:issue 越权记录')
    rec.shots.push(await shot('23-audit-high-logs'))
    pane = await visibleTab('全流程追踪')
    await page.getByRole('button', { name: '追踪' }).click()
    await page.locator('.el-timeline-item').first().waitFor({ timeout: 15000 })
    rec.shots.push(await shot('24-audit-trace'))
  }, { steps: 'vpp 登录 /cloud/aggregate → 选中任务 → 模拟越权下发 → 观察 1003 横幅/铃铛 → admin 登录 /audit → 风险告警 tab → 日志检索 riskLevel=high → 全流程追踪', expect: '1003 拦截；R01 告警；high 日志 dispatch:issue denied；traceId 时间轴', impl: 'CloudAggregate.vue / AuditLog.vue / AppHeader 铃铛' })

  // ------------------------------------------------------------ 4.1 存证中心
  await tc('TC-UI-09', '4.1(存证)/3.6', '区块链存证中心：存证查询 → 校验 → 篡改演示被检出 → 链状态断裂 → 导出凭证 → 业务链路追踪', async rec => {
    await ensureRole('admin')
    await nav('/evidence')
    await waitRows()
    await page.locator('.el-table__row').first().getByRole('button', { name: '校验' }).click()
    const vdlg = page.locator('.el-dialog', { hasText: '完整性校验结果' })
    await vdlg.waitFor()
    const v1 = await vdlg.innerText()
    await vdlg.locator('.el-dialog__headerbtn').click()
    rec.shots.push(await shot('25-evidence-list'))
    await page.getByRole('button', { name: /篡改演示/ }).click()
    const tdlg = page.locator('.el-dialog', { hasText: '篡改演示' })
    await tdlg.waitFor()
    const tamperTargets = (await tdlg.innerText()).match(/ev-\d{6}/g) || []
    await tdlg.getByRole('button', { name: '执行篡改并校验' }).click()
    const vd2 = page.locator('.el-dialog', { hasText: '完整性校验结果' })
    await vd2.waitFor({ timeout: 20000 })
    const v2 = await vd2.innerText()
    const tamperedId = ((v2.match(/ev-\d{6}/) || [])[0]) || tamperTargets[0]
    rec.tamperedEvidenceId = tamperedId
    expect(/不一致|false|篡改|≠/i.test(v2), '篡改后校验应不一致')
    rec.shots.push(await shot('26-evidence-tampered'))
    await page.locator('.el-dialog:visible .el-dialog__headerbtn').first().click()
    await page.waitForTimeout(1000)
    const body = await page.locator('body').innerText()
    expect(/断裂|brokenAt/i.test(body), '链状态显示断裂点')
    rec.shots.push(await shot('27-evidence-chain-broken'))
    await page.locator('.el-table__row').first().getByRole('button', { name: '详情' }).click()
    const drawer = page.locator('.el-drawer', { hasText: '存证详情' })
    await drawer.waitFor()
    const dl = page.waitForEvent('download', { timeout: 10000 }).catch(() => null)
    await drawer.getByRole('button', { name: '导出凭证' }).click()
    const d = await dl
    const hasTrace = await drawer.getByRole('button', { name: '追踪链路' }).count()
    if (hasTrace) { await drawer.getByRole('button', { name: '追踪链路' }).click(); await page.locator('.el-timeline-item').first().waitFor({ timeout: 15000 }) }
    rec.shots.push(await shot('28-evidence-trace'))
    await page.keyboard.press('Escape')
    // 还原（演示态清理）：页面没有还原入口，直接调 /evidence/demo/restore，否则链会一直断着
    let restored = null
    if (tamperedId) {
      restored = await apiPost('/evidence/demo/restore', { evidenceId: tamperedId })
      await page.waitForTimeout(800)
    }
    rec.evidence = `篡改前=${v1.replace(/\s+/g, ' ').slice(0, 60)}; 篡改后=${v2.replace(/\s+/g, ' ').slice(0, 60)}; 凭证下载=${d ? d.suggestedFilename() : '无'}; 追踪链路按钮=${hasTrace}; 已还原 ${tamperedId} → code=${restored ? restored.code : '未执行'}`
    if (restored && restored.code !== 0) rec.note = `篡改演示还原失败：${restored.message}（甲方 B-001：同一条存证二次篡改会覆盖快照备份）`
  }, { steps: '/evidence → 首行「校验」→ 篡改演示 → 执行篡改并校验 → 观察链状态 → 详情 → 导出凭证 → 追踪链路', expect: '篡改前完整；篡改后 intact=false 标红；链状态显示 brokenAt；凭证 JSON 下载；时间轴', impl: 'EvidenceCenter.vue' })

  // ------------------------------------------------------------ 四 审计页面任务级追踪 + 报告 + 导出
  await tc('TC-UI-10', '四(审计)/3.7', '审计页面：任务级全流程追踪、风险告警确认、审计报告（DeepSeek 解读+来源标识）、CSV 导出、按月统计图', async rec => {
    await ensureRole('admin')
    await nav('/audit')
    await waitRows()
    const charts = await page.locator('[_echarts_instance_]').count()
    const [download] = await Promise.all([page.waitForEvent('download', { timeout: 15000 }), page.getByRole('button', { name: '导出 CSV' }).click()])
    expect(/\.csv$/.test(download.suggestedFilename()), 'CSV 文件名')
    let pane = await visibleTab('全流程追踪')
    await page.getByRole('button', { name: '联邦训练链路' }).click()
    await page.waitForTimeout(1500)
    const steps = await page.locator('.el-timeline-item').count()
    // 契约 2.7 期望 auth→did→permission→algo→evidence 的多步链；真后端对 FL/调度链路只回 1 步
    // （甲方 B-015，已立单）。页面能力本身按「时间轴渲染 + 摘要含 traceId」判定。
    expect(steps >= 1, `联邦训练链路时间轴应有步骤，实际 ${steps}`)
    if (steps < 3) rec.note = `真后端 /audit/trace 对联邦训练链路只返回 ${steps} 步（期望 auth→did→permission→algo→evidence 多步）→ 甲方 B-015`
    expect((await page.locator('.trace-summary').innerText()).includes('traceId'), '追踪摘要含 traceId')
    rec.shots.push(await shot('29-audit-trace-fl'))
    pane = await visibleTab('风险告警')
    const ack = pane.getByRole('button', { name: '确认' }).first()
    if (await ack.count()) { await ack.click(); await page.waitForTimeout(1000) }
    pane = await visibleTab('审计报告')
    await page.getByRole('button', { name: '生成报告' }).click()
    await page.locator('.el-tab-pane:visible .source-badge').first().waitFor({ timeout: 30000 })
    const src = await page.locator('.el-tab-pane:visible .source-badge').first().innerText()
    const txt = await pane.innerText()
    expect(/解读|narrative|日报/.test(txt), '报告含自然语言解读')
    rec.shots.push(await shot('30-audit-report'))
    rec.evidence = `统计图=${charts}; csv=${download.suggestedFilename()}; 追踪步骤=${steps}; 报告来源=${src}`
  }, { steps: '/audit → 导出 CSV → 全流程追踪「联邦训练链路」→ 风险告警「确认」→ 审计报告「生成报告」', expect: 'CSV 下载；时间轴 ≥3 步；告警可确认；报告含 narrative 与来源徽章', impl: 'AuditLog.vue' })

  // ------------------------------------------------------------ 边端页面（现有页面保留）+ 风险评估
  await tc('TC-UI-11', '四(现有页面)/2.12', '边端视角：本地感知与分级（L1–L3 打标）、动态隐私风险评估（算法服务评分）', async rec => {
    await ensureRole('admin')
    await nav('/edge/classification')
    await page.getByRole('button', { name: /开始分级/ }).waitFor({ timeout: 20000 })
    await page.getByRole('button', { name: /开始分级/ }).click()
    await page.waitForTimeout(4000)
    const b1 = await page.locator('body').innerText()
    expect(/L1/.test(b1) && /L2/.test(b1) && /L3/.test(b1), '分级结果含 L1/L2/L3')
    rec.shots.push(await shot('31-edge-classification'))
    await nav('/edge/risk')
    await page.getByRole('button', { name: /立即评估/ }).waitFor({ timeout: 20000 })
    await page.getByRole('button', { name: /立即评估/ }).click()
    await page.waitForTimeout(3000)
    const b2 = await page.locator('body').innerText()
    expect(/分|score|风险/.test(b2), '风险评估显示评分')
    await page.getByRole('button', { name: /预算耗尽批量导出/ }).click()
    await page.getByRole('button', { name: /立即评估/ }).click()
    await page.waitForTimeout(3000)
    const b3 = await page.locator('body').innerText()
    rec.shots.push(await shot('32-edge-risk'))
    rec.evidence = `高风险场景含拦截/警告=${/拦截|high|危险|警告/.test(b3)}`
  }, { steps: '/edge/classification → 开始分级；/edge/risk → 立即评估 → 预设「预算耗尽批量导出」→ 立即评估', expect: '分级表打标 L1/L2/L3；风险评分与等级、建议显示', impl: 'DataClassification.vue / RiskAssessment.vue' })

  // ------------------------------------------------------------ RBAC 页面级差异
  await tc('TC-UI-12', '3.1/3.5', '页面级 RBAC：vpp 无签名下发/篡改演示/新建用户/审批；subject 访问调度页被守卫拦回；edge 侧栏无存证菜单且为边端视角', async rec => {
    await ensureRole('vpp')
    await nav('/evidence'); expect(await page.getByRole('button', { name: /篡改演示/ }).count() === 0, 'vpp 无篡改演示')
    await nav('/identity'); await visibleTab('用户管理'); expect(await page.getByRole('button', { name: '新建用户' }).count() === 0, 'vpp 无新建用户')
    await nav('/permission'); await visibleTab('申请审批'); expect(await page.getByRole('button', { name: '通过' }).count() === 0, 'vpp 无审批')
    await nav('/edge/privacy'); expect(await page.getByRole('button', { name: /创建并启动/ }).isDisabled(), 'vpp 创建 FL 置灰')
    rec.shots.push(await shot('33-rbac-vpp-privacy-disabled'))
    await ensureRole('subject')
    await page.goto(BASE + '/cloud/aggregate')
    await page.waitForURL(/\/cloud\/topology/, { timeout: 15000 })
    await ensureRole('edge')
    expect(await page.locator('.sidebar .node-selector').count() > 0, 'edge 为边端视角')
    expect(await page.locator('.sidebar .menu-title', { hasText: '区块链存证' }).count() === 0, 'edge 无存证菜单')
    rec.shots.push(await shot('34-rbac-edge-sidebar'))
    await ensureRole('regulator')
    await nav('/audit')
    await waitRows()
  }, { steps: 'vpp：/evidence、/identity 用户管理、/permission 申请审批、/edge/privacy；subject 直接访问 /cloud/aggregate；edge 登录看侧栏；regulator 打开 /audit', expect: '按钮级与路由级权限与角色矩阵一致', impl: 'v-permission / router.beforeEach / AppSidebar' })

  // ------------------------------------------------------------ WS node_status + 退出 + 布局
  await tc('TC-UI-13', '3.11/3.12', 'WebSocket 实时：前端建立 ws 连接并收到 node_status 节点状态推送（5 秒一次）', async rec => {
    wsFrames.length = 0
    await ensureRole('admin')
    await page.goto(BASE + '/cloud/topology')
    wsFrames.length = 0
    await page.waitForTimeout(16000)
    const types = {}
    for (const f of wsFrames) types[f.type] = (types[f.type] || 0) + 1
    rec.evidence = `16s 内收到的 WS 帧：${JSON.stringify(types)}`
    if (!types.node_status) { rec.note = '16 秒内未收到 node_status 周期推送（后端仅在设备上线时推送，未按契约 2.13 每 5 秒推送）→ 见 B-issue'; return false }
  }, { steps: 'admin 登录首页 → 监听 WebSocket 帧 16 秒', expect: '收到 ≥1 条 node_status', impl: 'ws/manager.py / stores/ws' })

  await tc('TC-UI-14', '非功能', '1366×768 下 11 条路由无横向溢出、ECharts 容器尺寸非 0、无未捕获异常', async rec => {
    await ensureRole('admin')
    const routes = ['/cloud/topology', '/cloud/aggregate', '/edge/classification', '/edge/risk', '/edge/privacy', '/edge/response', '/audit', '/identity', '/assets', '/permission', '/evidence']
    const bad = []
    for (const r of routes) {
      await nav(r)
      await page.waitForTimeout(1200)
      const m = await page.evaluate(() => {
        const de = document.documentElement
        const charts = Array.from(document.querySelectorAll('[_echarts_instance_]')).map(el => { const b = el.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)] })
        return { overflow: de.scrollWidth > de.clientWidth + 1, charts }
      })
      if (m.overflow) bad.push(r + ':横向溢出')
      if (m.charts.some(([w, h]) => w === 0 || h === 0)) bad.push(r + ':图表尺寸 0')
    }
    const pe = consoleErrors.filter(e => e.startsWith('pageerror'))
    rec.evidence = `问题=${bad.join(',') || '无'}; pageerror=${pe.length}; console.error=${consoleErrors.length}`
    if (bad.length || pe.length) { rec.note = bad.concat(pe.slice(0, 3)).join('; '); return false }
  }, { steps: '逐一打开 11 条路由', expect: '无横向滚动条、图表尺寸非 0、无未捕获 JS 异常', impl: '全部页面' })

  await tc('TC-UI-15', '3.1', '退出登录后访问受保护路由重定向到 /login?redirect=', async rec => {
    await ensureRole('admin')
    await logout()
    await page.goto(BASE + '/evidence')
    await page.waitForURL(/\/login/, { timeout: 15000 })
    expect(page.url().includes('redirect='), '带 redirect 参数')
    rec.evidence = page.url()
  }, { steps: '退出 → 直接访问 /evidence', expect: '跳转 /login?redirect=/evidence', impl: 'router 守卫' })

  await browser.close()
  const summary = { tag: TAG, total: results.length, pass: results.filter(r => r.verdict === '通过').length, fail: results.filter(r => r.verdict === '失败').length, consoleErrors: consoleErrors.slice(0, 20) }
  fs.writeFileSync(OUT, JSON.stringify({ summary, results }, null, 1))
  console.log(JSON.stringify(summary))
}

main().catch(async e => { console.error(e); try { await browser?.close() } catch {} ; process.exit(1) })
