"""假算法服务——契约第三部分的可执行版本。

用途有两个：
1. 乙的 algo-service 没就绪之前，甲用它自测算法代理层
   `uvicorn tests.fake_algo:app --port 8100`
2. 给乙当参照物：接口路径、请求体、响应体、字段名以本文件为准

**这里面的数字是编出来的，不是算出来的。**
真正的 FedAvg、差分隐私、Top-k、DQN 必须由乙在 algo-service 里实现，
契约第三部分对此有明确要求（禁止硬编码假曲线）。本文件只保证「结构正确」。

本文件不依赖 backend 的任何模块，可以单独拷走运行。
"""
import asyncio
import hashlib
import os
import random
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="假算法服务（开发桩）", version="1.0.0")

CST = timezone(timedelta(hours=8))

# 每轮训练的模拟耗时。测试里设成 0 可以让任务瞬间跑完。
ROUND_SECONDS = float(os.getenv("FAKE_ALGO_ROUND_SECONDS", "1.5"))

_jobs: dict[str, dict] = {}


def _sm3_like(text: str) -> str:
    """假的摘要。真实服务里乙应该用 SM3 或 SHA256，契约允许两者。"""
    return hashlib.sha256(text.encode()).hexdigest()


# ================================================================ 3.1 健康检查

@app.get("/algo/v1/health")
def health():
    return {
        "status": "ok",
        "version": "1.0.0-fake",
        "models": {"dqn": "loaded", "deepseek": "cache"},
    }


# ================================================================ 3.2 联邦学习

class FlNode(BaseModel):
    id: str
    samples: int = 100


class DpConfig(BaseModel):
    enabled: bool = True
    epsilon: float = 1.0
    delta: float = 1e-5


class TopkConfig(BaseModel):
    enabled: bool = True
    ratio: float = 0.1


class FlTrainRequest(BaseModel):
    jobId: str
    rounds: int = 10
    nodes: list[FlNode] = Field(default_factory=list)
    dp: DpConfig = Field(default_factory=DpConfig)
    topk: TopkConfig = Field(default_factory=TopkConfig)


@app.post("/algo/v1/fl/train")
async def fl_train(body: FlTrainRequest):
    """异步执行，立刻返回。甲通过 GET /fl/jobs/{jobId} 轮询进度。"""
    job = {
        "jobId": body.jobId,
        "status": "running",
        "currentRound": 0,
        "totalRounds": body.rounds,
        "rounds": [],
        "modelVersion": None,
        "anomaly": None,
        "_config": body.model_dump(),
    }
    _jobs[body.jobId] = job
    asyncio.create_task(_run_job(body.jobId))
    return {"jobId": body.jobId, "status": "running"}


async def _run_job(job_id: str) -> None:
    job = _jobs[job_id]
    config = job["_config"]
    nodes = config["nodes"] or [{"id": "Node-A", "samples": 100}]
    total_samples = sum(n["samples"] for n in nodes) or 1

    rng = random.Random(sum(ord(c) for c in job_id))
    loss, acc, epsilon_spent = 0.85, 0.55, 0.0

    for r in range(1, config["rounds"] + 1):
        if ROUND_SECONDS:
            await asyncio.sleep(ROUND_SECONDS)
        if job["status"] == "cancelled":
            return

        # 收敛曲线：损失指数下降，准确率渐近饱和
        loss = round(loss * rng.uniform(0.78, 0.90), 6)
        acc = round(min(0.97, acc + (0.97 - acc) * rng.uniform(0.2, 0.35)), 4)
        # 隐私预算按轮累加，耗尽即为异常
        epsilon_spent = round(epsilon_spent + config["dp"]["epsilon"] / config["rounds"], 4)
        compression = round(100 * (1 - config["topk"]["ratio"]) + rng.uniform(-1.5, 1.5), 2)

        job["rounds"].append({
            "round": r,
            "loss": loss,
            "acc": acc,
            "compressionRatio": compression if config["topk"]["enabled"] else 0.0,
            "epsilonSpent": epsilon_spent if config["dp"]["enabled"] else 0.0,
            "gradientHash": _sm3_like(f"{job_id}:{r}:{loss}"),
            "nodeContributions": [
                {"nodeId": n["id"],
                 "weight": round(n["samples"] / total_samples, 3),
                 "localLoss": round(loss * rng.uniform(0.92, 1.18), 4)}
                for n in nodes
            ],
        })
        job["currentRound"] = r

        if config["dp"]["enabled"] and epsilon_spent > config["dp"]["epsilon"] * 1.001:
            job["anomaly"] = {
                "type": "privacy_budget_exhausted",
                "detail": f"隐私预算已耗尽（已用 {epsilon_spent}，上限 {config['dp']['epsilon']}）",
            }

    job["status"] = "success"
    job["modelVersion"] = f"v{len(_jobs) + 10}"


@app.get("/algo/v1/fl/jobs/{job_id}")
def fl_job(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"error": f"job {job_id} not found"})
    return {k: v for k, v in job.items() if not k.startswith("_")}


@app.post("/algo/v1/fl/jobs/{job_id}/cancel")
def fl_cancel(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"error": f"job {job_id} not found"})
    job["status"] = "cancelled"
    return {"jobId": job_id, "status": "cancelled"}


@app.post("/algo/v1/debug/anomaly/{job_id}")
def inject_anomaly(job_id: str, node_id: str = "Node-C"):
    """开发桩专用：手工注入梯度异常，用来验证甲这边的 R05 规则。

    真实算法服务不需要这个接口，它是乙实现异常检测之后自然产生的。
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"error": f"job {job_id} not found"})
    job["anomaly"] = {
        "type": "gradient_poisoning",
        "nodeId": node_id,
        "detail": f"节点 {node_id} 梯度范数超出中位数 8 倍，疑似投毒",
    }
    return {"jobId": job_id, "anomaly": job["anomaly"]}


# ================================================================ 3.3 DQN 调度

class DqnNode(BaseModel):
    id: str
    pv: float = 0.0
    load: float = 0.0
    soc: float = 50.0
    storage: float = 0.0
    price: float | None = 0.62


class DqnRequest(BaseModel):
    taskId: str
    timeWindow: str | None = None
    nodes: list[DqnNode] = Field(default_factory=list)


# 储能运行约束。真实实现里这些应该来自设备台账，这里写死。
SOC_MIN, SOC_MAX, MAX_POWER_KW = 20.0, 95.0, 30.0


@app.post("/algo/v1/dqn/dispatch")
def dqn_dispatch(body: DqnRequest):
    """按「净负荷 + SOC 裕度」给出动作。

    这是启发式规则，不是 DQN。乙必须用真实 DQN 替换：
    自建储能调度环境、离线训练、checkpoint 存到 models/dqn.npz、启动时加载推理。
    """
    actions, q_table, violations = [], [], []
    total_reward = 0.0

    for n in body.nodes:
        net = n.pv - n.load          # 正数=光伏富余，负数=有缺口
        price = n.price or 0.62

        if net < -5 and n.soc > SOC_MIN + 10:
            action, power = "discharge", min(MAX_POWER_KW, abs(net) * 0.5)
            reason = f"负荷缺口 {abs(net):.1f}kW 且 SOC {n.soc:.0f}% 有放电裕度"
            reward = power * price * 1.2
        elif net > 5 and n.soc < SOC_MAX - 5:
            action, power = "charge", min(MAX_POWER_KW, net * 0.4)
            reason = f"光伏富余 {net:.1f}kW 且 SOC {n.soc:.0f}% 未满，就地消纳"
            reward = power * price * 0.6
        else:
            action, power, reward = "idle", 0.0, 0.0
            reason = "供需基本平衡，保持待机以留出备用容量"

        if n.soc < SOC_MIN or n.soc > SOC_MAX:
            violations.append({"nodeId": n.id, "reason": f"SOC {n.soc} 超出安全区间"})

        q_discharge = round(max(0.0, -net) * 0.06 + n.soc * 0.04, 2)
        q_charge = round(max(0.0, net) * 0.07 + (100 - n.soc) * 0.03, 2)
        q_idle = round(5.0 - abs(net) * 0.01, 2)
        q_table.append({"nodeId": n.id, "charge": q_charge,
                        "idle": q_idle, "discharge": q_discharge})

        actions.append({
            "nodeId": n.id, "action": action, "powerKw": round(power, 1),
            "qValue": {"charge": q_charge, "idle": q_idle, "discharge": q_discharge}[action],
            "reason": reason,
        })
        total_reward += reward

    return {
        "taskId": body.taskId,
        "actions": actions,
        "totalReward": round(total_reward, 2),
        "qTable": q_table,
        "constraintsChecked": {
            "socMin": SOC_MIN, "socMax": SOC_MAX,
            "maxPowerKw": MAX_POWER_KW, "violations": violations,
        },
    }


# ================================================================ 3.4 DeepSeek 分析

class AnalyzeRequest(BaseModel):
    scene: str = "qa"
    context: dict = Field(default_factory=dict)
    question: str = ""


_ANSWERS = {
    "dispatch": (
        "该策略优先让负荷缺口最大、且储能荷电状态仍有裕度的节点放电削峰；"
        "光伏富余的节点转为充电就地消纳，避免弃光；其余节点保持待机留出备用容量。",
        ["按净负荷排序确定削峰优先级", "校验 SOC 处于 20%~95% 安全区间",
         "单节点出力不超过 30kW 限值"],
    ),
    "risk": (
        "当前风险主要由查询频率与数据粒度叠加造成，二者结合可反推用能主体的行为规律。",
        ["查询频率高于基线", "分钟级粒度信息量大", "暴露字段中含地理位置"],
    ),
    "data": (
        "该数据含主体可关联字段且采集粒度较细，按分级规则应判为敏感级以上，需授权后访问。",
        ["检查敏感字段", "评估采集粒度", "统计数据量级"],
    ),
    "audit": (
        "本期各项操作均已完整留痕并上链，越权尝试已被权限中心拦截并记为高危事件。",
        ["高危操作全部上链", "越权尝试已拦截", "可按 traceId 全链路追溯"],
    ),
    "qa": (
        "可信数据空间在数据不出域的前提下，用可信身份、可信存证与细粒度授权"
        "实现数据要素的安全流通。",
        ["身份可信", "过程可溯", "权限可控"],
    ),
}


@app.post("/algo/v1/deepseek/analyze")
def deepseek_analyze(body: AnalyzeRequest):
    """契约 3.4：**永远返回 200，永远有 answer**。

    乙的真实实现要做三级降级：真实 API（超时 8 秒）→ 本地缓存 → 规则化模板。
    这个桩直接返回缓存级结果。
    """
    started = time.perf_counter()
    answer, reasoning = _ANSWERS.get(body.scene, _ANSWERS["qa"])
    return {
        "answer": answer,
        "reasoning": reasoning,
        "source": "cache",
        "latencyMs": int((time.perf_counter() - started) * 1000) + 12,
    }


# ================================================================ 3.5 数据分类分级

class ClassifyRecord(BaseModel):
    dataType: str = "pv"
    fields: list[str] = Field(default_factory=list)
    freq: str = "minute"
    volume: int = 1


class ClassifyRequest(BaseModel):
    records: list[ClassifyRecord] = Field(default_factory=list)


_SENSITIVE = {"gps", "location", "lat", "lng", "address", "idcard", "phone", "userid"}
_FREQ = {"second": 1.0, "minute": 0.8, "hour": 0.5, "day": 0.2}


@app.post("/algo/v1/classify")
def classify(body: ClassifyRequest):
    """规则加权版。乙要用 k-means + 规则加权实现，替代原 raspi 前端的 Pyodide 方案。"""
    results = []
    for i, rec in enumerate(body.records):
        lowered = [f.lower() for f in rec.fields]
        sensitivity = min(1.0, sum(0.4 for s in _SENSITIVE if any(s in f for f in lowered)))
        granularity = _FREQ.get(rec.freq, 0.5)
        volume = min(1.0, rec.volume / 100_000) if rec.volume else 0.1
        score = round(0.45 * sensitivity + 0.35 * granularity + 0.20 * volume, 3)

        level = "L1" if score < 0.35 else "L2" if score < 0.55 else "L3" if score < 0.78 else "L4"
        reasons = []
        if sensitivity > 0:
            reasons.append("包含可关联到主体的敏感字段")
        if granularity >= 0.8:
            reasons.append(f"采集粒度为 {rec.freq} 级")
        if rec.volume >= 10_000:
            reasons.append(f"数据量 {rec.volume} 条")
        results.append({
            "index": i, "level": level, "score": score,
            "reason": "，".join(reasons) or "仅含聚合统计量",
            "cluster": min(2, int(score * 3)),
            "factors": {"sensitivity": round(sensitivity, 2),
                        "granularity": round(granularity, 2),
                        "volume": round(volume, 2)},
        })

    return {"results": results, "clusterCenters": [[0.2, 0.3], [0.5, 0.6], [0.8, 0.7]]}


# ================================================================ 3.6 隐私风险评估

class RiskRequest(BaseModel):
    nodeId: str
    features: dict = Field(default_factory=dict)


@app.post("/algo/v1/risk/assess")
def risk_assess(body: RiskRequest):
    f = body.features
    freq_score = min(100.0, float(f.get("queryFreq", 0)) * 7)
    gran_score = {"second": 100, "minute": 80, "hour": 50, "day": 20}.get(
        str(f.get("dataGranularity", "hour")), 50)
    field_score = min(100.0, float(f.get("exposedFields", 0)) * 12)
    budget_score = max(0.0, (1 - float(f.get("epsilonRemaining", 1.0))) * 100)

    score = round(freq_score * 0.35 + gran_score * 0.30
                  + field_score * 0.20 + budget_score * 0.15, 2)
    level = "low" if score < 30 else "medium" if score < 55 else "high" if score < 80 else "critical"

    return {
        "nodeId": body.nodeId,
        "riskScore": score,
        "level": level,
        "factors": [
            {"name": "查询频率", "weight": 0.35, "score": round(freq_score, 1),
             "desc": f"5分钟内{f.get('queryFreq', 0)}次查询"},
            {"name": "数据粒度", "weight": 0.30, "score": float(gran_score),
             "desc": f"采集粒度 {f.get('dataGranularity', 'hour')}"},
            {"name": "暴露字段数", "weight": 0.20, "score": round(field_score, 1),
             "desc": f"暴露 {f.get('exposedFields', 0)} 个字段"},
            {"name": "隐私预算余量", "weight": 0.15, "score": round(budget_score, 1),
             "desc": f"剩余 {f.get('epsilonRemaining', 1.0)}"},
        ],
        "suggestion": ("建议将差分隐私 ε 由 1.0 降至 0.5" if level in ("high", "critical")
                       else "当前隐私风险可控，保持现有策略"),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8100)
