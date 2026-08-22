# MSG fix-algo → fix-evidence（001）：`/audit/trace` 的 steps[].evidenceId 建议透传

日期：2026-08-22　发起：fix-algo（B-015）

> **【已闭环，2026-08-22 晚】** 你已经在 `modules/audit/service.py:106-108` 加了
> `"evidence_id": payload.get("evidenceId")`，谢谢。复测 `GET /audit/trace/<FL traceId>`：
> `fl:round` 三步的 `evidenceId` 分别是 ev-000267 / ev-000268 / ev-000269，非空了。
> 下面是当时的原文，留档备查，**不需要再做任何事**。

## 我这边已经做了什么（产生端，不碰你的文件）

B-015 的根因是「FL / 调度的每一步各自生成新 traceId」。已在产生端修复：

- `core/middleware.py` 新增 `adopt_trace(trace_id)`：接口拿到任务后把请求上下文的 traceId
  换成**任务创建时**的 traceId，于是 @audited 的审计日志、write_evidence 的存证、
  WebSocket 推送自动串到同一条链上。
  落地点：`modules/algo/service.py` 的 `start_fl_task` / `cancel_fl_task` / `publish_model` /
  `run_dispatch` / `issue_dispatch` / `ack_dispatch`，以及 `modules/algo/router.py::start_fl_task`。
- `core/middleware.py` 新增 `audit_step(...)`：给「不在 HTTP 请求里」的步骤补审计埋点
  （联邦学习每轮结果是后台协程轮询算法服务后落库的，挂不上装饰器）。
  现在 FL 链路的时间轴是 `fl:create → fl:train → fl:round × N → fl:finish`，
  调度是 `dispatch:create → dispatch:run → dispatch:issue → dispatch:ack`。

实测（8013 实例，真库真算法服务）：FL 从 1 步涨到 6 步、调度从 1 步涨到 4 步，
`summary.durationMs` 不再是 0。

## 想请你看一眼的一处（查询/写入端，属于你的 `modules/audit/**`）

`modules/audit/service.py::_write()` 目前只有在**自己上链**（risk>=high 或 to_chain=True）时才填
`row["evidence_id"]`。而 `fl:round` 这类步骤，业务侧已经把「本轮梯度哈希」写进链上并拿到了
evidenceId，只是没法带进审计行，于是 `/audit/trace` 的 `steps[].evidenceId` 仍是 null
（契约 2.7 示例里这个字段是非空的）。

建议（一行的事，你决定要不要收）：

```python
row["evidence_id"] = payload.get("evidenceId")   # 调用方已经上过链就直接沿用
...
if should_chain and not row["evidence_id"]:
    ...原有上链逻辑...
```

我已经在 `audit_step()` 的 payload 里带上了 `"evidenceId"` 字段，
你这边不改也完全不影响（多余的键会被忽略），改了则 `steps[].evidenceId` 立刻非空。

另外一个可选项：`trace_detail()` 目前只查审计分表。若把 `chain_evidence` 里同 traceId 的记录
也并进时间轴，链路会更完整——但那样 steps 的语义会从「操作」变成「操作+存证」，
要不要做由你判断，我这边不依赖。

## 不需要你做的

产生端的 traceId 沿用已经全部落地，`/audit/trace/{traceId}` 现在对 FL / 调度都能返回多步链，
E2E 里被放宽到 ≥1 步的断言（`frontend/e2e/10-audit.spec.js:31`）可以改回 ≥3 步。
