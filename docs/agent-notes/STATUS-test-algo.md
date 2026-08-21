# STATUS-test-algo（2026-08-21）—— algo-service 对抗/边界测试与修复

测试方式：真实 uvicorn（:8137/:8138，测完已按 pid 杀掉）+ httpx/raw socket 探针脚本，以及 TestClient 内嵌测试。
结论：**发现 12 类问题，全部已修**；契约字段/语义不变；qa 原 70 用例全绿；新增对抗用例 244 个（2 个文件），总计 **314 passed**。
命令：`cd algo-service && ../.venv/bin/pytest -q tests`

## 问题清单（严重级别 / 已修）

| # | 级别 | 问题 | 复现 | 修复文件:行 | 验证 |
|---|---|---|---|---|---|
| 1 | P1 | `/dqn/dispatch` 传 `NaN`/`Infinity`/`1e308` → **500**（JSON 序列化 nan/inf 失败） | `POST /dqn/dispatch` body `{"nodes":[{"id":"a","soc":NaN}]}`（Python json 接受 NaN 字面量）或 `pv:1e308` → qValue 6e306、多节点求和溢出 | `main.py` DqnNode 字段加 `allow_inf_nan=False` + 物理范围（soc 0~100、pv/load/storage ±1e6、price 0~1e4、hour 0~23）；`algorithms/dqn.py` `_sanitize_node()` 在 `dispatch()` 内再做一次有限值/裁剪（防直接调用） | 400 `{"error"}`；直接调用 `DQNDispatcher.dispatch` 传 1e308/nan 也可 `json.dumps(allow_nan=False)`；`test_dqn_rejects_nan_inf`、`test_dqn_dispatcher_sanitizes_direct_input` |
| 2 | P1 | `/deepseek/analyze` 脏 `context`（`nodes:"abc"`、`nodes:[1]`、`load:"abc"`、`factors:[1]`、`identityOps:"x"` 等）→ **500**，违反"永远 200" | 9 种 body，见 `test_deepseek_dirty_context_always_200` | `adapters/deepseek.py` 新增 `_num/_dicts/_dict` 宽松取值、`rule_answer()` 外包 try/except 退化为通用说明、`analyze()` 外包 try/except 永远返回 `source=rule` | 9 种脏 context 均 200 且 reasoning ≥2 |
| 3 | P1 | `/classify` `volume:"abc"`/`[1]`/`NaN` → **500** | `{"records":[{"volume":"abc"}]}` | `algorithms/classifier.py` `_to_num()`（非数值/NaN/Inf→0）、`fields` 非列表容错 | 200，level/score 合法；`test_classify_dirty_record_ok` |
| 4 | P1 | `/risk/assess` `queryFreq:"abc"`/`[1]`/`NaN`、`epsilonRemaining:"x"` → **500**；`queryFreq:-100` 返回 factor score **-500** | `{"features":{"queryFreq":"abc"}}` | `algorithms/risk.py` `_to_num()`（非数值→默认，负数→0）；`main.py` `features` 允许 `null` 视为 `{}` | 200，factor score 均在 0~100；`test_risk_dirty_features_ok` |
| 5 | P2 | FL `dp.epsilon` 0/负/NaN、`delta` 0/≥1 被接受（静默夹到 1e-6 / 1e-12 训练，曲线发散且必报 budget_exhausted） | `dp:{"enabled":true,"epsilon":0}` → 200 running | `main.py` DpCfg：`epsilon gt=0 le=1e6`，`delta gt=0 lt=1`，`allow_inf_nan=False`；TopkCfg ratio 同样禁 NaN | 400 `{"error":"参数错误: dp.epsilon: ..."}`；`test_fl_train_rejects_bad_body` |
| 6 | P2 | 无长度/数量上限：200KB `jobId`、100KB `nodeId`、200 个 FL 节点、1MB `question` 均被接受（内存/CPU 放大） | 见 probe | `main.py`：id 类字段 ≤128、FL nodes ≤64、DQN nodes ≤500、records ≤5000、question ≤20000、scene ≤64、timeWindow ≤128 | 400；用例同上 |
| 7 | P2 | `JOBS` 任务字典无限增长；并发训练无上限 | 连发 210 个任务 → 字典 231 项，线程数随并发训练线性增长 | `main.py` `_prune_jobs_locked()`（终态任务 TTL `FL_JOB_TTL`=3600s + 上限 `FL_MAX_JOBS`=200，只淘汰终态、最旧优先）；并发训练 > `FL_MAX_RUNNING`(16) → **429** `{"error"}`；`config.py` 三个新环境变量 | 210 个任务后字典稳定在 200；`test_job_registry_is_bounded`、`test_max_running_returns_429` |
| 8 | P2 | `X-Trace-Id` 任意字节原样回写：5000 字符、含空格、UTF-8/latin-1 非法字节都透传到响应头 | raw socket 发 `X-Trace-Id: tr-\xe4\xb8\xad` | `main.py` `_sanitize_trace_id()`：仅接受可见 ASCII 1~128 字符，否则服务自行生成 `tr-YYYYMMDD-xxxxxxxx`（缺失时同样生成） | 合法 id 原样回写（含 `-_.:/`），非法/缺失时生成；400 响应也带 trace id；`test_trace_id_generated_when_missing_or_invalid` |
| 9 | P2 | `cache/deepseek_cache.json` 损坏时服务能启动但**不自愈**：每个请求都重新解析失败并刷 WARNING | 写入 `{"broken": ` 后启动 :8138，每次 analyze 都告警 | `adapters/deepseek.py` `load_cache()`：损坏（含根不是对象）→ 备份为 `deepseek_cache.corrupt-<ts>.json` 并重写 `{}`，只告警一次 | `test_deepseek_corrupt_cache_self_heals` |
| 10 | P3 | live 回答缓存无上限（每个不同 context 一条）；写文件非原子（写一半可能被并发请求读到） | 持续 live 调用 | `save_cache_entry()`：超过 `DEEPSEEK_CACHE_MAX`(2000) 淘汰最旧精确 key（`scene:*` 兜底永不淘汰）；tmp+`replace` 原子写 | `test_deepseek_cache_bounded` |
| 11 | P3 | live 响应 `content` 非字符串/空串时靠 AttributeError 兜底 | fake server 返回 `content:123` | `call_live()` 显式校验 content 为非空字符串 | `test_deepseek_malformed_live_response`（5 种畸形响应均降级 rule） |
| 12 | P3 | `dqn.train()` 用 `open()` 不关闭写训练日志（ResourceWarning） | 代码审查 | `algorithms/dqn.py` 改为 `with open(...)` | — |

## 各维度实测记录（未修的项 = 行为已正确）
1. **非法/极端输入**：83 个探针 request 修复前 16 个 500，修复后 0 个 500，全部 400 `{"error"}` 或合法 200。`rounds=1/500`、`samples=0/重复 nodeId/未知 nodeId/1e7` 合法处理并 success。非 JSON / 空 body / 数组 body → 400。
2. **并发**：10 任务并发创建 + 8 线程×50 次轮询（最大延迟 40ms，平均 18ms）+ 5 个并发 cancel → 5 cancelled/5 success；cancel 后 `thread.is_alive()==False`；OS 线程数在 50 个任务前后保持 70（anyio 线程池基线），**无线程泄漏**；立即 cancel 的竞态最终也为 cancelled；cancel 后同 id 可重提。
3. **DQN**：soc∈{0,19.9,20,20.1,50,94.9,95,95.1,100} × pv/load 4 组 × price 5 档 × hour 3 个 = 660 状态，`powerKw` 全在 0~30、动作合法、SOC≤20 从不 discharge、SOC≥95 从不 charge、violations 结构固定；1/20 节点全在边界时 violations 数 == 节点数；同一 20 节点输入推理 20 次结果完全一致；20 节点推理 1.3ms。`numpy` 在 `simplefilter("error")` 下无任何 RuntimeWarning（修复前 1e308 输入会刷 `invalid value encountered in matmul`）。
4. **classify/risk**：100 条 2.6ms；100 条完全相同特征 k-means 不崩（退化簇，3 个中心）；1/2/5/6/7 条同样正常；score→level 在 81 组合上单调；risk 在 400 组合上 level 与 score 单调一致（3.8~98.8）。
5. **DeepSeek**：连接拒绝 0.16s、挂起/12s 慢响应 8.09s 降级（`latencyMs` 8086 真实）、非 JSON/无 choices/content 非字符串/HTTP 500 均降级；5 个并发挂起请求 8.45s 全部降级；**真实 key live 路径**：单次 3.2s `source=live`，5 并发全部 live（2.4~3.7s）；无 key 时 health 为 `cache`。注意：httpx 的 timeout 是按 connect/read 各阶段计，极端情况（连接慢+读慢）总时长可能略超 8s，但任一阶段 ≤8s。
6. **X-Trace-Id**：见 #8。
7. **启动健壮性**：删除 `models/dqn.npz` + `data/node_datasets.npz` + 写坏缓存后启动 :8138 → 6s 内 health ok（--quick 训练），dispatch/analyze 可用；测后 `git checkout` 恢复原训练产物（已核对 41010 字节）。
8. **代码审查**：无裸 `except:`（均为 `except Exception` + 记录日志）；httpx 用 `httpx.post` 一次性调用无 client 泄漏；日志不含 key（失败日志只含 URL）；源码/测试无真实 key；`@app.on_event` 为 FastAPI 弃用告警（3 条 DeprecationWarning），功能无影响，未改动。

## 新增回归用例
- `algo-service/tests/test_adversarial_inputs.py`：78 个（参数化）——非法/极端入参。
- `algo-service/tests/test_adversarial_runtime.py`：166 个——并发/取消/上限/429、trace id、DQN 网格约束与确定性、classify/risk 退化与单调、DeepSeek 不可达/超时/畸形响应/缓存自愈/缓存上限。
- 全部 DeepSeek 用例通过 monkeypatch `DEEPSEEK_CACHE_PATH` 指向临时目录，不污染仓库缓存文件。

## 对其它 Agent 的影响（deploy / backend 调用方）
- 新增可选环境变量（均有默认值，不改 compose 也可）：`FL_MAX_JOBS=200`、`FL_JOB_TTL=3600`、`FL_MAX_RUNNING=16`、`DEEPSEEK_CACHE_MAX=2000`。
- 新增错误码 **429**（并发训练超限），格式仍为 `{"error"}`。
- 入参新增硬校验（原本会 500 或静默夹值的情况现在 400）：DQN `soc` 0~100、`hour` 0~23、数值必须有限；DP `epsilon>0`、`0<delta<1`；id 类字段 ≤128 字符。正常调用不受影响。
