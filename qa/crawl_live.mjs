// 真后端模式全站爬行：6 账号登录 × 12 路由 + admin 按钮操作；收集控制台/接口/文本/WS 问题
// 运行：cd frontend && node ../qa/crawl_live.mjs   （需 5199 前端已以 VITE_USE_MOCK=false 运行）
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
// 从 frontend/node_modules 解析 playwright（脚本位于 qa/，需在 frontend/ 下运行）
const { chromium } = createRequire(path.resolve(process.cwd(), 'package.json'))('@playwright/test')

const BASE = process.env.CRAWL_BASE || 'http://127.0.0.1:5199'
const SHOT_DIR = path.resolve(process.cwd(), '../qa/crawl-shots')
const REPORT = path.resolve(process.cwd(), '../qa/crawl-report.json')
fs.mkdirSync(SHOT_DIR, { recursive: true })

const ACCOUNTS = [
  ['admin', 'admin123'], ['grid', 'grid123'], ['vpp', 'vpp123'],
  ['subject', 'subject123'], ['regulator', 'reg123'], ['edge', 'edge123']
]
const ROUTES = ['/cloud/topology', '/cloud/aggregate', '/edge/classification', '/edge/risk', '/edge/privacy',
  '/edge/response', '/audit', '/identity', '/assets', '/permission', '/evidence']
const WS_TYPES = ['node_status', 'fl_progress', 'dispatch_progress', 'audit_alert', 'log', 'evidence_written', 'pong']
const IGNORE = [/Download the Vue Devtools/i, /ResizeObserver/i, /favicon\.ico/i, /\[MSW\]/i, /Failed to load resource.*(401|403)/]

const report = { pages: {}, ws: { total: {}, perPhase: [] }, actions: [] }
let phase = 'init'
function bucket() { return report.pages[phase] || (report.pages[phase] = { console: [], api: [], text: [], actions: [] }) }
function note(kind, item) { const b = bucket(); if (!b[kind].some(x => JSON.stringify(x) === JSON.stringify(item))) b[kind].push(item) }
function setPhase(p) { phase = p }
const sleep = ms => new Promise(r => setTimeout(r, ms))

async function step(name, fn, timeout = 60000) {
  const p = phase
  const t0 = Date.now()
  try {
    await Promise.race([fn(), new Promise((_, rej) => setTimeout(() => rej(new Error('step timeout ' + timeout)), timeout))])
    report.actions.push({ phase: p, name, ok: true, ms: Date.now() - t0 })
    console.log(`  ok   ${name} (${Date.now() - t0}ms)`)
  } catch (e) {
    const msg = String(e.message || e).split('\n').slice(0, 3).join(' | ').slice(0, 400)
    report.actions.push({ phase: p, name, ok: false, error: msg })
    console.log(`  FAIL ${name}: ${msg}`)
    // 失败后关掉可能残留的弹窗，避免影响后续步骤
    try { const pg = globalThis.__page; if (pg) { await pg.keyboard.press('Escape'); await sleep(300); await pg.keyboard.press('Escape') } } catch {}
  }
}

function attach(page) {
  page.on('console', msg => {
    const type = msg.type()
    if (type !== 'error' && type !== 'warning') return
    const text = msg.text()
    if (IGNORE.some(r => r.test(text))) return
    note('console', { type, text: text.slice(0, 400) })
  })
  page.on('pageerror', err => note('console', { type: 'pageerror', text: String(err.message).slice(0, 400) }))
  page.on('response', async res => {
    const url = res.url()
    if (!url.includes('/api/v1')) return
    const req = res.request()
    const rel = url.replace(/^https?:\/\/[^/]+/, '')
    const status = res.status()
    if (status < 200 || status >= 300) {
      let body = ''
      try { body = (await res.text()).slice(0, 300) } catch {}
      note('api', { method: req.method(), path: rel, status, body })
      return
    }
    const ct = res.headers()['content-type'] || ''
    if (!ct.includes('json')) return
    try {
      const j = await res.json()
      if (j && typeof j === 'object' && 'code' in j && j.code !== 0) {
        note('api', { method: req.method(), path: rel, status, code: j.code, message: j.message })
      }
    } catch {}
  })
  page.on('websocket', ws => {
    if (!/\/ws\?token=/.test(ws.url())) return // 排除 Vite HMR 的 websocket
    report.ws.sockets = (report.ws.sockets || 0) + 1
    ws.on('framereceived', f => {
      try {
        const m = JSON.parse(f.payload)
        const t = m.type || '?'
        report.ws.total[t] = (report.ws.total[t] || 0) + 1
        const b = bucket(); b.ws = b.ws || {}; b.ws[t] = (b.ws[t] || 0) + 1
        if (!report.ws.samples) report.ws.samples = {}
        if (!report.ws.samples[t]) report.ws.samples[t] = m
      } catch { report.ws.total.nonjson = (report.ws.total.nonjson || 0) + 1 }
    })
  })
}

async function scanText(page, label) {
  const r = await page.evaluate(() => {
    const txt = document.body.innerText || ''
    const bad = []
    for (const w of ['undefined', 'NaN', '[object Object]']) {
      let i = txt.indexOf(w)
      while (i >= 0 && bad.length < 20) { bad.push(w + ' @ …' + txt.slice(Math.max(0, i - 40), i + 40).replace(/\s+/g, ' ') + '…'); i = txt.indexOf(w, i + 1) }
    }
    // 独立单词 null
    const m = txt.match(/(^|[^a-zA-Z])null([^a-zA-Z]|$)/g)
    if (m) { const i = txt.search(/(^|[^a-zA-Z])null([^a-zA-Z]|$)/); bad.push('null @ …' + txt.slice(Math.max(0, i - 40), i + 40).replace(/\s+/g, ' ') + '…') }
    const empties = Array.from(document.querySelectorAll('.el-table__empty-text')).filter(e => e.offsetParent !== null).map(e => {
      const card = e.closest('.el-tab-pane, .card, .panel, section, .el-drawer, .el-dialog') || document.body
      const title = (card.querySelector('.card-title, .panel-title, .section-title, h3, h2, .el-dialog__title, .el-drawer__header') || {}).innerText || ''
      return (title || card.className || '').toString().slice(0, 60).replace(/\s+/g, ' ')
    })
    const ca = document.querySelector('.content-area, main') || document.body
    return { bad, empties, contentLen: (ca.innerText || '').trim().length }
  })
  for (const b of r.bad) note('text', { label, issue: b })
  for (const e of r.empties) note('text', { label, issue: 'empty table: ' + e })
  if (r.contentLen < 40) note('text', { label, issue: 'content nearly blank (' + r.contentLen + ' chars)' })
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(SHOT_DIR, name + '.jpg'), type: 'jpeg', quality: 55 }).catch(() => {})
}

let CURRENT = null
async function ensureLoggedIn(page) {
  if (!CURRENT) return
  if (page.url().includes('/login')) {
    console.log('    (session lost -> relogin ' + CURRENT[0] + ')')
    note('console', { type: 'note', text: 'session lost (HMR full-reload?) relogin ' + CURRENT[0] })
    await login(page, CURRENT[0], CURRENT[1])
  }
}
async function nav(page, p) {
  await ensureLoggedIn(page)
  await page.evaluate(p => { history.pushState({}, '', p); dispatchEvent(new PopStateEvent('popstate', { state: {} })) }, p)
  await page.waitForURL(u => u.pathname === p, { timeout: 15000 })
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {})
  await sleep(800)
}

async function login(page, user, pass) {
  await page.goto(BASE + '/login', { waitUntil: 'networkidle' })
  const inputs = page.locator('.login-card input, input')
  await inputs.nth(0).fill(user)
  await inputs.nth(1).fill(pass)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(u => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {})
}

async function logout(page) {
  await page.locator('.user-box').click()
  await page.getByText('退出登录').click()
  await page.waitForURL(/\/login/, { timeout: 15000 })
}

async function visitAll(page, user) {
  for (const r of ROUTES) {
    setPhase(`${user}:${r}`)
    await step(`visit ${r}`, async () => {
      await nav(page, r)
      await sleep(1200)
      await scanText(page, r)
      await shot(page, `${user}${r.replace(/\//g, '_')}`)
    })
  }
}

const bellCount = page => page.locator('.bell .el-badge__content').innerText().then(t => Number(t) || 0).catch(() => 0)
const logCount = page => page.locator('.log-bar .log-item').count().catch(() => 0)
const msgText = async page => (await page.locator('.el-message').allInnerTexts()).join(' | ')

async function adminActions(page) {
  const u = 'admin'
  const T = Date.now().toString().slice(-5)
  // ---- identity ----
  setPhase(`${u}:/identity#actions`)
  await nav(page, '/identity')
  await step('identity: 注册 DID', async () => {
    await page.getByRole('tab', { name: 'DID 管理' }).click()
    await page.getByRole('button', { name: /注册 DID/ }).click()
    const dlg = page.locator('.el-dialog', { hasText: '注册 DID' })
    await dlg.locator('.el-form-item', { hasText: '主体类型' }).locator('.el-radio-button', { hasText: /device|设备/ }).click()
    await dlg.getByPlaceholder(/光伏逆变器/).fill('爬行逆变器-' + T)
    await dlg.getByPlaceholder(/园区/).fill('爬行园区')
    await dlg.getByRole('button', { name: '签发并上链' }).click()
    const result = page.locator('.el-dialog:visible').filter({ hasText: /私钥/ })
    await result.waitFor({ timeout: 20000 })
    await scanText(page, 'identity register result')
    await shot(page, 'admin_identity_register')
    await result.getByRole('button', { name: '关闭', exact: true }).click()
  })
  await step('identity: 文档/验签', async () => {
    await page.getByPlaceholder('DID / 名称 / 机构').fill('爬行逆变器-' + T)
    await page.getByRole('button', { name: '查询' }).first().click()
    await sleep(800)
    const row = page.locator('.el-table__row', { hasText: '爬行逆变器-' + T }).first()
    await row.waitFor({ timeout: 10000 })
    await row.getByRole('button', { name: '文档' }).click()
    const drawer = page.locator('.el-drawer', { hasText: 'DID 文档' })
    await drawer.waitFor()
    await scanText(page, 'did doc drawer')
    await page.keyboard.press('Escape')
    await sleep(500)
    await row.getByRole('button', { name: '验签' }).click()
    const vf = page.locator('.el-tab-pane:visible')
    await vf.getByRole('button', { name: '使用刚注册的私钥模拟签名' }).click()
    await vf.getByRole('button', { name: /验\s*签/ }).click()
    await sleep(1500)
    const body = await page.locator('body').innerText()
    if (!/验签通过|valid/i.test(body)) throw new Error('验签结果未显示通过: ' + (await msgText(page)))
    await vf.getByRole('button', { name: '填入无效签名' }).click()
    await vf.getByRole('button', { name: /验\s*签/ }).click()
    await sleep(1500)
    await shot(page, 'admin_identity_verify')
  })
  await step('identity: 冻结/解冻/轮换', async () => {
    await page.getByRole('tab', { name: 'DID 管理' }).click()
    await page.getByPlaceholder('DID / 名称 / 机构').fill('爬行逆变器-' + T)
    await page.getByRole('button', { name: '查询' }).first().click()
    await sleep(800)
    const row = page.locator('.el-tab-pane:visible .el-table__row', { hasText: '爬行逆变器-' + T }).first()
    await row.getByRole('button', { name: '冻结', exact: true }).click()
    const dlg = page.locator('.el-dialog:visible')
    await dlg.getByPlaceholder(/原因/).fill('爬行冻结')
    await dlg.getByRole('button', { name: /确认/ }).click()
    await sleep(1000)
    await row.getByRole('button', { name: '解冻' }).click({ timeout: 10000 })
    await page.locator('.el-dialog:visible').getByRole('button', { name: /确认/ }).click()
    await sleep(1000)
    await row.getByRole('button', { name: '轮换密钥' }).click({ timeout: 10000 })
    await page.locator('.el-message-box:visible .el-button--primary, .el-dialog:visible .el-button--primary').first().click()
    await sleep(1500)
  })
  await step('identity: 密钥管理 生成并绑定', async () => {
    await page.getByRole('tab', { name: '密钥管理' }).click()
    await sleep(800)
    await scanText(page, 'keys tab')
    await page.getByRole('button', { name: '生成并绑定密钥' }).click()
    const dlg = page.locator('.el-dialog:visible')
    await dlg.waitFor()
    await dlg.getByRole('button', { name: '生成', exact: true }).click()
    await sleep(1500)
    await scanText(page, 'key create')
    await page.keyboard.press('Escape')
  })
  await step('identity: 用户管理 新建/编辑/删除', async () => {
    await page.getByRole('tab', { name: '用户管理' }).click()
    await sleep(800)
    await scanText(page, 'users tab')
    await page.getByRole('button', { name: '新建用户' }).click()
    const dlg = page.locator('.el-dialog:visible')
    await dlg.waitFor()
    const byLabel = l => dlg.locator('.el-form-item').filter({ has: page.locator('.el-form-item__label', { hasText: l }) }).locator('input').first()
    await byLabel('用户名').fill('crawl' + T)
    await byLabel('密码').fill('Crawl123456')
    await byLabel('姓名').fill('爬行用户')
    await byLabel('机构').fill('爬行机构')
    // 角色选择
    const sel = dlg.locator('.el-select').first()
    if (await sel.count()) {
      await sel.click()
      await page.locator('.el-select-dropdown:visible .el-select-dropdown__item').first().click().catch(() => {})
      await page.keyboard.press('Escape')
    }
    await dlg.getByRole('button', { name: '保存' }).click()
    await sleep(1500)
    console.log('    msg:', await msgText(page))
    const row = page.locator('.el-tab-pane:visible .el-table__row', { hasText: 'crawl' + T }).first()
    if (await row.count()) {
      await row.getByRole('button', { name: '编辑' }).click()
      await page.locator('.el-dialog:visible').getByRole('button', { name: '保存' }).click()
      await sleep(1000)
      await row.getByRole('button', { name: '删除' }).click()
      await page.locator('.el-message-box:visible .el-button--primary').click()
      await sleep(1000)
    } else throw new Error('新建用户后列表未出现 crawl' + T + ' msg=' + (await msgText(page)))
  })

  // ---- assets ----
  setPhase(`${u}:/assets#actions`)
  await nav(page, '/assets')
  await step('assets: 登记资产(自动分级)→溯源→详情', async () => {
    await page.getByRole('button', { name: /登记资产/ }).click()
    const dlg = page.locator('.el-dialog', { hasText: '数据资产登记' })
    await dlg.getByPlaceholder(/光伏出力/).fill('爬行光伏-' + T)
    await dlg.locator('.el-form-item', { hasText: '数据类型' }).locator('.el-radio-button', { hasText: /pv|光伏/ }).first().click()
    const sel = dlg.locator('.el-form-item', { hasText: '数据源 DID' }).locator('.el-select')
    await sel.click()
    await page.locator('.el-select-dropdown:visible .el-select-dropdown__item').first().click()
    await dlg.getByRole('button', { name: /自动分级/ }).click()
    await sleep(2500)
    await scanText(page, 'asset classify dialog')
    const t = await dlg.innerText()
    if (!/L[1-4]/.test(t)) throw new Error('自动分级未出现 L 级: ' + (await msgText(page)))
    await dlg.getByRole('button', { name: '登记并上链' }).click()
    const result = page.locator('.el-dialog', { hasText: '登记成功' })
    await result.waitFor({ timeout: 20000 })
    await scanText(page, 'asset register result')
    await shot(page, 'admin_assets_register')
    await result.getByRole('button', { name: '查看溯源' }).click()
    const lineage = page.locator('.el-drawer', { hasText: '溯源链路' })
    await lineage.waitFor()
    await sleep(1000)
    await scanText(page, 'lineage drawer')
    await shot(page, 'admin_assets_lineage')
    await page.keyboard.press('Escape')
    await sleep(500)
    await page.getByPlaceholder('名称 / ID / 哈希').fill('爬行光伏-' + T)
    await page.getByRole('button', { name: '查询' }).click()
    await sleep(800)
    const row = page.locator('.el-table__row', { hasText: '爬行光伏-' + T }).first()
    await row.waitFor({ timeout: 10000 })
    await row.getByRole('button', { name: '详情' }).click()
    await page.locator('.el-drawer', { hasText: '资产详情' }).waitFor()
    await sleep(500)
    await scanText(page, 'asset detail drawer')
    await page.keyboard.press('Escape')
  })

  // ---- classification ----
  setPhase(`${u}:/edge/classification#actions`)
  await nav(page, '/edge/classification')
  await step('classification: 开始分级 → 一键登记', async () => {
    await page.getByRole('button', { name: /开始分级/ }).click()
    await sleep(4000)
    await scanText(page, 'classification result')
    await shot(page, 'admin_classification_done')
    const btn = page.getByRole('button', { name: /一键登记为数据资产/ })
    if (await btn.isDisabled()) throw new Error('一键登记按钮仍禁用: ' + (await msgText(page)))
    await btn.click()
    await sleep(4000)
    console.log('    msg:', await msgText(page))
    await scanText(page, 'classification register')
    await shot(page, 'admin_classification_registered')
  })

  // ---- risk ----
  setPhase(`${u}:/edge/risk#actions`)
  await nav(page, '/edge/risk')
  await step('risk: 预设×3 + 立即评估', async () => {
    for (const p of ['低风险统计查询', '高频分钟级拉取', '预算耗尽批量导出']) {
      await page.getByRole('button', { name: p }).click()
      await sleep(1500)
      await page.locator('button:has-text("立即评估")').first().click()
      await sleep(2000)
      await scanText(page, 'risk ' + p)
    }
    await shot(page, 'admin_risk_assessed')
    const know = page.getByRole('button', { name: '知道了' })
    if (await know.count()) await know.click()
  })

  // ---- privacy FL ----
  setPhase(`${u}:/edge/privacy#actions`)
  await nav(page, '/edge/privacy')
  await step('privacy: 创建并启动 FL → 等待完成', async () => {
    const before = report.ws.total.fl_progress || 0
    await page.getByPlaceholder('负荷预测联合建模').fill('爬行FL-' + T)
    await page.locator('.el-form-item', { hasText: '参与节点' }).locator('.el-tag').first().waitFor({ timeout: 10000 }).catch(() => {})
    await page.getByRole('button', { name: /创建并启动/ }).click()
    await sleep(2000)
    console.log('    msg:', await msgText(page))
    const status = page.locator('.status-card', { hasText: '爬行FL-' + T })
    await status.waitFor({ timeout: 15000 })
    let rounds = 0, last = ''
    for (let i = 0; i < 90; i++) {
      await sleep(1000)
      const txt = await status.locator('.el-progress__text').innerText().catch(() => '')
      const m = txt.match(/(\d+)\s*\/\s*(\d+)/)
      if (m) rounds = Number(m[1])
      last = await status.locator('.chip').first().innerText().catch(() => '')
      if (/已完成|completed|失败|failed|取消/i.test(last)) break
    }
    const curve = await page.locator('.card-title', { hasText: '收敛曲线' }).locator('.ct-meta').innerText().catch(() => '?')
    const fl = (report.ws.total.fl_progress || 0) - before
    report.ws.perPhase.push({ phase: 'fl', fl_progress_frames: fl, roundsShown: rounds, status: last, curveMeta: curve })
    console.log(`    FL: rounds=${rounds} status=${last} curve=${curve} fl_progress frames=${fl}`)
    await scanText(page, 'fl running/finished')
    await shot(page, 'admin_privacy_fl')
    if (rounds < 1) throw new Error('FL 轮次未增长 status=' + last)
  }, 130000)
  await step('privacy: 重新生成 demo', async () => {
    await page.getByRole('button', { name: /重新生成/ }).click(); await sleep(1000)
  })

  // ---- cloud aggregate dispatch ----
  setPhase(`${u}:/cloud/aggregate#actions`)
  await nav(page, '/cloud/aggregate')
  await step('aggregate: 生成调度策略', async () => {
    await page.getByRole('button', { name: /生成调度策略/ }).click()
    await sleep(1500)
    console.log('    msg:', await msgText(page))
    const issue = page.getByRole('button', { name: /签名下发/ })
    for (let i = 0; i < 40 && await issue.isDisabled(); i++) await sleep(1000)
    await scanText(page, 'strategy')
    await shot(page, 'admin_aggregate_strategy')
    if (await issue.isDisabled()) throw new Error('签名下发仍禁用（策略未生成）msg=' + (await msgText(page)))
  })
  await step('aggregate: AI 提问', async () => {
    await page.getByRole('button', { name: '为什么选择该节点放电？' }).click()
    await page.locator('.source-badge').first().waitFor({ timeout: 40000 })
    await sleep(500)
    await scanText(page, 'ai answer')
  }, 60000)
  await step('aggregate: 生成分析报告', async () => {
    const dl = page.waitForEvent('download', { timeout: 15000 }).catch(() => null)
    await page.getByRole('button', { name: /生成分析报告/ }).click()
    await sleep(1500)
    console.log('    msg:', await msgText(page), 'download:', !!(await dl))
  })
  await step('aggregate: 签名下发', async () => {
    const before = report.ws.total.dispatch_progress || 0
    await page.getByRole('button', { name: '生成', exact: true }).click()
    await page.getByRole('button', { name: /签名下发/ }).click()
    await sleep(3000)
    console.log('    msg:', await msgText(page))
    const body = await page.locator('body').innerText()
    await scanText(page, 'issued')
    await shot(page, 'admin_aggregate_issued')
    report.ws.perPhase.push({ phase: 'issue', dispatch_progress_frames: (report.ws.total.dispatch_progress || 0) - before })
    if (!/已下发|issued|下发成功/i.test(body)) throw new Error('未见已下发状态')
  })
  await step('aggregate: 模拟越权下发 + 无效签名', async () => {
    const deny = page.getByRole('button', { name: /越权/ })
    if (await deny.count()) { await deny.click(); await sleep(2000); console.log('    deny msg:', await msgText(page)) }
    await page.getByPlaceholder(/签发者 SM2 签名/).fill('invalid')
    await page.getByRole('button', { name: /签名下发/ }).click().catch(() => {})
    await sleep(2000)
    console.log('    invalid msg:', await msgText(page))
    await shot(page, 'admin_aggregate_deny')
  })

  // ---- terminal response ----
  setPhase(`${u}:/edge/response#actions`)
  await nav(page, '/edge/response')
  await step('response: 验签 → 回执', async () => {
    const before = report.ws.total.dispatch_progress || 0
    const sw = page.getByRole('button', { name: /切换到该节点查看/ })
    if (await sw.count()) await sw.click()
    await sleep(1000)
    const vb = page.locator('button:has-text("开始校验"), button:has-text("重新校验")').first()
    if (!(await vb.count())) throw new Error('无校验按钮，面板文本：' + (await page.locator('.command-panel').innerText().catch(() => '?')).replace(/\s+/g, ' ').slice(0, 200))
    if ((await vb.innerText()).includes('开始校验')) await vb.click()
    await page.locator('.verify-panel.passed').waitFor({ timeout: 30000 })
    await page.getByRole('button', { name: /确认执行并回执/ }).click()
    await sleep(5000)
    const chip = await page.locator('.command-panel .status-chip').innerText().catch(() => '?')
    console.log('    chip:', chip, 'msg:', await msgText(page))
    await scanText(page, 'receipt')
    await shot(page, 'admin_response_ack')
    report.ws.perPhase.push({ phase: 'ack', dispatch_progress_frames: (report.ws.total.dispatch_progress || 0) - before, chip })
    if (!/已回执|acked|已执行/.test(chip)) throw new Error('回执后状态 ' + chip)
  }, 90000)

  // ---- evidence ----
  setPhase(`${u}:/evidence#actions`)
  await nav(page, '/evidence')
  await step('evidence: 校验 / 详情 / 凭证 / 追踪', async () => {
    const before = report.ws.total.evidence_written || 0
    await page.locator('.el-table__row').first().getByRole('button', { name: '校验' }).click()
    const vdlg = page.locator('.el-dialog', { hasText: '完整性校验结果' })
    await vdlg.waitFor({ timeout: 15000 })
    await scanText(page, 'verify dialog')
    await vdlg.locator('.el-dialog__headerbtn').click()
    await page.locator('.el-table__row').first().getByRole('button', { name: '详情' }).click()
    const drawer = page.locator('.el-drawer', { hasText: '存证详情' })
    await drawer.waitFor()
    await scanText(page, 'evidence detail')
    const dl = page.waitForEvent('download', { timeout: 8000 }).catch(() => null)
    await drawer.getByRole('button', { name: '导出凭证' }).click()
    console.log('    cert download:', !!(await dl), 'msg:', await msgText(page))
    if (await drawer.getByRole('button', { name: '追踪链路' }).count()) {
      await drawer.getByRole('button', { name: '追踪链路' }).click()
      await sleep(1500)
      await scanText(page, 'evidence trace')
    } else await page.keyboard.press('Escape')
    report.ws.perPhase.push({ phase: 'evidence-view', evidence_written_frames_since_start: report.ws.total.evidence_written || 0 })
  })
  await step('evidence: 篡改演示', async () => {
    await page.getByRole('button', { name: /篡改演示/ }).click()
    const tdlg = page.locator('.el-dialog', { hasText: '篡改演示' })
    await tdlg.waitFor()
    await tdlg.getByRole('button', { name: '执行篡改并校验' }).click()
    await page.locator('.el-dialog', { hasText: '完整性校验结果' }).waitFor({ timeout: 20000 })
    await sleep(500)
    const t = await page.locator('.el-dialog', { hasText: '完整性校验结果' }).innerText()
    await shot(page, 'admin_evidence_tamper')
    await page.locator('.el-dialog:visible .el-dialog__headerbtn').first().click()
    await sleep(1500)
    const body = await page.locator('body').innerText()
    console.log('    tamper result contains 不一致:', /不一致|false|篡改|≠/.test(t), ' chain broken shown:', /断裂|brokenAt/.test(body))
    await scanText(page, 'after tamper')
    if (!/不一致|false|篡改|≠/.test(t)) throw new Error('篡改后校验未显示不一致')
  })

  // ---- audit ----
  setPhase(`${u}:/audit#actions`)
  await nav(page, '/audit')
  await step('audit: 导出 CSV', async () => {
    const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 15000 }), page.getByRole('button', { name: '导出 CSV' }).click()])
    console.log('    csv:', dl.suggestedFilename())
  })
  await step('audit: 筛选 high + 追踪 + 告警确认 + 日报', async () => {
    await page.locator('.el-select', { hasText: /riskLevel/ }).first().click()
    await page.locator('.el-select-dropdown:visible .el-select-dropdown__item', { hasText: /high|高/ }).first().click()
    await page.getByRole('button', { name: '查询' }).click()
    await sleep(1000)
    await scanText(page, 'audit high filter')
    await page.getByRole('tab', { name: '全流程追踪' }).click()
    await page.getByRole('button', { name: '追踪' }).click()
    await sleep(1500)
    const items = await page.locator('.el-timeline-item').count()
    console.log('    trace items:', items, 'msg:', await msgText(page))
    await scanText(page, 'audit trace')
    await shot(page, 'admin_audit_trace')
    await page.getByRole('tab', { name: '风险告警' }).click()
    await sleep(1000)
    await scanText(page, 'alerts tab')
    const bellBefore = await bellCount(page)
    const ackBtn = page.locator('.el-tab-pane:visible').getByRole('button', { name: '确认' }).first()
    if (await ackBtn.count()) {
      await ackBtn.click(); await sleep(1500)
      console.log(`    bell ${bellBefore} -> ${await bellCount(page)} msg:`, await msgText(page))
    } else console.log('    no unacked alert to ack; bell=', bellBefore)
    await page.getByRole('tab', { name: '审计报告' }).click()
    await page.getByRole('button', { name: '生成报告' }).click()
    await page.locator('.el-tab-pane:visible .source-badge').first().waitFor({ timeout: 40000 }).catch(() => {})
    await sleep(500)
    await scanText(page, 'audit report')
    await shot(page, 'admin_audit_report')
    await page.getByRole('tab', { name: '前端操作记录' }).click()
    await sleep(500)
    await scanText(page, 'frontend ops')
  }, 90000)

  // ---- permission (admin part: role dialog / matrix / checker) ----
  setPhase(`${u}:/permission#actions`)
  await nav(page, '/permission')
  await step('permission: 矩阵 / 校验器', async () => {
    await page.getByRole('tab', { name: '权限矩阵' }).click(); await sleep(800)
    await scanText(page, 'matrix')
    await page.getByRole('tab', { name: '权限校验测试器' }).click()
    await page.getByRole('button', { name: /vpp 尝试 dispatch:issue/ }).click()
    await page.getByRole('button', { name: /校\s*验/ }).click(); await sleep(1200)
    const t1 = await page.locator('.el-tab-pane:visible').innerText()
    await page.getByRole('button', { name: /admin 读取 asset 1001/ }).click()
    await page.getByRole('button', { name: /校\s*验/ }).click(); await sleep(1200)
    const t2 = await page.locator('.el-tab-pane:visible').innerText()
    console.log('    checker: vpp DENIED?', /DENIED/.test(t1), ' admin ALLOWED?', /ALLOWED/.test(t2))
    await shot(page, 'admin_permission_check')
    if (!/DENIED/.test(t1) || !/ALLOWED/.test(t2)) throw new Error('校验器结果不符')
  })
}

async function subjectApply(page, T) {
  setPhase('subject:/permission#apply')
  await nav(page, '/permission')
  await step('subject: 申请权限', async () => {
    await page.getByRole('button', { name: /申请权限/ }).click()
    const dlg = page.locator('.el-dialog', { hasText: '申请权限' })
    await dlg.locator('.el-form-item', { hasText: 'resourceType' }).locator('.el-radio-button', { hasText: /^asset$/ }).click()
    await dlg.getByPlaceholder(/资产 ID/).fill(String(1001 + (Number(T) % 70)))
    await dlg.locator('.el-form-item').filter({ has: page.locator('label', { hasText: /^action$/ }) }).locator('.el-radio-button', { hasText: /^read$/ }).click()
    await dlg.locator('textarea').fill('爬行申请-' + T)
    await dlg.getByRole('button', { name: '提交', exact: true }).click()
    await sleep(1500)
    console.log('    msg:', await msgText(page))
    await page.getByRole('tab', { name: '申请审批' }).click(); await sleep(800)
    const row = page.locator('.el-tab-pane:visible .el-table__row', { hasText: '爬行申请-' + T })
    if (!(await row.count())) throw new Error('申请列表未见新申请')
    await shot(page, 'subject_permission_apply')
  })
}

async function adminApprove(page, T) {
  setPhase('admin:/permission#approve')
  await nav(page, '/permission')
  await step('admin: 审批通过 + 已授权 + 回收', async () => {
    await page.getByRole('tab', { name: '申请审批' }).click(); await sleep(800)
    const row = page.locator('.el-tab-pane:visible .el-table__row', { hasText: '爬行申请-' + T })
    await row.waitFor({ timeout: 10000 })
    await row.getByRole('button', { name: '通过' }).click()
    const review = page.locator('.el-dialog', { hasText: '审批通过' })
    await review.locator('textarea').fill('同意')
    await review.getByRole('button', { name: '通过并生成授权' }).click()
    await sleep(1500)
    console.log('    msg:', await msgText(page), 'row:', (await row.innerText()).replace(/\s+/g, ' ').slice(0, 120))
    await shot(page, 'admin_permission_approved')
    await page.getByRole('tab', { name: '已授权管理' }).click(); await sleep(800)
    await scanText(page, 'grants')
    const rb = page.locator('.el-tab-pane:visible').getByRole('button', { name: '回收' }).first()
    if (await rb.count()) { await rb.click(); await page.locator('.el-message-box:visible .el-button--primary').click().catch(() => {}); await sleep(1000); console.log('    revoke msg:', await msgText(page)) }
    await page.getByRole('tab', { name: '角色' }).click().catch(() => {})
    await sleep(500)
    if (await page.getByRole('button', { name: '新建自定义角色' }).count()) {
      await page.getByRole('button', { name: '新建自定义角色' }).click(); await sleep(800)
      await scanText(page, 'role dialog'); await page.keyboard.press('Escape')
    }
  })
}

async function wsPageUpdateCheck(page) {
  setPhase('admin:ws-check')
  await step('ws: node_status / log / bell 页面联动观察 (20s)', async () => {
    await nav(page, '/cloud/topology')
    const logs0 = await logCount(page)
    const bell0 = await bellCount(page)
    const c0 = { ...report.ws.total }
    await sleep(20000)
    const logs1 = await logCount(page)
    const bell1 = await bellCount(page)
    const diff = {}
    for (const k of Object.keys(report.ws.total)) diff[k] = report.ws.total[k] - (c0[k] || 0)
    const wsLabel = await page.locator('.ws-label').innerText().catch(() => '?')
    const topo = await page.evaluate(() => Array.from(document.querySelectorAll('.node-item, .topo-node, .node-card')).map(n => n.innerText.replace(/\s+/g, ' ').slice(0, 60)))
    report.ws.perPhase.push({ phase: 'idle20s', frames: diff, logbar: [logs0, logs1], bell: [bell0, bell1], wsLabel })
    console.log('    20s frames:', JSON.stringify(diff), 'logbar', logs0, '->', logs1, 'bell', bell0, '->', bell1, 'ws:', wsLabel)
    console.log('    topo nodes:', topo.slice(0, 4).join(' || '))
  }, 60000)
}

;(async () => {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 }, acceptDownloads: true })
  const page = await ctx.newPage()
  globalThis.__page = page
  attach(page)
  const T = Date.now().toString().slice(-5)
  ctx.setDefaultTimeout(12000)
  page.on('framenavigated', f => { if (f === page.mainFrame()) console.log('    [nav]', f.url().replace(BASE, '')) })
  for (const [user, pass] of ACCOUNTS) {
    CURRENT = [user, pass]
    console.log(`\n=== ${user} ===`)
    setPhase(`${user}:/login`)
    await step('login', () => login(page, user, pass))
    if (!report.actions.at(-1).ok) continue
    await scanText(page, 'after login')
    await visitAll(page, user)
    if (user === 'admin') {
      await adminActions(page)
      await wsPageUpdateCheck(page)
    }
    if (user === 'subject') await subjectApply(page, T)
    if (user === 'subject') {
      setPhase('admin:/permission#approve')
      CURRENT = ['admin', 'admin123']
      await step('relogin admin', async () => { await logout(page).catch(() => {}); await login(page, 'admin', 'admin123') })
      await adminApprove(page, T)
    }
    setPhase(`${user}:/logout`)
    await step('logout', async () => { await ensureLoggedIn(page); await logout(page) })
  }
  await browser.close()
  // summary
  const out = { ...report, summary: {} }
  for (const [k, v] of Object.entries(report.pages)) {
    if (v.console.length || v.api.length || v.text.length) out.summary[k] = { console: v.console.length, api: v.api.length, text: v.text.length }
  }
  fs.writeFileSync(REPORT, JSON.stringify(out, null, 2))
  console.log('\n=== WS totals ===', JSON.stringify(report.ws.total))
  console.log('=== failed steps ===')
  for (const a of report.actions.filter(a => !a.ok)) console.log(' ', a.phase, a.name, '=>', a.error)
  console.log('=== issues per page ===')
  for (const [k, v] of Object.entries(report.pages)) {
    const items = [...v.console.map(c => `[${c.type}] ${c.text.slice(0, 160)}`), ...v.api.map(a => `[api ${a.method} ${a.path} ${a.status} code=${a.code ?? ''} ${a.message || a.body || ''}`), ...v.text.map(t => `[text ${t.label}] ${t.issue}`)]
    if (items.length) { console.log(k); for (const i of items) console.log('   ', i) }
  }
  console.log('report:', REPORT)
})()
