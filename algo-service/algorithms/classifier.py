"""
能源数据分类分级：k-means(k=3) 聚类 + 规则加权 → L1~L4（研究报告 2.2.1 节）。

敏感度函数  S(d_i) = ω1·u_i + ω2·p_i + ω3·a_i
  u_i 用户隐私相关度（本实现 = sensitivity，由字段敏感词表打分）
  p_i 设备运行敏感度（本实现 = granularity，采集粒度越细越敏感）
  a_i 访问/采集频率系数（本实现 = volume，数据量对数归一）
  权重 ω = (0.5, 0.3, 0.2)

分级阈值（S ∈ [0,1]）：
  L1 公开   S < 0.30
  L2 内部   0.30 ≤ S < 0.50
  L3 敏感   0.50 ≤ S < 0.70
  L4 核心   S ≥ 0.70
k-means 在特征空间 [sensitivity, granularity, volume] 上聚 3 簇（固定随机种子），
簇中心按敏感度均值排序得到簇号 0/1/2（低/中/高），簇信息叠加为规则分的微调项：
  score = 0.85·S + 0.15·(簇排名 / 2)
"""
from __future__ import annotations

import math

import numpy as np

WEIGHTS = {"sensitivity": 0.5, "granularity": 0.3, "volume": 0.2}
THRESHOLDS = [(0.70, "L4"), (0.50, "L3"), (0.30, "L2"), (0.0, "L1")]

# 字段敏感词表：字段名（小写）包含关键词即按该分值计
SENSITIVE_KEYWORDS = {
    "gps": 1.0, "location": 1.0, "latitude": 1.0, "longitude": 1.0, "address": 0.9,
    "id_card": 1.0, "idcard": 1.0, "phone": 0.9, "name": 0.7, "owner": 0.7, "user": 0.6,
    "did": 0.5, "key": 0.8, "secret": 1.0, "token": 0.9, "password": 1.0,
    "price": 0.6, "bid": 0.7, "contract": 0.7, "revenue": 0.7, "cost": 0.5,
    "soc": 0.4, "load": 0.35, "power": 0.3, "voltage": 0.2, "current": 0.2, "temp": 0.1, "status": 0.1,
}
# 数据类型基础敏感度（dispatch 指令最敏感）
DATATYPE_BASE = {"pv": 0.2, "wind": 0.2, "storage": 0.35, "load": 0.45, "dispatch": 0.6}
# 采集粒度分值
FREQ_SCORE = {"second": 1.0, "realtime": 1.0, "minute": 0.7, "5min": 0.6, "15min": 0.5, "hour": 0.35, "hourly": 0.35, "day": 0.15, "daily": 0.15, "month": 0.05}


def _field_sensitivity(fields: list[str], data_type: str) -> tuple[float, list[str]]:
    """取字段中的敏感关键词：综合 = max 命中分×0.7 + 平均命中分×0.3，再与类型基线取较大。"""
    hits: list[tuple[str, float]] = []
    for f in fields:
        fl = str(f).lower()
        for kw, sc in SENSITIVE_KEYWORDS.items():
            if kw in fl:
                hits.append((f, sc))
                break
    base = DATATYPE_BASE.get(data_type, 0.3)
    if not hits:
        return base, []
    scores = [s for _, s in hits]
    val = 0.7 * max(scores) + 0.3 * float(np.mean(scores))
    return max(base, min(1.0, val)), [f for f, s in hits if s >= 0.6]


def _granularity(freq: str) -> float:
    return FREQ_SCORE.get(str(freq).lower(), 0.5)


def _volume(volume: float) -> float:
    """数据量对数归一：log10(volume+1)/5（10 万条 → 1.0）。"""
    return float(min(1.0, math.log10(max(float(volume), 0.0) + 1.0) / 5.0))


def extract_features(rec: dict) -> tuple[dict, list[str]]:
    """records 可能缺字段：dataType 默认 load，fields 默认 []，freq 默认 hour，volume 默认 0。"""
    data_type = str(rec.get("dataType") or "load")
    fields = list(rec.get("fields") or [])
    sens, sensitive_fields = _field_sensitivity(fields, data_type)
    feats = {
        "sensitivity": round(sens, 4),
        "granularity": round(_granularity(rec.get("freq") or "hour"), 4),
        "volume": round(_volume(rec.get("volume") or 0), 4),
    }
    return feats, sensitive_fields


def kmeans(X: np.ndarray, k: int = 3, iters: int = 50, seed: int = 2026) -> tuple[np.ndarray, np.ndarray]:
    """标准 Lloyd k-means（固定种子）。样本数 < k 时补充虚拟锚点保证有 k 个中心。"""
    rng = np.random.default_rng(seed)
    anchors = np.array([[0.15, 0.2, 0.2], [0.5, 0.5, 0.5], [0.85, 0.8, 0.7]])
    data = np.vstack([X, anchors]) if X.shape[0] < k * 2 else X  # 少样本时加入先验锚点稳定聚类
    centers = data[rng.choice(data.shape[0], k, replace=False)].copy()
    for _ in range(iters):
        d = ((data[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        new = np.array([data[lab == j].mean(0) if np.any(lab == j) else centers[j] for j in range(k)])
        if np.allclose(new, centers):
            break
        centers = new
    # 按敏感度均值排序簇号：0 低 / 1 中 / 2 高
    order = np.argsort(centers.mean(1))
    centers = centers[order]
    d = ((X[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
    labels = d.argmin(1)
    return labels, centers


def level_of(score: float) -> str:
    for th, lv in THRESHOLDS:
        if score >= th:
            return lv
    return "L1"


def classify(records: list[dict]) -> dict:
    """契约 3.5：返回 results + clusterCenters。"""
    if not records:
        return {"results": [], "clusterCenters": []}
    feats_list, sens_fields = zip(*(extract_features(r) for r in records))
    X = np.array([[f["sensitivity"], f["granularity"], f["volume"]] for f in feats_list])
    labels, centers = kmeans(X, k=3)
    results = []
    for i, (rec, f) in enumerate(zip(records, feats_list)):
        s_rule = sum(WEIGHTS[k] * f[k] for k in WEIGHTS)
        score = 0.85 * s_rule + 0.15 * (int(labels[i]) / 2.0)
        score = float(min(1.0, max(0.0, score)))
        lv = level_of(score)
        reasons = []
        if sens_fields[i]:
            reasons.append(f"包含敏感字段 {'/'.join(sens_fields[i][:3])}")
        freq = str(rec.get("freq") or "hour")
        if f["granularity"] >= 0.7:
            reasons.append(f"采集粒度为{ '秒级' if f['granularity'] >= 1.0 else '分钟级'}")
        elif f["granularity"] <= 0.2:
            reasons.append("采集粒度较粗（日级及以上）")
        if f["volume"] >= 0.6:
            reasons.append(f"数据量较大（{int(rec.get('volume') or 0)} 条）")
        dt = rec.get("dataType") or "load"
        if dt == "dispatch":
            reasons.append("属于调度指令类数据")
        reasons.append(f"规则综合敏感度 {s_rule:.2f}，聚类簇 {int(labels[i])}（{['低','中','高'][int(labels[i])]}敏感簇）")
        results.append(
            {
                "index": i,
                "level": lv,
                "score": round(score, 4),
                "reason": "，".join(reasons),
                "cluster": int(labels[i]),
                "factors": f,
            }
        )
    return {"results": results, "clusterCenters": [[round(float(v), 4) for v in c] for c in centers]}
