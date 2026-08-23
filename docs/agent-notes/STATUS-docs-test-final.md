# STATUS · docs-test-final（两份测试文档最后一轮同步优化）

- 执行时间：2026-08-23（全量复测之后的本会话收尾）
- 权限范围：仅修改 `docs/测试文档.md`、`docs/测试文档-接口与安全.md`，并新建本文件。未动任何代码 / 测试 / 迁移脚本。
- 事实来源：`git log`（`3e3acb1` B-030、`9ceaabb` B-031、`9497ac0`+`7a5692f` 权限中心字段名三连、`47eb578` 首页 500/sys_notice）、`docs/agent-notes/BACKEND-ISSUES.md`（B-030 / B-031 末尾条目）、`backend/modules/algo/router.py:57-66`（发布模型现为 `@require_permission("algo","execute")`）、`backend/sql/05_migrate_20260823.sql`。逐条核对后再落笔，无编造。

## 补进的缺陷清单（编号 + 一句话）

| 单号（测试文档.md / 接口与安全.md） | 提交 | 一句话 |
|---|---|---|
| B-030 / §4.2 B-016 | `3e3acb1` | 风控告警 `alert_id` 用 `COUNT(*)+1` 并发撞唯一键致告警丢失 → 改行自增主键派生 |
| B-031 / §4.2 B-017 | `9ceaabb` | 发布模型接口后端 `model:read` 比前端 `algo:execute` 松、可越权发布 → 后端对齐 `algo:execute`（前端裁剪严于后端的越权面） |
| UI-11 /（仅测试文档.md，接口与安全 §7.2 交叉引用） | `9497ac0`+`7a5692f` | 权限中心「授权人 grantedBy / 被授权主体 granteeDid / 审批意见 reason」三处字段名不匹配致界面显示为空 → 后端补字段 + 前端改读改发（接口全过、界面为空的典型） |
| B-032 /（仅测试文档.md，接口与安全 §7.2 交叉引用） | `47eb578` | `sys_notice` 表在存量库缺失致 `GET /notices` 500 打崩首页 → 建表 + `05_migrate_20260823.sql` + 服务层 `1146` 缺表兜底（存量库遗留） |

## 改 / 新增的章节

### docs/测试文档.md
- §6.4 表：intro 从「7 条」改「11 条」并厘清编号约定；表末新增 4 行（B-030 / B-031 / UI-11 / B-032）。
- §8.5 遗留清单第 1 条：补 `05_migrate_20260823.sql`（sys_notice）与 03、04 并列，说明当前开发库均已执行 / 建表、缺表已有服务层兜底。
- §8.7（新增小节）：本会话 4 条缺陷汇总表 + **测试方法论要点（接口层自动化全过 ≠ 界面正确）** + 回归结果表（8 行「待补」占位）。
- §10 总结：新增「本会话补充」项，收敛 4 条缺陷 + 方法论要点，回归数字指向 §8.7 待补。

### docs/测试文档-接口与安全.md
- §4.2 heading + intro blockquote：号段续编到 B-016 / B-017，注明 ↔ BACKEND-ISSUES B-030 / B-031；表内新增 2 行。
- §4 intro（§4.2 引用句）：「2 条」改「5 条（B-013~B-017）」。
- §6.1 接口级 RBAC 矩阵：intro「6 个接口」改「7 个接口」，新增「发布模型 POST /fl/models/{version}/publish = algo:execute」行（前后端一致）；矩阵下方「6×6」改「6 角色 × 7 接口」。
- §6 安全结论：新增一段 —— B-017 是「前端裁剪严于后端」的越权面，已通过后端对齐 `algo:execute` 消除；B-016 属可靠性非攻击面。安全结论维持不变。
- §7.2 缺陷结论表：新增 4 行交叉引用（B-016、B-017、UI-11、首页 500）。

## 回归数字（已定稿，2026-08-23 由协调者回填）
- `docs/测试文档.md` §8.7 回归表 8 处「待补」已全部填入：backend pytest **352**、algo-service pytest **318**、frontend vitest **141**、契约审计（`--algo-port 8100`）**0 阻断**（4 条 mocks 多出 notices handler 告警，附加性质允许）、`check_backend_live` **123/123**、接口层 api_func_test **114/114**、页面层 ui_func_test **16/16**、存证链 **intact，height 7689**；另加一行前端构建「通过」。§10 总结「本会话补充」末句、§0 硬遗留行、§8.5 item 2 均已同步为已定稿口径，无 待补 残留。
- **新增仓库完整性/契约项（非功能缺陷）**：notices（站内消息/铃铛）功能「半提交 + 契约外」已按用户决定补全（提交 `592312e`）——此前模块实现文件未提交（fresh clone 无法启动）、`/notices` 4 接口不在冻结契约（契约审计 api_extra 阻断 4）；处理为完整提交 notice 文件 + 4 接口调用从 `api/*.js` 移入 `stores/notice.js`（直接用 `request`，同 `/did/sign`）+ 删 `api/notice.js` → api_extra 归零、阻断 0（残余 4 条为 mocks notices handler 告警，允许）。写入 §8.7 末尾，并把 §8.5 遗留清单「契约审计阻断 0」改为「0 阻断（4 条 mocks 多出 notices handler 告警，允许）」。该项印证并延伸方法论要点：接口层/单测全过 ≠ 交付完整，仓库一致性也要查。

## 发现的文档 ↔ 代码不一致（只报告，未擅改代码）
1. **迁移脚本路径**：任务备忘与两份文档历史文字用 `sql/05_migrate_20260823.sql`，实际文件在 `backend/sql/05_migrate_20260823.sql`（03、04 同样在 `backend/sql/`）。文档既有行文一直写全路径 `backend/sql/...`，本次新增文字统一用 `backend/sql/05_migrate_20260823.sql`，与实际一致。
2. **存在重复的迁移脚本**：`backend/sql/` 下同时有 `05_migrate_20260823.sql`（提交 `47eb578` 随首页 500 修复产生，是权威版）与 `05_migrate_20260823_notice.sql`（内容等价、连接串示例不同，疑似另一 Agent 另建）。两份 `CREATE TABLE IF NOT EXISTS sys_notice` 等价、均幂等。文档只引用权威的 `05_migrate_20260823.sql`。建议后续删掉 `_notice` 冗余副本以免歧义（未处理，超出本任务范围）。
3. **B-030 的 BACKEND-ISSUES 编号与本文档号段罕见地连号**：`docs/测试文档.md` §6.4 甲方后端历来沿用 BACKEND-ISSUES 编号（B-027/B-028/B-029），而《接口与安全》§4.2 用自有号段（B-013~B-017）。B-030 / B-031 在 BACKEND-ISSUES 与《测试文档.md》里同号，在《接口与安全》§4.2 里则是 B-016 / B-017 —— 已在两文档 intro 显式注明对应关系，不是笔误。
