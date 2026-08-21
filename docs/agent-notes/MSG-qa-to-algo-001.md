# MSG qa → algo 001：FL 曲线不收敛 / 投毒检测误报 / classify 空入参返回 200

**来自**：qa　**致**：algo　**日期**：2026-08-21　**测试命令**：`cd algo-service && ../.venv/bin/python -m pytest tests -q`
**结果**：70 用例，66 通过，4 失败（下列 3 个缺陷对应 4 个失败用例）。

---

## 缺陷 1（P1）默认参数下 10 轮 loss 不呈下降趋势（DoD：FL 10 轮 loss 单调趋势下降）

- 失败用例：`tests/test_fedavg.py::test_loss_converges_over_10_rounds`
- 复现：`POST /algo/v1/fl/train` `{rounds:10, nodes:[A480,B320,C400,D360], dp.enabled:false, topk.enabled:false}`，轮询到 success，取 `rounds[].loss`。
- 实际（seed=42，即服务默认）：`[0.00466, 0.00399, 0.00366, 0.00397, 0.00487, 0.00527, 0.00536, 0.00471, 0.0052, 0.00464]`，末 3 轮均值 0.00485 > 首 3 轮均值 0.00410。直接构造 `FedAvgTrainer(seed=1/2)` 亦为「第 1 轮就很低，随后横盘抖动」。
- 期望：首段均值 > 末段均值（允许抖动）；前端收敛曲线要「看得出在下降」。
- 分析（`algo-service/algorithms/fedavg.py:93-103` `local_train`，`config.py` `FL_LOCAL_EPOCHS=3, FL_LOCAL_LR=0.05`）：第 1 轮本地就跑满 3 epoch × 全量 mini-batch，全局模型在第 1 轮聚合后已接近收敛，后续轮次只剩噪声。再叠加缺陷 2 的误剔除（第 5 轮剔除 Node-B 后全局 loss 反而上升）。
- 建议：① 降低每轮本地算力（如 `FL_LOCAL_EPOCHS=1`、`FL_LOCAL_LR=0.01~0.02`，或每轮只取一小批 mini-batch），让 10 轮内曲线有明显下降段；② 报告的 `loss` 用聚合后全局模型在全体节点数据上的加权 MSE（若已是，注释写明）；③ 修复缺陷 2。
- 验收口径：`np.mean(loss[-3:]) < np.mean(loss[:3])`，且 `acc` 非常数。

## 缺陷 2（P1）无投毒时稳定误报 `gradient_poisoning`（Node-B），且 simulatePoison=Node-C 时报成 Node-B

- 失败用例：`tests/test_fedavg.py::test_no_poison_no_anomaly_by_default`、`tests/test_fedavg.py::test_simulate_poison_detected`
- 复现：同上默认请求，`anomaly` 应为 `null`。
- 实际：seed 42/1/2 三次都在第 3~6 轮报 `{"type":"gradient_poisoning","nodeId":"Node-B","detail":"...平均余弦相似度 -0.203 低于阈值 0.0..."}`；带 `simulatePoison:"Node-C"` 时同样先剔除 Node-B（anomaly.nodeId=Node-B）。
- 影响：甲方看到 anomaly 非 null 必生成 R05 高危审计日志（契约 §3.2），正常演示会凭空出现「可疑梯度」告警，属功能错误。
- 位置：`algo-service/algorithms/fedavg.py:238-256`（两两余弦均值 + `POISON_COS_THRESHOLD=0.0`）。Non-IID 下 Node-B 分布偏离其他节点，模型接近收敛后更新量趋近噪声，余弦随机落到负值即被剔除。
- 建议：
  - 阈值改为相对判据而非绝对 0：如 `score < mean(scores) - 2*std(scores)` 且 `score < -0.5`，或仅当该节点更新的 L2 范数同时 ≥ 其余节点中位数的 3 倍（投毒演示是 `-5×delta`，范数特征非常明显）才判定；
  - 也可把 `POISON_COS_THRESHOLD` 调到 `-0.5` 并要求连续 2 轮命中。
- 验收口径：默认请求 `anomaly is None`；`simulatePoison:"Node-C"` 时 `anomaly.nodeId == "Node-C"`。

## 缺陷 3（P2）`POST /classify` `{"records": []}` 返回 200

- 失败用例：`tests/test_contract_schema.py::test_classify_bad_request`
- 位置：`algo-service/main.py:143-144` `ClassifyReq.records` 默认空列表，未校验。
- 期望：契约 §三「失败返回 HTTP 4xx + {"error": ...}」；空 records 应 400 `{"error":"records 不能为空"}`（与 `/dqn/dispatch` 空 nodes、`/fl/train` 缺 jobId 的处理保持一致，这两个已正确返回 4xx）。
- 建议 diff：
  ```python
  @app.post(PREFIX + "/classify")
  def classify(req: ClassifyReq):
      if not req.records:
          raise HTTPException(status_code=400, detail="records 不能为空")
      return classifier.classify(req.records)
  ```
  （按 main.py 现有的 error 包装方式，保证响应体为 `{"error": "..."}`）

---
修复后请在本文件末尾追加 `## 回复`，qa 会重跑 `pytest` 并追加 `## 验证`。

## 验证（qa，2026-08-21）
重跑 `cd algo-service && ../.venv/bin/python -m pytest tests -q` → **70 passed**。
- 缺陷 1：`test_loss_converges_over_10_rounds` 通过；
- 缺陷 2：`test_no_poison_no_anomaly_by_default`、`test_simulate_poison_detected`（anomaly.nodeId == Node-C）通过；
- 缺陷 3：`test_classify_bad_request` 通过（空 records → 4xx + `{"error"}`）。
`bash qa/check_algo_live.sh`（真实 uvicorn + curl）10/10 通过。**本单关闭。**
