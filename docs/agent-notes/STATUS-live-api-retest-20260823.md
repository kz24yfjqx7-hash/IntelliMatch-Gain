# STATUS · live-api-retest · 2026-08-23（R03 修复后、后端 11:29 重启，pid 2934506）

所有套件**串行**跑在真后端 `127.0.0.1:8000` 上（北京时间 11:36–11:47）。临时文件与各套件完整日志在
`/tmp/claude-1002/-home-stu-yanxulong-Tzb/65f0c8ee-142f-469a-945b-159bbb6cf897/scratchpad/live-api/`。
未改 backend/、frontend/、docs/（本文件除外）；未 kill/重启任何进程；未执行 DDL。

## 一、套件结果

| # | 套件 | 结果 | results 文件 | 备注 |
|---|---|---|---|---|
| 1 | `qa/check_backend_live.sh` | **123/123** | 日志 `live-api/check_backend_live.log` | 脚本末尾按设计篡改 ev-002273 演示，已在第 7 步还原 |
| 2 | `qa/api-tests/run_all.py` | **245/245**（第一遍 244/245） | `qa/api-tests/results/run_all.json` | **API-AUD-23 通过** |
| 3 | `qa/api-tests/ws_test.py` | **13/13** | `qa/api-tests/results/ws.json` | |
| 4 | `qa/api-tests/perf.py` | 原脚本：**6/6 → 5/6**（两次，见下）；修正脚本后 **6/6、6/6** | `qa/api-tests/results/perf.json`（最后一次）；四次快照 `live-api/perf{1,2,3,4}.json` | API-PERF-06 定性为**脚本测量缺陷**，已修脚本 |
| 5 | `qa/api-tests/degrade_test.py` | **11/11** | `qa/api-tests/results/degrade.json` | 脚本缺陷一处已修（见下），5300/5301 副本已自行退出 |
| 6 | R03 专项 | **通过** | `live-api/r03.log` | 详见第三节 |
| 7 | 链还原 | `restore-all` → restoredCount 0（run_all 已自行还原）；`chain/status` **intact=true**，height 2478，brokenAt=null | | |

## 二、失败项 / 异常项定性

### API-PERF-06 「20 并发登录 P95<3000ms」——定性：**脚本问题（测量缺陷）**，非后端缺陷，也不是环境干扰
- 现象：单独串行跑（机器空闲，load 0.8）两次：第 1 次 P95 **2921 ms** PASS（差 79 ms 到阈值），第 2 次 P95 **3036 ms** FAIL。第一遍 3115 ms 也是同一量级——不是 pytest 并行的干扰，是稳定地贴着阈值抖。
- 证据链：
  1. 服务端 TraceMiddleware 日志里这 20 个登录 `cost=69–148 ms`，全部在 1.15 s 窗口内完成；但客户端每个请求量到 0.9–2.0 s。20 并发打**不存在的路由**（404，服务端 cost 1–8 ms，20 个 112 ms 内全部处理完）客户端也要 1.3–2.1 s ⇒ 延迟不在后端。
  2. `curl` 单发 404 total 2–4 ms；`seq 20 | xargs -P20 curl` 20 并发登录两次：**P95 274 ms / 294 ms**，20×200（`live-api/curl20.txt`）。
  3. 微基准（`live-api/httpx_overhead.log`）：共享 `httpx.Client` 2.0 ms/req；顶层 `httpx.get()`（每次新建 Client）**81.6 ms/req**；`httpx.Client()` 构造本身 78–93 ms（建 SSL 上下文，持 GIL）。
  4. 原 `perf.py::login_once` 在**计时区内、每线程各新建一个** `httpx.Client`：20 × ~85 ms 被 GIL 串行 ≈ 1.7–1.9 s 纯客户端开销计入"登录耗时"，加上 bcrypt/线程切换就落到 2.9–3.1 s。
  5. bcrypt 本身可并行（`bcrypt 4.0.1` 释放 GIL）：本机 20 线程 `ctx.verify` 墙钟 90 ms，排除密码校验串行。登录审计日志 `to_chain` 为否（audit_log_202608 该分钟 143 条 login 全部 evidence_id NULL），排除链尾行锁。
- 处理：改 `qa/api-tests/perf.py`（备份 `live-api/perf.py.orig`）：20 个 Client 在计时**之外**预建（仍是 20 条独立连接、20 线程并发），计时区内只剩 POST；阈值 3000 ms 未动。修后两次：**P95 252.6 ms / 284.2 ms**，与 curl 实测一致。
- 附带观察（不是缺陷）：登录单次服务端 ~70 ms ≈ bcrypt rounds=10 成本，PERF-01 P95 74.9 ms。

### API-DEG-xx 首次跑整段崩：`FileNotFoundError: .../scratchpad/degrade/algo-5301.log`——定性：**脚本问题**
- `degrade_test.py` 第 20 行 `LOG = os.environ.get("SCRATCH", "/tmp")` 后直接 `open(os.path.join(LOG, logname), "w")`，不创建目录；指定一个不存在的 SCRATCH 就在起副本前抛异常。
- 处理：加一行 `os.makedirs(LOG, exist_ok=True)`。复跑 11/11。

### API-AUD-23（第一遍失败项）——本轮 **通过**，R03 修复有效；但顺带指出该用例断言偏弱（未改）
- 用例只检查 `GET /audit/alerts?size=50` 里"存在任意 R03_PERM_CHURN"，不看 createdAt。本轮 run_all 跑到 AUD-23 时（≈11:38）距 11:31:43 的上一条 R03 告警不足 10 分钟，处于去重窗口，实际**没有**新告警产生（`audit_alert` 里 11:31:43 之后下一条是我 11:42:35 触发的 id 91），用例是靠历史行通过的。反过来若历史为空又恰在窗口内，它也会误报失败。建议后续主 Agent 决定是否改成"createdAt ≥ 本次触发前 N 秒"+ 触发前先确认主体不在窗口内（去重是设计内，不能靠放宽解决）。

## 三、R03 专项验证（11:42:34–11:42:36）
- admin 注册 DID `did:vpp:device:0xc75c452f62cd989a16ef74036d0323ad`（r03-retest）；资产 id **1148**（ev-002443）。
- subject 连续 5 次 apply → admin 立即 reject，逐次耗时（客户端墙钟，含 httpx 新建连接）：

| 次 | application id | apply | **reject** | http/code |
|---|---|---|---|---|
| 1 | 96 | 96 ms | **98 ms** | 200 / 0 |
| 2 | 97 | 93 ms | **92 ms** | 200 / 0 |
| 3 | 98 | 102 ms | **95 ms** | 200 / 0 |
| 4 | 99 | 93 ms | **93 ms** | 200 / 0 |
| 5 | 100 | 94 ms | **95 ms** | 200 / 0 |

  修复前第 5 次约 9 s（3 s 锁超时 × 3 次重试）；现在全部 <100 ms，且告警在窗口计数到 5 的那一刻（本次第 1 次 apply，因为窗口内还计入 run_all 11:38 的 5 次申请）就落库，没有再拖慢请求。
- `GET /audit/alerts?ruleCode=R03_PERM_CHURN&size=10` 首行：
  `id=91 alertId=al-000084 actorDid=did:vpp:user:0x4018374cd87a3d4a566223e8bbc2e852 hitCount=5 evidenceId=ev-002449 traceId=tr-20260823-99ebc728 status=open createdAt=2026-08-23T11:42:35+08:00 message="…最近一次为 apply"`
- 只读 SQL（`mariadb -uroot energy_tds`）：
  - `audit_alert`：`91 | al-000084 | R03_PERM_CHURN | hit_count 5 | evidence_id ev-002449 | 2026-08-23 11:42:35`（上一条 83/al-000076/ev-002270 11:31:43，是后端重启后主 Agent 验证留下的；再上一条 id 5 是 08-22 的 evidence_id NULL 旧行）。
  - `chain_evidence`：`id 2450 | ev-002449 | category audit | 2026-08-23 11:42:35` 存在（ev-002270 → id 2271 同样存在）。
- 后端日志 `grep '【风控告警】R03'`：02:58:15（修复前，库里无行）、03:31:43、**03:42:35**（UTC；= 11:42:35 北京时间）——三条日志中修复后的两条都有对应 `audit_alert` 行 + 链上存证，修复前那条仍是孤儿，印证 ENV 文件第 4 条的描述。
- 去重窗口：本次触发距上一条 10 分 52 秒，刚好出窗，故出了新告警；属于设计内行为。

## 四、链状态
- 跑完 `POST /evidence/demo/restore-all`（admin）：restoredCount 0（check_backend_live 篡改的 ev-002273 已被 run_all 自己的还原步骤恢复）。
- `GET /evidence/chain/status`：**intact=true**，height **2478**，brokenAt=null，byCategory data 204 / identity 315 / permission 230 / audit 1196 / algo 533（verifiedAt 11:47:26）。
- 5300/5301 降级副本进程已退出；8000 后端 pid 2934506 未动。

## 五、新的后端缺陷
**无。** 本轮两个失败项均定性为 qa 脚本自身问题（perf.py 测量把客户端 SSL 上下文构造算进去；degrade_test.py 不建日志目录），后端在 20 并发登录下真实 P95 ≈ 280 ms。

## 六、本轮对 qa/ 的改动
- `qa/api-tests/perf.py`：PERF-06 的 20 个 `httpx.Client` 移到计时区外预建（注释已写明原因），阈值不变。
- `qa/api-tests/degrade_test.py`：`LOG` 之后加 `os.makedirs(LOG, exist_ok=True)`。
