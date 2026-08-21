"""
预热 DeepSeek 缓存 -> cache/deepseek_cache.json

- 有 DEEPSEEK_API_KEY（且 .env 提供 DEEPSEEK_BASE_URL）：对每个典型场景调用 live，把真实回答写入缓存
  （精确 key + 场景兜底 key "scene:<scene>"）。答辩前联网跑一次。
- 无 key：把规则模板文本灌入缓存，保证断网时 /deepseek/analyze 至少命中 source=cache。
用法： python scripts/warm_cache.py [--force]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from adapters import deepseek  # noqa: E402

# 典型场景样例：与前端 mock / 甲方后端常用 context 形态一致
SAMPLES = [
    {
        "scene": "dispatch",
        "question": "为什么选择节点C放电？",
        "context": {
            "taskId": "dp-000009",
            "nodes": [
                {"id": "Node-A", "pv": 45.3, "load": 120, "soc": 65, "price": 0.62},
                {"id": "Node-B", "pv": 80.0, "load": 60, "soc": 40, "price": 0.62},
                {"id": "Node-C", "pv": 20.0, "load": 150, "soc": 85, "price": 1.05},
                {"id": "Node-D", "pv": 10.0, "load": 140, "soc": 15, "price": 1.05},
            ],
            "actions": [
                {"nodeId": "Node-C", "action": "discharge", "powerKw": 24.0, "qValue": 8.42, "reason": "负荷最高，SOC充足，处于峰时电价"},
                {"nodeId": "Node-D", "action": "charge", "powerKw": 12.0, "qValue": 2.1, "reason": "SOC低于下限，禁止放电，SOC偏低"},
            ],
            "totalReward": 15.7,
            "constraintsChecked": {"violations": [{"nodeId": "Node-D", "constraint": "socMin", "detail": "SOC=15.0% 触发下限约束，禁止 discharge"}]},
        },
    },
    {
        "scene": "risk",
        "question": "该节点的隐私风险如何？",
        "context": {
            "nodeId": "Node-A",
            "riskScore": 72.4,
            "level": "high",
            "features": {"queryFreq": 12, "dataGranularity": "minute", "exposedFields": 6, "epsilonRemaining": 0.58},
            "factors": [
                {"name": "查询频率", "weight": 0.35, "score": 85, "desc": "5分钟内12次查询"},
                {"name": "数据粒度", "weight": 0.25, "score": 75, "desc": "采集粒度 minute"},
                {"name": "暴露字段数", "weight": 0.20, "score": 60, "desc": "暴露 6 个字段"},
                {"name": "剩余隐私预算", "weight": 0.20, "score": 42, "desc": "剩余预算 ε=0.58"},
            ],
            "suggestion": "建议将差分隐私 ε 由 1.0 降至 0.5",
        },
    },
    {
        "scene": "data",
        "question": "请分析当前数据资产的分级分布",
        "context": {
            "total": 85,
            "authorized": 40,
            "onChain": 85,
            "byLevel": [{"level": "L1", "count": 12}, {"level": "L2", "count": 33}, {"level": "L3", "count": 28}, {"level": "L4", "count": 12}],
            "byType": [{"dataType": "pv", "count": 30}, {"dataType": "load", "count": 25}, {"dataType": "storage", "count": 18}, {"dataType": "dispatch", "count": 12}],
        },
    },
    {
        "scene": "audit",
        "question": "请生成今日审计日报解读",
        "context": {
            "period": "day",
            "date": "2026-08-17",
            "totalLogs": 420,
            "highRiskLogs": 6,
            "identityOps": {"register": 5, "freeze": 1, "revoke": 0, "rotate": 2},
            "permissionOps": {"applied": 8, "approved": 6, "rejected": 2, "revoked": 1},
            "evidence": {"total": 128, "byCategory": {"data": 60, "identity": 22, "permission": 18, "audit": 25, "algo": 3}},
            "riskEvents": [{"ruleCode": "R01_UNAUTHORIZED", "count": 3, "level": "high"}],
        },
    },
    {"scene": "qa", "question": "什么是联邦学习？", "context": {}},
    {"scene": "qa", "question": "差分隐私的 ε 是什么意思？", "context": {}},
    {"scene": "qa", "question": "平台如何防止数据被篡改？", "context": {}},
    {"scene": "qa", "question": "DQN 是怎么做调度决策的？", "context": {}},
]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="已有缓存也覆盖")
    args = ap.parse_args(argv)
    has_key = bool(config.DEEPSEEK_API_KEY and config.DEEPSEEK_BASE_URL)
    print(f"[warm_cache] 模式：{'live（有 API key）' if has_key else 'rule 灌入（无 API key）'}  缓存文件：{config.DEEPSEEK_CACHE_PATH}")
    existing = deepseek.load_cache()
    n_live = n_rule = 0
    for s in SAMPLES:
        key = deepseek.cache_key(s["scene"], s["context"], s["question"])
        if key in existing and not args.force and existing[key].get("source") == "live":
            print(f"  跳过（已有 live 缓存）{s['scene']} / {s['question']}")
            continue
        res = deepseek.call_live(s["scene"], s["context"], s["question"]) if has_key else None
        if res is not None:
            answer, reasoning = res
            src = "live"
            n_live += 1
        else:
            answer, reasoning = deepseek.rule_answer(s["scene"], s["context"], s["question"])
            src = "rule"
            n_rule += 1
        entry = {"scene": s["scene"], "question": s["question"], "answer": answer, "reasoning": reasoning, "source": src, "cachedAt": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if src == "rule":
            entry["template"] = "rule"  # 标记为模板：场景级兜底命中时按实际 context 重新填空
        deepseek.save_cache_entry(key, entry)
        # 场景兜底 key：同场景任意 context 未命中精确 key 时使用（优先保留 live 版本）
        sk = deepseek.scene_key(s["scene"])
        cur = deepseek.load_cache().get(sk)
        if cur is None or src == "live" or args.force:
            deepseek.save_cache_entry(sk, entry)
        print(f"  [{src}] {s['scene']} / {s['question']}")
    print(f"[warm_cache] 完成：live {n_live} 条，rule {n_rule} 条，缓存总计 {len(deepseek.load_cache())} 条")


if __name__ == "__main__":
    main()
