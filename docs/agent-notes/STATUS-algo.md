# STATUS-algo（2026-08-21）

## 已完成
- [x] `algo-service/config.py`、`main.py`（FastAPI，`/algo/v1`，X-Trace-Id 中间件，统一 `{"error"}`，pydantic 请求模型字段与契约一致）
- [x] `algorithms/fedavg.py`（FedAvg + NumPy MLP 8-16-1 + 余弦相似度投毒检测 + SHA256 梯度哈希）
- [x] `algorithms/dp.py`（自适应 L2 裁剪 + 服务端高斯噪声 + 简化矩会计 ε 累计 + 超预算 anomaly）
- [x] `algorithms/topk.py`（Top-k + 残差缓存 + 真实压缩率）
- [x] `algorithms/dqn.py` + `scripts/train_dqn.py`（环境/Q 网络/经验回放/目标网络/Adam 手写；**完整训练已跑**：3000 episodes，72000 步，57s，平均奖励 −118 → +52；产物 `models/dqn.npz`、`models/dqn_train_log.json`）
- [x] `algorithms/classifier.py`（k-means k=3 + 规则加权 L1~L4）、`algorithms/risk.py`（4 因子，权重和 1，ε 建议）
- [x] `adapters/deepseek.py`（live→cache→rule；`scripts/warm_cache.py` **已跑**，无 key 模式灌入 8 条样例 + 5 个场景兜底 key，共 13 条）
- [x] `scripts/gen_datasets.py` → `data/node_datasets.npz`（4 节点 Non-IID，480/320/400/560 样本 + 400 条全局测试集）
- [x] `Dockerfile`（python:3.11-slim，非 root，EXPOSE 8100，模型/数据/缓存兜底生成）、`requirements.txt`、`README.md`
- [x] qa 的 `algo-service/tests` 全绿：`70 passed in 2.97s`

## 自测（venv 启动 uvicorn，curl 打通 6 个接口）
```
curl -i -H 'X-Trace-Id: tr-20260821-abcdef01' :8100/algo/v1/health
  → x-trace-id 回写；{"status":"ok","version":"1.0.0","models":{"dqn":"loaded","deepseek":"cache"}}
POST /fl/train (4 节点, dp ε=1.0 δ=1e-5, topk 0.1, 10 轮)  → {"jobId":"fl-000012","status":"running"}
  重复提交 → 409 {"error":"任务 fl-000012 正在运行"}
GET /fl/jobs/fl-000012（每轮约 1s）：
  loss: 0.0902 0.0158 0.0185 0.0205 0.0077 0.0113 0.0082 0.0100 0.0057 0.0044（DP 噪声下整体下降）
  epsilonSpent: 0.316 → 1.000（第 10 轮恰好用满）；compressionRatio 90.06；modelVersion "v12"；anomaly null
  无 DP/Top-k 对照（fl-000020）：loss 0.00453 → 0.00316 单调下降，acc 0.46 → 0.57
  simulatePoison=Node-C：第 1 轮 anomaly gradient_poisoning nodeId=Node-C（余弦 −0.965），其权重置 0
  ε=0.2：第 3 轮 anomaly privacy_budget_exhausted
POST /fl/jobs/{id}/cancel → {"status":"cancelled"}
POST /dqn/dispatch (4 节点, Node-D soc=15) 耗时 ~50ms：
  Node-C discharge 17.0kW（负荷最高，SOC充足，处于峰时电价）；Node-D charge 30kW，
  violations=[{nodeId:Node-D, constraint:socMin, attempted:charge, applied:charge, detail:"SOC=15.0% 触发下限约束，禁止 discharge；…"}]
  soc=97 节点 → constraint socMax 记录
POST /deepseek/analyze scene=dispatch → source=cache，reasoning 5 条（用 context 数值填空）
POST /classify → gps+minute 记录 L4 score 0.72；clusterCenters 3×3
POST /risk/assess → riskScore 60.2 high，4 因子权重 0.35/0.25/0.20/0.20，建议 ε 1.0→0.49
参数错误 → 400 {"error":"参数错误: nodeId: Field required"}；不存在任务 → 404
```
测试完成后后台进程已杀掉。

## 对外约定
见 `MSG-algo-to-all-001.md`。

## 已知问题 / 说明
- DP 开启且 ε ≤ 0.3 时噪声乘子触顶（σ_max=2.0），曲线会震荡甚至发散——这是"预算过小"的真实表现，同时上报 `privacy_budget_exhausted`。
- `FL_ROUND_DELAY` 默认 1.0s；容器里如需更快可在 compose 传 0.5。
- 源码无外网 URL（`grep -rn "http://\|https://"` 仅 qa 的测试文件含 127.0.0.1）。

## 阻塞
无。
