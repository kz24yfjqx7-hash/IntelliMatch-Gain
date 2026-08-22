# STATUS · integration（前端接真后端联调）

日期：2026-08-22　负责人：integration（中途因额度中断，由主 Agent 接手完成）

## 环境
甲方 `backend/` 原样合入（未改一行）；MariaDB 11.4 用户态实例导入 `backend/sql/01_schema.sql + 02_seed.sql`（26 表 / 6 用户 / 80 资产 / 154 存证 / 2880 指标）；backend:8000、algo-service:8100、frontend:5199（`VITE_USE_MOCK=false`）、mock 副本:5300。详见 `INTEGRATION-ENV.md`。

## 做了什么
1. **全站巡检**（`qa/crawl_live.mjs`，可复跑）：6 账号 × 11 路由 + admin 全按钮操作，采集控制台/接口/文本/WS 四类问题，产出 `qa/crawl-report.json` 与 `qa/crawl-shots/`。最后一轮 33 个阶段，剩余 console 12（全部为 ECharts 容器宽高告警与爬虫上下文的 replaceState 警告，干净会话不复现）、api 3、text 2。WS 六类消息均收到（log 29 / evidence_written 48 / fl_progress 10 / dispatch_progress 6 / audit_alert 1 / pong 5）。
2. **真后端适配层**：`api/dispatch.js` 把后端的 `status=success + issued + ackStatus(none|partial|all) + ackDetail[]` 归一化成前端内部的 `issued/acked` 与 `ackedNodes`，两种模式共用一套页面逻辑。
3. **修复清单**（乙方侧，文件:行见各 MSG 回复）：
   - 签名下发在真后端必败（1004）→ 真后端发空签名走后端托管代签；
   - 终端响应页永远"暂无已下发指令" → 按 `issued/commandId/ackStatus` 判断；
   - 终端验签步骤谎报"验签通过" → 有签名字节才真验签，否则明示未复核；
   - 非管理角色轮询 `/audit/alerts`、未登录状态下的定时刷新造成 1003/401 污染 → 按权限与登录态门控，审计菜单按角色过滤；
   - 告警确认用了字符串 `alertId`（后端要数字主键）→ store 内换算，WS 推送同时保存两个 id；
   - 回收授权未带后端必填的 `reason` → API 层补默认原因；
   - 新建用户后列表看不到（后端 id 升序分页，新用户在最后一页）→ 保存后跳到最后一页；
   - 算法侧三项（见 test-func / test-api 工单回复）：DP 预算耗尽熔断停止、DQN `violations` 只记真实修正、DeepSeek health 反映真实可达性（启动探活）。
4. **部署配置核对**：后端探活路径是根 `/health`（无 `/api/v1/health`），compose / `install.sh` / kiosk 脚本与 `deploy/nginx.conf`（新增 `location = /health`）已统一；环境变量与 `backend/core/config.py` 一一对应。

## 回归结果（2026-08-22 收尾）
| 套件 | 结果 |
|---|---|
| algo pytest | 315/315（新增 2 条 DP 熔断用例） |
| frontend vitest | 137/137 |
| Playwright E2E（mock 模式 5300） | 28/28 |
| MSW selfcheck | 12/12 |
| 全站巡检 | 33 阶段，无阻断问题 |
| 后端 pytest（甲方自带） | 185/186（唯一失败用例假设算法服务不可用，本机 algo 正在运行，属环境差异） |

## 未解决 / 移交
- 甲方缺陷 12 条见 `BACKEND-ISSUES.md`（B-002 越权读他人存证、B-010 并发写存证丢失、B-011 loss 列溢出为较重的三条）。
- 身份中心「模拟签名」未实现浏览器端真 SM2；真后端下按 DID 文档状态判定并已在界面写明。
- 真后端不暴露 `simulatePoison`，投毒检测演示需 mock 模式或直连算法服务。
- 仍需外部条件：Docker 权限（实打镜像/安装包）、树莓派真机、干净 x86 机整包验收。
