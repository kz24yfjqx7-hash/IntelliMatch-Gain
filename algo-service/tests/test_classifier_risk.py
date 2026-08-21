"""
数据分类分级（§3.5）与隐私风险评估（§3.6）。
"""
import pytest

from conftest import PREFIX

LEVELS = ["L1", "L2", "L3", "L4"]


def _classify(client, records):
    r = client.post(f"{PREFIX}/classify", json={"records": records})
    assert r.status_code == 200, r.text
    return r.json()


def _lvl(s):
    return LEVELS.index(s)


def test_sensitive_fields_at_least_L3(client):
    recs = [
        {"dataType": "pv", "fields": ["power", "voltage", "gps"], "freq": "minute", "volume": 1440},
        {"dataType": "load", "fields": ["kwh", "id_card", "phone"], "freq": "minute", "volume": 5000},
        {"dataType": "dispatch", "fields": ["cmd", "location", "owner_name"], "freq": "second", "volume": 86400},
    ]
    b = _classify(client, recs)
    for res in b["results"]:
        assert _lvl(res["level"]) >= _lvl("L3"), res
        assert res["factors"]["sensitivity"] >= 0.5, res


def test_plain_low_freq_small_volume_L1_or_L2(client):
    recs = [
        {"dataType": "pv", "fields": [], "freq": "day", "volume": 10},
        {"dataType": "wind", "fields": ["speed"], "freq": "hour", "volume": 24},
    ]
    b = _classify(client, recs)
    for res in b["results"]:
        assert res["level"] in ("L1", "L2"), res


def test_level_monotonic_with_score(client):
    recs = [
        {"dataType": "pv", "fields": [], "freq": "day", "volume": 10},
        {"dataType": "load", "fields": ["kwh"], "freq": "hour", "volume": 500},
        {"dataType": "pv", "fields": ["power", "gps"], "freq": "minute", "volume": 1440},
        {"dataType": "dispatch", "fields": ["cmd", "id_card", "gps"], "freq": "second", "volume": 100000},
    ]
    b = _classify(client, recs)
    pairs = sorted(((r["score"], _lvl(r["level"])) for r in b["results"]))
    for (s1, l1), (s2, l2) in zip(pairs, pairs[1:]):
        assert l1 <= l2, pairs
    assert b["results"][-1]["level"] in ("L3", "L4")
    assert _lvl(b["results"][-1]["level"]) > _lvl(b["results"][0]["level"])


def test_cluster_centers_shape(client):
    recs = [{"dataType": t, "fields": f, "freq": q, "volume": v} for t, f, q, v in [
        ("pv", [], "day", 10), ("pv", ["gps"], "minute", 1440), ("load", ["id_card"], "second", 50000),
        ("wind", ["speed"], "hour", 100), ("storage", ["soc"], "15min", 3000), ("dispatch", ["cmd", "owner"], "minute", 9000),
    ]]
    b = _classify(client, recs)
    cc = b["clusterCenters"]
    assert len(cc) == 3
    dims = {len(c) for c in cc}
    assert len(dims) == 1 and dims.pop() in (2, 3)
    for c in cc:
        assert all(0.0 <= v <= 1.0 for v in c), c
    clusters = {r["cluster"] for r in b["results"]}
    assert clusters <= {0, 1, 2}
    assert len(clusters) >= 2, "6 条差异明显的记录应至少落入 2 个簇"


def test_classify_deterministic(client):
    recs = [{"dataType": "pv", "fields": ["gps"], "freq": "minute", "volume": 1440}, {"dataType": "pv", "fields": [], "freq": "day", "volume": 5}]
    assert _classify(client, recs) == _classify(client, recs)


def test_classify_single_record(client):
    b = _classify(client, [{"dataType": "pv", "fields": ["power"], "freq": "hour", "volume": 24}])
    assert len(b["results"]) == 1 and b["clusterCenters"]


# ---------------------------------------------------------------- risk
def _risk(client, features, node="Node-A"):
    r = client.post(f"{PREFIX}/risk/assess", json={"nodeId": node, "features": features})
    assert r.status_code == 200, r.text
    return r.json()


def test_risk_factor_weights_sum_to_one(client):
    b = _risk(client, {"queryFreq": 12, "dataGranularity": "minute", "exposedFields": 6, "epsilonRemaining": 0.58})
    assert abs(sum(f["weight"] for f in b["factors"]) - 1.0) < 1e-3
    assert len(b["factors"]) >= 3
    names = " ".join(f["name"] for f in b["factors"])
    for kw in ("频率", "粒度", "字段", "预算"):
        assert kw in names, names


def test_risk_score_matches_weighted_factors(client):
    b = _risk(client, {"queryFreq": 12, "dataGranularity": "minute", "exposedFields": 6, "epsilonRemaining": 0.58})
    expected = sum(f["weight"] * f["score"] for f in b["factors"])
    assert abs(b["riskScore"] - expected) < 1.0, (b["riskScore"], expected)


def test_high_freq_fine_grain_low_budget_is_high_or_critical(client):
    b = _risk(client, {"queryFreq": 60, "dataGranularity": "second", "exposedFields": 12, "epsilonRemaining": 0.05})
    assert b["level"] in ("high", "critical"), b
    assert b["riskScore"] >= 60
    assert "ε" in b["suggestion"] or "epsilon" in b["suggestion"].lower() or "隐私" in b["suggestion"]


def test_low_freq_coarse_grain_full_budget_is_low(client):
    b = _risk(client, {"queryFreq": 1, "dataGranularity": "day", "exposedFields": 1, "epsilonRemaining": 1.0})
    assert b["level"] in ("low", "medium"), b
    assert b["riskScore"] < 50


def test_risk_monotonic_in_query_freq(client):
    scores = [_risk(client, {"queryFreq": q, "dataGranularity": "hour", "exposedFields": 3, "epsilonRemaining": 0.8})["riskScore"] for q in (1, 5, 20, 60)]
    assert all(b >= a for a, b in zip(scores, scores[1:])), scores
    assert scores[-1] > scores[0]


def test_risk_level_enum_consistent_with_score(client):
    for feats in (
        {"queryFreq": 1, "dataGranularity": "day", "exposedFields": 1, "epsilonRemaining": 1.0},
        {"queryFreq": 10, "dataGranularity": "hour", "exposedFields": 4, "epsilonRemaining": 0.6},
        {"queryFreq": 30, "dataGranularity": "minute", "exposedFields": 8, "epsilonRemaining": 0.3},
        {"queryFreq": 100, "dataGranularity": "second", "exposedFields": 20, "epsilonRemaining": 0.01},
    ):
        b = _risk(client, feats)
        assert b["level"] in ("low", "medium", "high", "critical")
    # 顺序关系
    lv = ["low", "medium", "high", "critical"]
    lo = _risk(client, {"queryFreq": 1, "dataGranularity": "day", "exposedFields": 1, "epsilonRemaining": 1.0})
    hi = _risk(client, {"queryFreq": 100, "dataGranularity": "second", "exposedFields": 20, "epsilonRemaining": 0.01})
    assert lv.index(hi["level"]) > lv.index(lo["level"])
