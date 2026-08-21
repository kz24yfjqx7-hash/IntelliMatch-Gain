# algo-service · 算法服务（乙方）

Python 3.11 + FastAPI + NumPy，无数据库，端口 `ALGO_PORT`（默认 8100），路径前缀 `/algo/v1`。
实现 `contract/API-CONTRACT.md` 第三部分全部接口；响应不包装，错误返回 `{"error": "..."}`；
每个请求读取 `X-Trace-Id` 并回写到响应头。所有算法为 NumPy 手写实现，**无硬编码曲线**。

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/algo/v1/health` | `{"status":"ok","version":"1.0.0","models":{"dqn":"loaded","deepseek":"live\|cache"}}` |
| POST | `/algo/v1/fl/train` | 启动联邦训练任务（异步，立即返回 `running`；重复 jobId 且仍在运行 → 409） |
| GET | `/algo/v1/fl/jobs/{jobId}` | 轮询进度：`status / currentRound / totalRounds / rounds[] / modelVersion / anomaly` |
| POST | `/algo/v1/fl/jobs/{jobId}/cancel` | 取消任务 |
| POST | `/algo/v1/dqn/dispatch` | DQN 储能调度：`actions / totalReward / qTable / constraintsChecked` |
| POST | `/algo/v1/deepseek/analyze` | DeepSeek 解释（live → cache → rule 三级降级，永远 200） |
| POST | `/algo/v1/classify` | 数据分类分级 k-means + 规则加权 → L1~L4 |
| POST | `/algo/v1/risk/assess` | 动态隐私风险评估（4 因子加权）+ ε 建议 |

扩展（契约外，甲不传不影响）：`fl/train` 支持 `simulatePoison: "Node-C"`（投毒演示）；`dqn/dispatch` 节点可带 `hour`；
`fl/jobs/{id}` 额外返回 `anomalies[]`；`dqn/dispatch` 额外返回 `immediateReward`；`GET /algo/v1/fl/jobs` 列出任务。

## 目录

```
main.py              FastAPI 应用、trace 中间件、FL 任务管理器（线程 + 锁）
config.py            环境变量与路径（DeepSeek base url 默认空，由 .env 提供）
algorithms/
  fedavg.py          FedAvg + 8-16-1 MLP + 投毒检测 + 梯度哈希
  dp.py              L2 裁剪 + 高斯机制 + 隐私预算会计
  topk.py            Top-k 稀疏化 + 残差缓存
  dqn.py             储能调度环境、Q 网络（手写反向传播 + Adam）、训练、推理与约束校验
  classifier.py      k-means(k=3) + 规则加权分级
  risk.py            隐私风险评分
adapters/deepseek.py 三级降级客户端
scripts/gen_datasets.py / train_dqn.py / warm_cache.py
data/node_datasets.npz   4 节点 Non-IID 数据（脚本生成）
models/dqn.npz           DQN checkpoint；dqn_train_log.json 训练曲线
cache/deepseek_cache.json
```

## 算法说明

**FedAvg**（报告 2.2.2）：全局目标 `min_θ F(θ) = Σ_k (n_k/n)·F_k(θ)`。每轮各节点以全局参数为起点做本地 mini-batch SGD
（MLP 8-16-1，MSE），服务端按样本量加权平均更新 `θ_{t+1} = θ_t + Σ_k (n_k/n)·Δ_k`。
`loss` = 全局测试集 MSE（目标为归一化负荷），`acc` = 相对误差 < 10% 的样本占比。
每轮 `gradientHash = SHA256(聚合更新向量)`（契约允许 SM3 或 SHA256）。
投毒检测：`cos θ_{i,j} = Δ_i·Δ_j / (‖Δ_i‖‖Δ_j‖)`，节点与其余节点的平均余弦 < −0.5 → `anomaly.type = gradient_poisoning`，
该节点后续轮次权重置 0。

**差分隐私**（报告 2.2.1 / DEV-PLAN §2）：每个节点更新先 L2 裁剪 `Δ' = Δ / max(1, ‖Δ‖/C)`（C 取各节点范数中位数，自适应裁剪），
服务端对加权平均加高斯噪声 `N(0, (σ·C·w_max)²)`。
预算会计采用简化矩会计 `ε = q·√(T·ln(1/δ)) / σ`（q = 本地 batch / 节点样本量），反推 `σ`；
第 t 轮累计 `ε(t) = q·√(t·ln(1/δ))/σ`。σ 有上限 2.0，申请的 ε 过小时会中途耗尽 →
`anomaly.type = privacy_budget_exhausted`（训练继续、噪声继续，后端据此生成 R05 告警）。

**Top-k**（报告 2.2.3）：`T_k(g) = {g_i : |g_i| ≥ τ_k}`，残差缓存 `c(t+1) = c(t) + g(t) − T_k(g(t))`，
`compressionRatio = (1 − k/n)·100`。

**DQN**（报告 2.2.4）：状态 `[p_pv, p_load, SOC, price, hour]`，动作 `{charge, idle, discharge}`，
奖励 `r = α1·B − α2·c_imb − α3·c_pri − α4·c_safe`（B 电价收益 + 削峰量，c_imb 净负荷不平衡，c_pri 本接口无风险等级输入取 α3=0，
c_safe SOC/功率越限）。Q 网络 5-64-64-3，Huber 损失，Adam，经验回放 50k，目标网络软更新 τ=0.01，γ=0.95，ε-greedy 1.0→0.05。
推理：约束 SOC 20%~95%、单节点 ≤ 30 kW；越限动作改为次优可行动作并写入 `constraintsChecked.violations`；
`powerKw = min(30·intensity, SOC 余量对应能量)`，intensity 由 Q 值优势决定。`totalReward = Σ Q(s_k,a_k)`。

**分类分级**（报告 2.2.1）：`S = 0.5·sensitivity + 0.3·granularity + 0.2·volume`，叠加 k-means 簇号修正；
阈值 L1 < 0.30 ≤ L2 < 0.50 ≤ L3 < 0.70 ≤ L4。

**风险评估**：`riskScore = 0.35·查询频率 + 0.25·数据粒度 + 0.20·暴露字段 + 0.20·剩余预算`；
`<40 low / 40~60 medium / 60~80 high / ≥80 critical`；ε 建议 `ε_i = ε0·exp(−λ·risk)`，λ=1.2。

**DeepSeek**：`DEEPSEEK_API_KEY` 非空 → live（httpx，超时 `DEEPSEEK_TIMEOUT`）；失败 → `cache/deepseek_cache.json`
（key = sha256(scene + canonical_json(context) + question)，再查 `scene:<scene>` 兜底）；再失败 → 中文规则模板。

## 本地运行

```bash
cd algo-service
../.venv/bin/python scripts/gen_datasets.py      # 生成数据（缺失时服务会自动生成）
../.venv/bin/python scripts/train_dqn.py         # 完整训练 DQN（约 1 分钟，80 核 x86）；--quick 为快速模式
../.venv/bin/python scripts/warm_cache.py        # 预热 DeepSeek 缓存（有 key 走 live，无 key 灌规则模板）
../.venv/bin/uvicorn main:app --port 8100
curl -s localhost:8100/algo/v1/health
```

环境变量见 `contract/API-CONTRACT.md` 第四部分；另有可选：`FL_ROUND_DELAY`（每轮停顿秒数，默认 1.0，测试设 0）、
`FL_LOCAL_EPOCHS / FL_LOCAL_LR / FL_BATCH_SIZE / FL_LR_DECAY / POISON_COS_THRESHOLD`。

## 重新训练 / 重新生成

- 数据：`python scripts/gen_datasets.py`（改 `NODE_PROFILES` 可调整各节点画像）
- DQN：`python scripts/train_dqn.py --episodes 5000 --seed 3`，产物 `models/dqn.npz` + `models/dqn_train_log.json`
- 缓存：`python scripts/warm_cache.py --force`

## Docker

`docker build -t energy-tds/algo-service:1.0 .`；镜像内含 models/data/cache，缺失时 Dockerfile 兜底生成。`EXPOSE 8100`。
