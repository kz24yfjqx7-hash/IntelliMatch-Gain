# STATUS · docs-manual · 2026-08-23

**职责**：把 `docs/系统操作手册.md` 更新到 2026-08-23 全量复测轮的真实状态并提升可用性。只改了本文件与手册。

## 改动章节
- 卷首：本次更新改为 2026-08-23（四个修复提交 + 开发默认直连真后端）；迁移遗留项措辞为「2026-08-23 复核仍未执行」；目录增 0 章、8 章链接改名。
- **新增 0 章「5 分钟上手」**（每步含成功标准）。
- 1.2：Vite 端口口径（默认 5173 / 联调机 5199）。
- 2.3 ④⑤：`npm run dev` 即真后端；MSW 改临时 `VITE_USE_MOCK=true npm run dev`。
- 2.4：提醒补 08-23 只读复核结果。
- 3.3：subject 看不到的菜单补齐；edge 行补审计 1003。
- 4.1–4.11：统一「入口 → 界面分区 → 典型操作 → 涉及接口 → 常见问题」；补路由权限（4.5 model:read、4.6 dispatch:read、4.7 roles）；4.8/4.9/4.10/4.11 加斑马纹+固定操作列说明；4.7/4.10 补 R03 / ruleCode。
- 5：第 4、5、7 步按钮名与第 4 章对齐；应急说明 MSW 不跨标签。
- 6.1：新增 3 条排错（200 s/9 s 自锁、R03 不出现、斑马纹/固定列）；6.7 补 `【风控告警】` 日志行。
- 7.1：`GET /audit/alerts` 补 `ruleCode`；7.3：`VITE_USE_MOCK` 两处默认值。
- 8：拆为 8.1（2026-08-23）与 8.2（2026-08-22），遗留项保留并更新措辞。

## 核对出的文档与代码不一致（已修）
1. 2.3 ④ 仍要求 `VITE_USE_MOCK=false npx vite --port 5173` → `.env.development` 已默认 false。
2. 4.5 / 4.6 标题未写路由权限（`router/index.js`：`model:read` / `dispatch:read`）。
3. 3.3 subject 行写「看不到的菜单：无」→ `AppSidebar.vue` 按 `permission` 过滤，subject 看不到「边缘隐私保护计算」「终端响应与执行」。
4. 7.1 `GET /audit/alerts` 漏 `ruleCode` 查询参数（`audit/router.py:68`）。
5. 5 章第 7 步只写 curl 下发，与 4.2「界面直接点按钮即可」不一致。
6. ENV 第 4 条写 R03 修复「未提交」，git 实际已提交为 `7edd38c`，手册按提交号写。

## 留给主 Agent 定夺
- 任务要求「端口用 5199」，但 `vite.config.js` 默认 5173；手册写成「默认 5173，本联调机 5199」。若希望手册统一只写 5199，需要改 `vite.config.js`（我没改）。
- `backend.tar` 手册原本未提及，未新增说明。
- 6.1 R03 条目给了 `redis-cli DEL risk:alerted:R03_PERM_CHURN:<DID>` 作排错手段，key 格式取自 `rules.py` 的 `_ALERT_FLAG_PREFIX`，未实测。
