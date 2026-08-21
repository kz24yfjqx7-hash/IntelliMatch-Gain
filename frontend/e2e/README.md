# E2E（Playwright，真实无头 Chromium）

针对 **MSW 离线 mock 模式** 的端到端测试，按《答辩演示剧本》十步 + 契约第六部分 12 条验收链路逐一在浏览器里真点。

## 运行

```bash
cd frontend
npm i                              # 已含 @playwright/test
npx playwright install chromium    # 首次：下载 headless chromium（~115MB）
npm run test:e2e                   # 自动拉起 VITE_USE_MOCK=true 的 vite（5197 端口）并执行
```

- 已有 dev server 时：`E2E_BASE_URL=http://localhost:5197 npm run test:e2e`（跳过自动起服务）。
- 单个文件：`npx playwright test e2e/09-evidence.spec.js`；带界面：`npm run test:e2e:headed`。
- 截图输出到 `e2e/screenshots/`（jpeg，≤ 20 张）；失败现场在 `e2e/.results/`（已 gitignore 价值不大，可删）。

## 用例一览

| 文件 | 覆盖 |
|---|---|
| `01-auth` | 6 个演示账号登录 / 按角色落地 / 侧栏与按钮级权限（v-permission）/ 路由守卫 / 退出后访问受保护路由被拦 / 错误密码 |
| `02-home` | TrustFlowBanner 六环节计数 + 点击跳转、拓扑 4 节点、视角切换、WS node_status 刷新 |
| `03-identity` | 注册 DID → 文档（@context / verificationMethod）→ 验签通过 / 无效签名失败 → 统计 +1；冻结 / 解冻 / 轮换密钥 |
| `04-assets` | 登记对话框自动分级（L 级 + 因子）→ 登记上链（hash / evidenceId）→ 详情 → 溯源（ECharts graph）|
| `05-permission` | subject 申请 → 同页切 admin 审批通过 → 已授权 → 矩阵 → 校验器 DENIED / ALLOWED |
| `06-dispatch-deny` | vpp「模拟越权下发」→ 1003 横幅 + toast → 铃铛 +1 / R01 → 审计告警 → traceId 时间轴 |
| `07-privacy-fl` | 创建并启动 FL → 轮次 ≥3 → 收敛 / 压缩率曲线尺寸非 0 → 预算环 → 完成；vpp 按钮置灰 |
| `08-dispatch` | 运行 DQN → 策略表 / 约束 → AI 解释（source 徽章）→ 签名下发 → 终端响应 DID 验签 → 回执 ack；invalid 签名 → 1004 |
| `09-evidence` | 校验 intact → 篡改演示 → verify 不一致标红 → 链 brokenAt / 受影响块 → 导出凭证 → traceId 追踪 |
| `10-audit` | 统计图、导出 CSV 下载、riskLevel 筛选、traceId 时间轴（DEMO_TRACE ≥3 步）、告警 ack 减少铃铛计数、日报 narrative + 徽章 |
| `11-layout-memory` | 11 条路由 × {1366×768, 1920×1080}：无横向滚动、ECharts 容器尺寸非 0、header/sidebar/content/logbar 不重叠；切换路由 20 次堆与 console 不膨胀 |

每个用例统一断言：**无 `console.error`、无未捕获异常、无 4xx 资源**（仅被测的 401 / 403 场景白名单）。

## 注意

- MSW 的内存数据库随页面刷新重置。跨账号 / 跨页面要延续状态的用例使用 `helpers.nav()`（SPA 内 pushState 导航）而不是 `page.goto`。
- 端口 5173 在本机被 nginx 占用，配置固定用 5197。
