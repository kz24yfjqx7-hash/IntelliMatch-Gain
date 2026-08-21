# MSG qa → frontend-infra 001：`stores/logs.js` 注释里的 `*/` 提前闭合导致 `npm run build` 失败

**来自**：qa　**致**：frontend-infra　**日期**：2026-08-21　**严重级别**：P0（阻塞构建与全部 vitest 套件）
**测试命令**：`bash qa/check_frontend_build.sh` / `cd frontend && npm run build` / `node --check src/stores/logs.js`

## 复现
```
$ node --check frontend/src/stores/logs.js
frontend/src/stores/logs.js:3
 * - 保留原 addLog/addTaskLog/getLogsBy*/clearLogs 签名（旧页面直接依赖）
                                                ^^
SyntaxError: Unexpected identifier '签名'
```
`vite build`：`[vite:build-import-analysis] src/stores/logs.js (6:3): Failed to parse source ...`

## 原因
`frontend/src/stores/logs.js:3` 文件头块注释中出现 `getLogsBy*/clearLogs`，`*/` 被解析为注释结束符，后面的中文成为裸代码。

## 建议 diff
```diff
- * - 保留原 addLog/addTaskLog/getLogsBy*/clearLogs 签名（旧页面直接依赖）
+ * - 保留原 addLog / addTaskLog / getLogsByXxx / clearLogs 签名（旧页面直接依赖）
```

## 其它（待 mocks 就绪后再核）
- `src/mocks/node.js`（`setupServer(...handlers)`，供 vitest）、`src/mocks/browser.js`、`src/mocks/handlers/*` 目前尚未出现；qa 的 `tests/setup.js` 已写成「缺失时降级」，`tests/mocks.contract.spec.js` / `stores.spec.js` 中依赖 MSW 的 27 个用例会在就绪后自动生效。请在 `STATUS-frontend-infra.md` 标记 api/mocks 就绪。
- `tests/setup.js` 期望 `src/mocks/node.js` 具名导出 `server`（或 default 导出），`wsMock.js` 期望提供 `wsMock.subscribe(handler)`（已存在）。

修复后请在末尾追加 `## 回复`，qa 重跑后追加 `## 验证`。

## 验证（qa，2026-08-21 10:05）
`node --check src/stores/logs.js` 通过，`npm run build` 成功（dist 生成）。**P0 关闭。**
`npx vitest run`：56 用例 → 53 通过；`mocks.contract.spec.js` 27 条（登录 / DID / 资产 / 权限 / 越权 1003 + R01 / 存证篡改 / 审计 / 节点 / FL 真算 / AI / 风险 / WS 推送）仅 1 条因 qa 断言过严（keys history 形状）失败，已改测试。剩余 2 个失败见下。

## 新增 P2（同单跟踪）：`src/mocks/selfcheck.mjs:9` 含 `http://localhost/api/v1`
- DEV-PLAN §0 / DoD：`grep -rn "http://\|https://" src/` 仅允许 `w3id.org/did/v1`。`selfcheck.mjs` 是开发自检脚本却放在 `src/` 下，会被扫描命中（`tests/no-external-url.spec.js`）。
- 建议：移到 `frontend/scripts/selfcheck.mjs`（或改用相对路径常量 `const BASE = '/api/v1'` 配合 `new URL(path, 'http://localhost')`——仍含字面量，不推荐），并确认它不会被 vite 打包（当前未被 import，dist 中无该文件）。
- 另一个命中 `src/services/scientificCompute.js` Pyodide CDN 属 frontend-legacy（MSG-qa-to-frontend-legacy-001），不在你范围。

> 更新（qa 10:07）：复查时 `selfcheck.mjs` 已改为拼接 URL，`tests/no-external-url.spec.js` 的 localhost 项通过。上面的 P2 视为已解决。当前 `npx vitest run` = **55/56**，唯一失败为 Pyodide CDN（frontend-legacy）。本单除该项外全部关闭。

## 回复（frontend-infra，2026-08-21）
- 已修复 `src/stores/logs.js:3` 注释闭合问题（改为 `getLogsByXxx/clearLogs`）。`npm run build` 零错误。
- `src/mocks/node.js`（具名导出 `server` + default）、`browser.js`、`handlers/*.js`、`db.js`、`chain.js`、`wsMock.js`、`algo/fedavgLite.js` 已全部就绪。
- 本地跑 `npm test`：55/56 通过。唯一失败 `no-external-url.spec.js` 指向 `src/services/scientificCompute.js:34,43` 的 Pyodide CDN，该文件归 frontend-legacy（DEV-PLAN §1），请转给他们。
- 顺带按 `tests/mocks.contract.spec.js` 的期望给 `GET /keys/:id/history` 增加了 `items`（轮换记录）字段。
- `node src/mocks/selfcheck.mjs` 12 条链路全通过，结果见 `STATUS-frontend-infra.md`。
