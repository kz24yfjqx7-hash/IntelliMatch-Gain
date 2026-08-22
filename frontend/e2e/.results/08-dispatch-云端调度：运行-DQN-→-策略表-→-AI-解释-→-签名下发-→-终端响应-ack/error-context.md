# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: 08-dispatch.spec.js >> 云端调度：运行 DQN → 策略表 → AI 解释 → 签名下发 → 终端响应 ack
- Location: e2e/08-dispatch.spec.js:4:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('button', { name: /切换到该节点查看/ }).or(locator('.command-panel .status-chip').filter({ hasText: /已下发|issued/ }))
Expected: visible
Timeout: 15000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 15000ms
  - waiting for getByRole('button', { name: /切换到该节点查看/ }).or(locator('.command-panel .status-chip').filter({ hasText: /已下发|issued/ }))

```

```yaml
- banner:
  - text: ⚡ 能源可信数据空间平台 视角切换： ☁️ 云端 🖥️ 边端
  - img
  - superscript: "21"
  - button "系统管理员 系统管理员":
    - img
    - text: 系统管理员 系统管理员
- complementary:
  - navigation:
    - text: 🖥️ 边端节点 选择节点 Node-A 虚拟电厂节点A Node-B 虚拟电厂节点B Node-C 虚拟电厂节点C Node-D 虚拟电厂节点D
    - list:
      - listitem: 📊 本地感知与分级
      - listitem: ⚠️ 动态隐私风险评估
      - listitem: 🔐 边缘隐私保护计算
      - listitem: ✅ 终端响应与执行
      - listitem: 📋 系统审计与日志中心
    - text: 🛡️ 可信数据空间
    - list:
      - listitem: 🪪 身份与可信接入
      - listitem: 🗂️ 能源数据资产
      - listitem: 🔑 权限控制中心
      - listitem: ⛓️ 区块链存证
- main:
  - heading "✅ 终端响应与执行" [level=2]
  - paragraph: "接收云端签名下发的调度指令，校验签发者 DID 与 SM2 签名后执行本地设备控制，并回执（POST /dispatch/tasks/{id}/ack）"
  - text: 当前节点： 虚拟电厂节点A 在线 did:vpp:edge:0xc82c18c...b807e1 📨 接收到的调度指令
  - combobox
  - text: cmd-000057 · 已下发
  - img
  - button "刷新"
  - text: 已回执 任务 / 指令编号 dp-000057 / cmd-000057 下发时间 2026-08-22 20:38:19 签发者 DID did:vpp:user:0x34ba9ca...be8f98 traceId tr-20260822-73ceeeb0 调度动作 充电 目标功率 -16.6 kW 执行时段 2026-08-22T15:00~16:00+08:00 Q 值 / 原因 52.49 · SOC有余量 🪪 DID 签名校验
  - button "开始校验"
  - text: "1 读取签发者 DID signerDid 2 解析 DID 文档（GET /did/{did}） verificationMethod → SM2 公钥 3 验签（POST /did/verify） message = commandId，signature = 签发者签名"
  - button "已回执" [disabled]
  - text: 📈 储能功率变化曲线 📊 执行状态对比 项目 执行前 执行后 储能功率 -12.0 kW -12.0 kW 运行模式 待机 待机 响应时延 - - 储能SOC 65.0% 65.0%
- text: 💻 实时日志 实时在线 2026/8/22 12:39:27 [AUDIT] 系统管理员 audit:export → success tr-20260822-113ea83b 2026/8/22 12:39:27 [AUDIT] [R04_BULK_EXPORT] 单次导出 2781 条数据，超过 1000 条阈值 tr-20260822-113ea83b 2026/8/22 12:39:27 [CHAIN] 存证上链 ev-001760（audit，高度 1760） tr-20260822-113ea83b
```

# Test source

```ts
  1  | import { test, expect } from '@playwright/test'
  2  | import { attachGuards, login, nav, assertChartsHaveSize, assertNoHorizontalOverflow, shot } from './helpers.js'
  3  | 
  4  | test('云端调度：运行 DQN → 策略表 → AI 解释 → 签名下发 → 终端响应 ack', async ({ page }) => {
  5  |   test.setTimeout(150000)
  6  |   const g = attachGuards(page)
  7  |   await login(page, 'admin')
  8  |   await page.goto('/cloud/aggregate')
  9  |   await page.getByRole('button', { name: /生成调度策略/ }).click()
  10 |   await expect(page.getByRole('button', { name: /签名下发/ })).toBeEnabled({ timeout: 30000 })
  11 |   const body1 = await page.locator('body').innerText()
  12 |   expect(body1).toMatch(/charge|discharge|idle|充电|放电|待机/)
  13 |   expect(body1).toMatch(/SOC|约束/)
  14 |   await shot(page, 'dispatch-strategy')
  15 |   expect(await assertChartsHaveSize(page, '(aggregate)')).toBeGreaterThanOrEqual(1)
  16 | 
  17 |   // AI 解释
  18 |   await page.getByRole('button', { name: '为什么选择该节点放电？' }).click()
  19 |   await expect(page.locator('.source-badge.live, .source-badge.cache, .source-badge.rule').first()).toBeVisible({ timeout: 30000 })
  20 |   await expect(page.locator('body')).toContainText(/reasoning|推理|解释|answer|回答/i)
  21 | 
  22 |   // 签名下发
  23 |   await page.getByRole('button', { name: '生成', exact: true }).click()
  24 |   await page.getByRole('button', { name: /签名下发/ }).click()
  25 |   await expect(page.locator('body')).toContainText(/已下发|issued|下发成功/i, { timeout: 30000 })
  26 |   await shot(page, 'dispatch-issued')
  27 | 
  28 |   // 终端响应
  29 |   await nav(page, '/edge/response')
  30 |   await expect(page.locator('.el-select').first()).toBeVisible()
  31 |   // 若当前节点不是指令目标节点，页面给出一键切换提示
  32 |   const switchBtn = page.getByRole('button', { name: /切换到该节点查看/ })
  33 |   const issuedChip = page.locator('.command-panel .status-chip', { hasText: /已下发|issued/ })
> 34 |   await expect(switchBtn.or(issuedChip)).toBeVisible()
     |                                          ^ Error: expect(locator).toBeVisible() failed
  35 |   if (await switchBtn.count()) await switchBtn.click()
  36 |   await expect(issuedChip).toBeVisible()
  37 |   // 选中指令后页面自动执行 DID 验签（按钮变为「重新校验」）；若未自动触发则手动点
  38 |   const verifyBtn = page.getByRole('button', { name: /开始校验|重新校验/ })
  39 |   if ((await verifyBtn.innerText()).includes('开始校验')) await verifyBtn.click()
  40 |   await expect(page.locator('.verify-panel')).toHaveClass(/passed/, { timeout: 30000 })
  41 |   await page.getByRole('button', { name: /确认执行并回执/ }).click()
  42 |   await expect(page.locator('.command-panel .status-chip')).toHaveText(/已回执|acked|已执行/, { timeout: 60000 })
  43 |   // mock 回执会返回 evidenceId；真后端 ack 响应无 evidenceId（BACKEND-ISSUES），两种模式都成立：有执行完成时间即可
  44 |   await expect(page.locator('.receipt-summary')).toContainText(/ev-|evidence|执行完成/i)
  45 |   await expect(page.getByRole('button', { name: /^已回执$/ })).toBeDisabled()
  46 |   await shot(page, 'terminal-ack')
  47 |   await assertNoHorizontalOverflow(page, '(response)')
  48 |   g.assertClean()
  49 | })
  50 | 
  51 | test('云端调度：无效签名触发 1004 验签失败提示', async ({ page }) => {
  52 |   const g = attachGuards(page)
  53 |   await login(page, 'admin')
  54 |   await page.goto('/cloud/aggregate')
  55 |   await page.getByRole('button', { name: /生成调度策略/ }).click()
  56 |   await expect(page.getByRole('button', { name: /签名下发/ })).toBeEnabled({ timeout: 30000 })
  57 |   await page.getByPlaceholder(/签发者 SM2 签名/).fill('invalid')
  58 |   await page.getByRole('button', { name: /签名下发/ }).click()
  59 |   await expect(page.locator('.el-message').filter({ hasText: /验签失败|1004/ }).first()).toBeVisible()
  60 |   // 唯一允许的 4xx：验签失败的 403（这正是被测现象）
  61 |   expect(g.errors.filter(e => !/403/.test(e))).toEqual([])
  62 | })
  63 | 
```