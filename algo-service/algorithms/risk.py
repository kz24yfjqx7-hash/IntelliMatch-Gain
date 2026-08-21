"""
动态隐私风险评估（研究报告 2.2.1「动态隐私预算分配」）。

riskScore = Σ_i w_i · score_i  （0~100），四个因子权重之和为 1：
  查询频率   w=0.35   score = min(100, queryFreq / 20 · 100)         （5 分钟内 ≥20 次视为满分风险）
  数据粒度   w=0.25   second/realtime 95, minute 75, 15min 55, hour 35, day 15
  暴露字段数 w=0.20   score = min(100, exposedFields / 10 · 100)
  剩余隐私预算 w=0.20 score = (1 − epsilonRemaining/ε0) · 100        （预算越少风险越高，ε0=1.0）
等级：<40 low / 40~60 medium / 60~80 high / ≥80 critical

ε 建议（动态预算分配 ε_i = ε0 · exp(−λ·risk)，λ=1.2，risk∈[0,1]）：
  风险越高，为该节点分配的 ε 越小（噪声越强）。
"""
from __future__ import annotations

import math

EPS0 = 1.0
LAMBDA = 1.2
GRAN_SCORE = {"second": 95, "realtime": 95, "minute": 75, "5min": 65, "15min": 55, "hour": 35, "hourly": 35, "day": 15, "daily": 15}
WEIGHTS = (("查询频率", 0.35), ("数据粒度", 0.25), ("暴露字段数", 0.20), ("剩余隐私预算", 0.20))


def level_of(score: float) -> str:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def assess(node_id: str, features: dict) -> dict:
    features = features or {}
    qf = float(features.get("queryFreq") or 0)
    gran = str(features.get("dataGranularity") or "hour").lower()
    ef = float(features.get("exposedFields") or 0)
    eps_rem = features.get("epsilonRemaining")
    eps_rem = float(eps_rem) if eps_rem is not None else EPS0

    s_qf = min(100.0, qf / 20.0 * 100.0)
    s_gr = float(GRAN_SCORE.get(gran, 50))
    s_ef = min(100.0, ef / 10.0 * 100.0)
    s_eps = min(100.0, max(0.0, (1.0 - eps_rem / EPS0) * 100.0))
    scores = [s_qf, s_gr, s_ef, s_eps]
    descs = [
        f"5分钟内{int(qf)}次查询",
        f"采集粒度 {gran}",
        f"暴露 {int(ef)} 个字段",
        f"剩余预算 ε={eps_rem:.2f}（基准 {EPS0}）",
    ]
    factors = [
        {"name": name, "weight": w, "score": round(sc, 1), "desc": d}
        for (name, w), sc, d in zip(WEIGHTS, scores, descs)
    ]
    risk = sum(w * sc for (_, w), sc in zip(WEIGHTS, scores))
    risk = round(min(100.0, max(0.0, risk)), 1)
    lv = level_of(risk)
    eps_suggest = round(EPS0 * math.exp(-LAMBDA * risk / 100.0), 2)
    top = max(factors, key=lambda f: f["weight"] * f["score"])
    if lv in ("high", "critical"):
        suggestion = f"风险等级 {lv}，主要来源为{top['name']}（{top['desc']}）。建议将差分隐私 ε 由 {EPS0:.1f} 降至 {eps_suggest}，并限制查询频率、收敛暴露字段。"
    elif lv == "medium":
        suggestion = f"风险等级 medium，建议将差分隐私 ε 由 {EPS0:.1f} 调整至 {eps_suggest}，关注{top['name']}变化。"
    else:
        suggestion = f"风险等级 low，可维持差分隐私 ε={EPS0:.1f}（动态建议值 {eps_suggest}），保持当前访问策略。"
    return {"nodeId": node_id, "riskScore": risk, "level": lv, "factors": factors, "suggestion": suggestion}
