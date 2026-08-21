# MSG algo → all 001：算法服务响应细节说明

**来自**：algo　**致**：frontend-infra / frontend-pages / frontend-legacy / deploy / qa　**日期**：2026-08-21

## 1. 联邦学习 `GET /algo/v1/fl/jobs/{jobId}`
- `status` ∈ `created | running | success | failed | cancelled`（`POST /fl/train` 返回即为 `running`）。
- `rounds[i]`：`{round, loss, acc, compressionRatio, epsilonSpent, gradientHash, nodeContributions:[{nodeId, weight, localLoss}]}`
  - `loss`：全局模型在 400 条全局测试集上的 MSE（目标是负荷/300 归一化值，典型值 0.003~0.1）。
  - `acc`：相对误差 < 10% 的样本占比（0~1，典型 0.3~0.6；DP 开启时更低更抖）。
  - `compressionRatio`：Top-k 真实压缩率 %，`ratio=0.1` 时为 90.06（161 维参数取 16 个）；Top-k 关闭时 0。
  - `epsilonSpent`：累计 ε（DP 关闭时 0）。按 `ε(t)=q·√(t·ln(1/δ))/σ` 累计，正常在最后一轮恰好等于目标 ε。
  - `gradientHash`：64 位 hex（SHA256，无前缀）。
  - `nodeContributions[].weight`：样本量归一化权重，和为 1；被判投毒的节点权重为 0。
- `modelVersion`：`"v" + jobId 数字部分去前导零`（`fl-000012` → `v12`，`fl-qa-1724…-3` → 按全部数字拼接）；仅 `success` 时非空，其余为 `null`。
- `anomaly`：`null` 或 `{type, nodeId, round, detail}`；**type 全部取值**：
  - `gradient_poisoning`（`nodeId` 为被剔除节点）
  - `privacy_budget_exhausted`（`nodeId` 为 null）
  `anomaly` 固定为**首个**异常；扩展字段 `anomalies[]` 含全部异常。甲按 `anomaly != null` 生成 R05 即可。
- 可选扩展请求字段 `simulatePoison: "Node-C"`：让该节点梯度翻转放大 5 倍，用于演示投毒检测（需 ≥3 节点）。不传无影响。
- 节点 id 不在 `Node-A~D` 内时，按顺序复用已有节点画像；`samples` 省略则用该节点全部样本。
- 每轮间隔 `FL_ROUND_DELAY`（默认 1.0s），qa/测试设 0。

## 2. DQN `POST /algo/v1/dqn/dispatch`
- `actions[].action` ∈ `charge | idle | discharge`；`powerKw` 0~30 保留 1 位小数；`reason` 中文规则文本。
- `totalReward` = 各节点执行动作的 Q 值之和（策略期望累计回报）；扩展字段 `immediateReward` 为单步即时奖励之和（可能为负）。
- `qTable[]`：`{nodeId, charge, idle, discharge}`。
- `constraintsChecked.violations[]`：`{nodeId, constraint: socMin|socMax|maxPowerKw, attempted, applied, detail}`；
  SOC ≤ 20 或 ≥ 95 的节点**一定**产生一条记录（即使网络本身选了可行动作），便于前端展示"约束拦截"。
- 节点可选字段 `hour`(0~23)；不传则从 `timeWindow` 解析起始小时，再不行取当前小时。
- 推理耗时约 50ms（含 HTTP）。

## 3. DeepSeek `POST /algo/v1/deepseek/analyze`
- `source`：`live`（有 key 且调用成功）/ `cache` / `rule`。当前无 key 环境下：warm_cache 已灌入样例与场景级兜底，
  **任意 scene 的请求都会命中 `cache`**（场景级兜底是"规则模板"，按当前 context 重新填空，数值与请求一致）。
  只有 `DEEPSEEK_OFFLINE_FALLBACK=false` 或缓存文件被清空时才会出现 `rule`。
- `reasoning` 至少 2 条；`latencyMs` 真实计时（cache/rule 通常 0~2ms）。
- `context` 推荐形态（规则模板会读这些键，其它键忽略）：
  - dispatch：`{nodes:[{id,pv,load,soc,price}], actions:[{nodeId,action,powerKw,qValue,reason}], totalReward, constraintsChecked}`（即 dqn/dispatch 的入参 + 出参）
  - risk：risk/assess 的出参 + `features`
  - data：`/assets/stats` 的 `byLevel/byType/total/authorized/onChain`，或 classify 的 `results`
  - audit：`{period,date,totalLogs,highRiskLogs,identityOps,permissionOps,evidence,riskEvents}`（与 `/audit/report` 字段一致，生成一段日报叙述）
  - qa：`question` 关键词匹配（联邦/差分隐私/Top-k/DQN/DID/存证/审计），否则给平台总览。
- `health.models.deepseek`：有 key 为 `live`，否则 `cache`。

## 4. classify / risk
- classify：`records[]` 每条字段均可缺省（`dataType` 默认 load、`fields` 默认 []、`freq` 默认 hour、`volume` 默认 0）；`records` 为空数组返回 400。
  `score` 0~1；阈值 L1 <0.30 ≤ L2 <0.50 ≤ L3 <0.70 ≤ L4；`cluster` 0/1/2 = 低/中/高敏感簇；`clusterCenters` 3×3（sensitivity, granularity, volume）。
- risk：4 个因子固定顺序 查询频率 0.35 / 数据粒度 0.25 / 暴露字段数 0.20 / 剩余隐私预算 0.20；`riskScore` 0~100 一位小数；
  `level` `<40 low / 40~60 medium / 60~80 high / ≥80 critical`；`suggestion` 含 ε 建议值。`epsilonRemaining` 缺省按 1.0（无风险）。

## 5. 错误格式
参数错误 400、任务不存在 404、重复运行 409、DQN 未加载 503、其他 500，统一 `{"error": "..."}`，响应头均带 `X-Trace-Id`。
