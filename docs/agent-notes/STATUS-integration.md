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

---

## 第二轮复核（integration，2026-08-22 晚，模型切换后继续）

上一轮的修复已在 `2eea39c` / `8586249` 提交。本轮做的是**独立复核 + 补漏**，未回退任何已有修改。

### 1. 新增/修复
| 文件:行 | 改动 | 原因 |
|---|---|---|
| `qa/check_backend_live.sh:95-115` | `check_raw` 改用临时文件把响应体传给 python | 审计 CSV 导出 634KB，走 argv 触发 `Argument list too long`，把一条本应 PASS 的用例误报为 FAIL |
| `frontend/src/stores/perspective.js:122-180` | 新增 `startNodePolling()/stopNodePolling()`：15 秒兜底轮询 `GET /nodes`，且只在这 15 秒内没收到 `node_status` 时才发请求 | 真后端从不周期推 `node_status`（B-021），拓扑/节点卡片指标不刷新；mock 模式因推送正常，不产生任何多余请求 |
| `frontend/src/layouts/AppLayout.vue:53` | 卸载布局时停轮询 | 防止退出登录后继续打请求 |
| `frontend/src/views/AssetsCenter.vue:42,229,242,292` | 资产表空态按角色给出说明「当前角色仅可见本人登记的数据资产」 | `energy_subject`/`edge_node` 的 scope=own（B-019），种子数据下列表为空，原来只显示"暂无数据"易被误判为故障 |
| `frontend/e2e/08-dispatch.spec.js:33,41-42` | 边端指令状态断言放宽为 `已下发\|issued\|已回执\|acked`，执行按钮存在才点 | 真后端数据持久，并发跑测时页面可能落在一条已回执的指令上（全量跑偶发 flake，单跑必过） |

### 2. 新增后端缺陷单
`BACKEND-ISSUES.md` 追加 **B-013 ~ B-019、B-024**（B-020/B-021 编号与 test-func 撞车，已把我这条 `node_status` 的内容并入其 B-021，WS 4001 那条改为 B-024）：
- **P1**：B-014 `POST /assets`、`POST /risk/assess` 并发下 500，且异常响应的 traceId 退化成 `tr-00000000-00000000`（无法追踪，违背契约 1.2）
- **P2**：B-013 `rotate-key` 强制要 body（前端已发 `{}` 兼容）、B-015 `/audit/trace` 对 FL/调度链路只回 1 步（契约 2.7 要求完整链）
- **P3/说明**：B-016 调度状态与签名字节、B-017 `brokenAt` 返回 evidenceId 字符串（前端已回查高度兼容）、B-018 `/audit/*` 角色门控与 R01 污染、B-019 资产 scope=own、B-024 WS 鉴权失败 403 而非 close 4001

### 3. 环境订正
`algo-service`(8100) 此前跑的是 `constraintEvents` 回退前的旧代码，与仓库源码不一致，已按 `INTEGRATION-ENV.md` 的命令重启；重启后 `contract_audit --algo-port 8100` 阻断级不一致 0。

### 4. 本轮回归（全部在真后端 + 真 algo 上跑）
| 套件 | 结果 |
|---|---|
| `qa/check_backend_live.sh`（新脚本，123 条断言） | **120 PASS / 3 FAIL**，3 条均为已备案的后端偏差：WS 无 token/错 token 握手 403（B-024）、8s 内无 `node_status`（B-021） |
| Playwright E2E 真后端 5199 | **28/28**（前两轮分别 27/28，失败项为并发 flake，已在断言层修好） |
| Playwright E2E mock 5300 | **28/28**（临时起的 5300 已按约定杀掉，只杀自己的 pid） |
| frontend vitest | 137/137 |
| algo pytest | 315/315 |
| `qa/contract_audit.py --algo-port 8100` | 阻断 0 / 警告 0 |
| `frontend/src/mocks/selfcheck.mjs` | 12/12 |
| `qa/deploy-sandbox/run.sh` | 39/39 |
| `qa/check_deploy.sh` | ALL PASS |

### 5. 真后端下各页面可用性结论
- **完整可用**：`/login`（6 账号 + 错误密码）、`/cloud/topology`（拓扑 + 流程横幅六项计数，指标靠轮询兜底刷新）、`/cloud/aggregate`（DQN 生成 → AI 解释 → 托管代签下发 → 越权 1003 演示）、`/edge/classification`、`/edge/privacy`（FL 创建/启动/收敛曲线/预算环）、`/edge/response`（验签步骤 + 回执）、`/identity`（注册/文档/冻结解冻/轮换/密钥/用户管理）、`/assets`（登记/分级/详情/溯源/统计）、`/permission`（申请→审批→授权→矩阵→校验器）、`/evidence`（检索/校验/篡改演示/断裂点/凭证/追踪）、`/audit`（统计图/检索/CSV/追踪/告警 ack/日报）。
- **受后端问题影响但不阻塞演示**：拓扑实时性（B-021，已轮询兜底）；`/audit` 全流程追踪对 FL 链路只有 1 步（B-015）；边端「验签」在真后端只能按 DID 文档状态判定（B-016，界面已如实标注）；并发高峰下资产登记/风险评估偶发 500（B-014）。
- **仅 mock 模式可演示**：梯度投毒检测（后端未透传 `simulatePoison`）。
