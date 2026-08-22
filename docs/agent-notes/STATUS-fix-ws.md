# STATUS · fix-ws（2026-08-22）

负责范围：B-021 / B-024 / B-022 / nginx 安全头 / 前端 E2E 收尾。

---

## 1. B-021（中）WebSocket 未按契约每 5 秒推 `node_status` —— 已修复

**改了什么**

| 文件 | 改动 |
|---|---|
| `backend/ws/manager.py` | 新增 `NODE_STATUS_INTERVAL=5.0`、`broadcast_node_status_once()`、`_node_status_loop()`、`start_node_status_task()` / `stop_node_status_task()`；在 `register_ws_endpoint()` 里挂 `@app.on_event("startup"/"shutdown")` |
| `backend/modules/node/service.py` | 新增 `tick_node_metrics()` / `_walk()` / `_ws_payload()`；`snapshot_for_ws()` 改为复用 `_ws_payload` |

- **payload 形状**严格按契约 2.13：`{nodeId, status, metrics:{pvOutput, storageOutput, load, soc}}`，一个节点一条消息，外层仍是统一的 `{type, ts, traceId, payload}`。
- **空转保护**：`broadcast_node_status_once()` 第一件事就是判 `manager.count == 0`，没有客户端时直接返回 0，**不开数据库会话、不改数据**。
- **生命周期**：任务注册写在 `ws/manager.py` 内部（`register_ws_endpoint` 里），沿用现有的 `@app.on_event`，没有动 `main.py` 的启动逻辑；shutdown 时 `task.cancel()` 并 `await` 到真正结束。注意 `register_websocket()` 在 `main.py` 模块级调用，所以我的 startup 钩子排在 `main.on_startup`（`wait_for_db`）**之前**执行——它只创建任务，任务第一件事是 `sleep(5)`，那时数据库早已就绪，没有竞态。
- 库操作走 `asyncio.to_thread`，不阻塞事件循环。单轮广播失败只 warning，不会让周期任务退出。

**数值变化的取法（这里做了取舍，说明如下）**

选了「**在库里的真实值上做有界随机游走并落库**」，而不是「读 `node_metric` 最新一条」。理由：

1. 演示环境没有真实硬件在上报，`node_metric` 是种子里 48 小时的固定历史，读最新一条每次都是同一串常数，推 100 遍前端曲线还是直线——那才是假的"实时"。
2. 游走结果**写回 `node_info` 并提交**，所以「WS 推出去的值 == 数据库里存着的值 == `GET /nodes` 读到的值」，三者永远一致，不是绕过持久层凭空编的展示数据。单测 `test_B021_指标步进落库且数值真的在变` 就断言了这一点。
3. 物理上自洽：步长上限是额定容量的 3%；`soc` 与 `storageOutput` 符号耦合（`storageOutput>0` 放电 → soc 降，`<0` 充电 → soc 升），不会出现"一边放电一边涨电量"；pv 夹在 `[0, capacity]`、load 夹在 `[0, 1.5×capacity]`、soc 夹在 `[5, 100]`。
4. `status == "offline"` 的节点**不动**（离线设备本来就不该产生新数据），但仍然照常推一条 `node_status`，前端能看到它还是离线。
5. **不往 `node_metric` 插新行**：每 5 秒 × 4 节点 = 每天 6.9 万行，会把 `/nodes/{id}/metrics` 的 7 天窗口顶过 `_MAX_POINTS=2000` 上限而变成 `truncated`，反而弄坏现有的历史曲线。历史沿用种子数据，实时值走 `node_info`。

前端 `frontend/src/stores/perspective.js:startNodePolling()` 的 15 秒兜底轮询**原样保留**，没有动——它本来就只在 15 秒内没收到 `node_status` 时才发请求，后端修好后自然不再触发。

---

## 2. B-024（P3）WS 鉴权失败返 403 而不是 4001 —— 已修复

- `backend/ws/manager.py`：两处 `close(code=4001)` 之前都补上了 `await websocket.accept()`。在 accept 之前 close，ASGI 服务器只能降级成 HTTP 403 握手拒绝，客户端拿不到关闭码。
- `frontend/src/api/ws.js`：`socket.onclose` 拿 `evt.code`，等于 `4001`（导出为 `WS_CLOSE_AUTH_FAILED`）时**不走退避重连**——置 `manualClose`、清重连定时器、`forceLogout()` 清会话并 `router.replace('/login?redirect=...')`。`forceLogout` 惰性 import `stores/user` 与 `router` 避免循环依赖，和 `src/api/request.js` 收到 `code 1002` 的处理完全一致。

---

## 3. B-022（低）30 并发登录尾延迟超 5 秒 —— 已修复，但**根因不是 bcrypt**

先摆实测数据（本机 80 核）：

| 测量 | 结果 |
|---|---|
| 单次 `verify_password`（cost=12） | 253ms |
| 单次 `verify_password`（cost=10，种子账号就是这个） | 63ms |
| 30 个线程并发跑 `verify_password` | **总墙钟 340ms，最大 328ms** |

也就是说 bcrypt 4.x（Rust 实现）在计算期间**释放 GIL**，30 路并行几乎线性，根本没有排队；而且 `/auth/login` 是**同步路由**（`def` 不是 `async def`），FastAPI 早就把它整个丢进 anyio 工作线程了，事件循环从来没被 bcrypt 堵过。原判读的「bcrypt CPU 密集 + 单进程 GIL 排队」与实测不符。

**真正的根因**：SQLAlchemy 连接池被打满。抓到的服务端日志：

```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 5 reached,
connection timed out, timeout 30.00
```

一个写操作请求会同时占**两条**连接——请求自身的 `get_db` 会话，加上 `@audited` 里 `write_audit_log()` 另开的审计会话。原来 `pool_size=5 / max_overflow=5` 只有 10 条，30 并发登录时后来者全部卡在 QueuePool 的 30 秒默认等待上，客户端表现就是 `ReadTimeout`。

**改动**

| 文件 | 改动 | 说明 |
|---|---|---|
| `backend/core/database.py` | `pool_size 5→10`、`max_overflow 5→20`、新增 `pool_timeout=8` | 上限 30 条，仍远低于 MariaDB 默认 `max_connections=151`；打满时 8 秒快速失败成 5000，而不是把客户端吊死半分钟 |
| `backend/core/security.py` | `CryptContext(..., bcrypt__rounds=10)` | passlib 默认 cost 是 12，而 `sql/02_seed.sql` 六个演示账号的哈希是 cost=10 生成的——不钉住的话**新建用户的登录比演示账号慢 4 倍**。校验时 cost 从哈希串 `$2b$NN$` 自身读出，老哈希照样验得过，只影响新生成的 |
| `backend/core/security.py` | 新增 `verify_password_async()`（`anyio.to_thread.run_sync`） | 当前同步路由用不到，留给以后可能出现的 `async def` 鉴权路径，免得有人在协程里直接调 `verify_password` 把事件循环堵死；`verify_password` 的 docstring 里写清了为什么同步路径不需要再套一层线程池 |

> `core/database.py` 不在我的独占文件表里（该表里没人认领它），但 B-022 的根因就在这一行上，只改了 `create_engine` 的三个池参数 + 模块 docstring，请主 Agent 知悉。

**改后实测**（我自己的 8014 实例，与 `qa/func-tests` 的 TC-NF-02 同一写法：30 线程 × `httpx.post /auth/login`，6 个演示账号轮流）：

```
修复前：run1 codes=["ReadTimeout('timed out')"] wall=33349ms max=33330ms   ← 30/30 全部超时
修复后：run1 codes=['200'] wall=3558ms max=3543ms p50=3464ms min=3371ms
        run2 codes=['200'] wall=3476ms max=3446ms p50=3334ms min=2907ms
```

**30/30 全 200，最大 3543ms < 5000ms 门槛，TC-NF-02 判定通过。**

补充：这 3.5s 里服务端只占约 0.6s——服务端 30 条 `POST /api/v1/auth/login` 日志的时间戳从 `15:42:12,052` 到 `15:42:12,610`，单条 `cost=` 最大 211.5ms。剩下的是**测量客户端自身的开销**（TC-NF-02 每次调用都新建一个 `httpx` Client，30 个 Python 线程抢 GIL）。真实浏览器不会这么用，所以实际尾延迟比这个数字还好看。

---

## 4. nginx 安全头 —— 已补，且 Playwright 实测通过

`deploy/nginx.conf` 与 `frontend/nginx.conf` 两份保持完全一致（`diff -q` 通过）。新增：

- `server_tokens off;` —— 响应头实测从 `Server: nginx/1.x.y` 变成 `Server: nginx`。
- **CSP**（server 级 + 三个自带 `add_header` 的 location 里各重复一份，因为 nginx 的 `add_header` 不跨层继承）：
  ```
  default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; form-action 'self';
  script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:;
  font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; manifest-src 'self'
  ```
  逐条理由写在配置文件的注释里。要点：`script-src` **没有**放开 `unsafe-inline`/`unsafe-eval`（构建产物是 ES module 外链，`index.html` 里没有内联 `<script>`）；`style-src` 必须留 `'unsafe-inline'`（`index.html` 有一段内联 `<style>`，且 Element Plus / ECharts 运行时写行内样式，去掉直接白屏）；`connect-src 'self'` 已经覆盖同源的 `ws://`（CSP3 规定 `'self'` 匹配同源 ws/wss），所以**没有**放宽成 `ws: wss:` 那种任意主机写法——实测 WebSocket 连得上。
- **HSTS 条件化**：`map $scheme $hsts_header { https "max-age=31536000; includeSubDomains"; default ""; }` + `add_header Strict-Transport-Security $hsts_header always;`。nginx 遇到空字符串值会跳过该头，所以当前 http 演示环境这个头**不出现**（实测 `curl -I` 确认），将来接了 TLS 自动生效。不用 Report-Only。

**顺带修掉一个真实的部署缺陷（Playwright 实测发现）**：`location /` 原来是 `try_files $uri $uri/ /index.html`，而前端有一个 `/assets` 路由（资产中心），构建产物里恰好也有一个 `assets/` 目录 —— `$uri/` 让 nginx 对 `/assets` 发 301 到 `/assets/`，落进 `location /assets/` 后被 `try_files $uri =404` 打成 **404，资产中心页面在 nginx 后面根本打不开**（vite dev server 上不复现，所以之前一直没暴露）。改成 `try_files $uri /index.html;`，静态目录各自有 location 兜着，SPA 不需要目录索引。

**验证方式**（本机有 `/usr/sbin/nginx`，不需要降级成 Report-Only）：

```
VITE_USE_MOCK=false npx vite build --outDir <scratchpad>/dist-nginx
nginx -c <scratchpad>/nginx-test/nginx.conf     # 由 deploy/nginx.conf sed 改端口 18099 + 指向 8014 后端
```

Playwright（Chromium）实测结果：

```
登录页标题: 登录 · 能源可信数据空间平台
登录后 URL: http://127.0.0.1:18099/cloud/topology
  /cloud/topology  echarts=3  csp违规=0
  /identity        echarts=0  csp违规=0
  /assets          echarts=2  csp违规=0     ← 修 try_files 前这里是 404
  /permission      echarts=0  csp违规=0
  /evidence        echarts=1  csp违规=0
  /audit           echarts=3  csp违规=0
  /cloud/aggregate echarts=3  csp违规=0
浏览器侧收到 node_status 帧数: 24
CSP 违规: 无
控制台 error: 无
```

即：**通过 nginx 提供的页面能正常加载、能登录、7 个页面图表全部渲染、零 CSP 违规、零控制台错误，WebSocket 经 nginx 反代拿到 24 条 node_status。**

**QA 脚本**（断言已同步更新）：

```
bash qa/check_deploy.sh          → == deploy check: ALL PASS ==
bash qa/deploy-sandbox/run.sh    → 场景 39：PASS 39，FAIL 0
```

新增/改动的断言：两份 nginx.conf 的 `server_tokens off` / CSP 存在性 / `connect-src 'self'` / `script-src` 未放开 `unsafe-*` / HSTS 必须走 `map $scheme $hsts_header` 条件化 / 三个 location 内 CSP+HSTS+Referrer-Policy 都要重复一遍；SPA `try_files` **不得**带 `$uri/`（带了就 fail，附带说明 `/assets` 会 404）。

---

## 5. 前端 E2E 断言收紧

- `frontend/e2e/10-audit.spec.js:34` traceId 链路步数断言：**`≥1` 改回 `≥3`**（契约 2.7 的 auth/did/permission/algo/evidence 多步链）。
  **尚未验证** —— 需要等 fix-algo 修完 B-015（`/audit/trace/{traceId}` 复用同一 traceId）后由主 Agent 统一回归。若届时仍不满足，说明后端修复没覆盖 FL/调度链路，我会写 MSG 给 fix-algo。
- 另外两处（种子数据量、pending 分页）检查过，本来就是对 mock / 真后端两种模式都成立的写法，**未改动**。
- `frontend/e2e/08-dispatch.spec.js:45`（回执存证 `ev-|执行完成` 的 OR 写法）属 B-026，归 fix-algo，也是两种模式都成立，未动。

---

## 6. 后端单测

新增（全部在 `backend/tests/test_websocket.py`，用例名带缺陷编号）：

| 用例 | 验什么 |
|---|---|
| `test_B024_鉴权失败先完成握手再以4001关闭` | 服务端确实先 accept 了（不是 403 握手拒绝），随后才是 4001 关闭帧 |
| `test_B021_广播间隔是契约要求的5秒` | `NODE_STATUS_INTERVAL == 5.0` |
| `test_B021_没有连接时不打库也不改数` | 无连接时 `broadcast_node_status_once()` 返回 0，`_collect_node_status` 一次都没被调用，`node_info` 数值不变 |
| `test_B021_指标步进落库且数值真的在变` | payload 形状符合契约；两轮之间数值有变化；广播值 == 库里的值；soc/pv 在物理边界内 |
| `test_B021_已连接客户端能收到node_status` | 真跑一轮广播，已连接客户端收到形状正确的 `node_status` |
| `test_B021_后台任务能随应用生命周期启动与关闭` | 启动幂等、cancel 后 `task.done()`、重复 stop 不报错 |

原有的 `test_没有token直接拒绝` / `test_伪造token直接拒绝` 因为改成先 accept 后 close，断言改为「连上后 `receive_text()` 抛 `WebSocketDisconnect(4001)`」。

`cd backend && .venv/bin/python -m pytest -q` 全绿（详见最后一节）。

---

## 7. 真实环境验证（自起 8014 实例，未碰共享的 8000）

```
cd backend && DEBUG=false .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8014
```

用 `.venv/bin/python` + `websockets` 库连上去，实际输出：

```
A. 无 token → close code=4001 reason='缺少 token'
B. 错误 token → close code=4001 reason='鉴权失败'
C. 正确 token：16 秒内收到 node_status 12 条
   + 3.37s  {"type": "node_status", "ts": "2026-08-22T23:43:16+08:00", "traceId": null, "payload": {"nodeId": "Node-A", "status": "online", "metrics": {"pvOutput": 42.48, "storageOutput": -17.75, "load": 122.25, "soc": 65.09}}}
   + 3.37s  {"type": "node_status", "ts": "2026-08-22T23:43:16+08:00", "traceId": null, "payload": {"nodeId": "Node-B", "status": "online", "metrics": {"pvOutput": 27.96, "storageOutput": 8.68, "load": 87.45, "soc": 77.96}}}
   + 3.37s  {"type": "node_status", "ts": "2026-08-22T23:43:16+08:00", "traceId": null, "payload": {"nodeId": "Node-C", "status": "warning", "metrics": {"pvOutput": 26.67, "storageOutput": -32.88, "load": 147.26, "soc": 42.11}}}
   + 3.37s  {"type": "node_status", "ts": "2026-08-22T23:43:16+08:00", "traceId": null, "payload": {"nodeId": "Node-D", "status": "online", "metrics": {"pvOutput": 35.47, "storageOutput": 0.21, "load": 90.28, "soc": 82.0}}}
   + 8.38s  {"type": "node_status", "ts": "2026-08-22T23:43:21+08:00", "traceId": null, "payload": {"nodeId": "Node-A", "status": "online", "metrics": {"pvOutput": 45.43, "storageOutput": -12.46, "load": 117.18, "soc": 65.15}}}
   涉及节点： ['Node-A', 'Node-B', 'Node-C', 'Node-D']
   Node-A 前后两次 pvOutput: 42.48 -> 43.96, soc: 65.09 -> 65.21
```

16 秒内 4 节点 × 3 轮 = 12 条（要求 ≥2），间隔 5.0 秒，payload 与契约 2.13 一致，数值逐轮变化。验证完已 kill 掉 8014 的 pid。

---

## 8. 迁移脚本 / 遗留

- **不需要执行任何数据库迁移**：没有改表结构，也没有改 `sql/01_schema.sql` / `02_seed.sql`。
- `node_info` 的四个指标列会在有 WS 客户端连着时被周期改写。这是设计内行为（见第 1 节），但联调库里 Node-A~D 的指标已经不再是种子里的初值，其它 Agent 若有按固定值断言的用例需要注意。
- `frontend/e2e/10-audit.spec.js` 的 `≥3` 断言依赖 fix-algo 的 B-015，**待主 Agent 统一回归**。
- `backend/core/database.py` 的连接池参数是跨归属改动（见第 3 节），请主 Agent 在汇总时留意是否与他人改动冲突。
