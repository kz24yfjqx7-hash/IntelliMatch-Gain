"""
对抗/边界输入（test-algo）：非法与极端入参必须 400 + {"error"}（或合法处理为 200），绝不 500。
"""
import json

import pytest

from conftest import PREFIX, start_job

NODES = [{"id": "Node-A", "samples": 480}]


def _assert_4xx(r):
    assert 400 <= r.status_code < 500, (r.status_code, r.text)
    assert "error" in r.json() and r.json()["error"]


def _raw(client, path, text):
    return client.post(f"{PREFIX}{path}", content=text, headers={"content-type": "application/json"})


# ---------------- FL /fl/train
@pytest.mark.parametrize(
    "body",
    [
        {"jobId": "a", "rounds": 2, "nodes": []},
        {"jobId": "a", "rounds": 0, "nodes": NODES},
        {"jobId": "a", "rounds": 1000, "nodes": NODES},
        {"jobId": "a", "rounds": 2.5, "nodes": NODES},
        {"jobId": "a", "rounds": "abc", "nodes": NODES},
        {"jobId": 123, "rounds": 2, "nodes": NODES},
        {"rounds": 2, "nodes": NODES},
        {"jobId": "a", "rounds": 2, "nodes": {"id": "x"}},
        {"jobId": "a", "rounds": 2, "nodes": NODES, "topk": {"enabled": True, "ratio": 0}},
        {"jobId": "a", "rounds": 2, "nodes": NODES, "topk": {"enabled": True, "ratio": 1.5}},
        {"jobId": "a", "rounds": 2, "nodes": NODES, "topk": {"enabled": True, "ratio": -1}},
        {"jobId": "a", "rounds": 2, "nodes": NODES, "dp": {"enabled": True, "epsilon": 0}},
        {"jobId": "a", "rounds": 2, "nodes": NODES, "dp": {"enabled": True, "epsilon": -1}},
        {"jobId": "a", "rounds": 2, "nodes": NODES, "dp": {"enabled": True, "epsilon": 1e9}},
        {"jobId": "a", "rounds": 2, "nodes": NODES, "dp": {"enabled": True, "delta": 0}},
        {"jobId": "a", "rounds": 2, "nodes": NODES, "dp": {"enabled": True, "delta": 2}},
        {"jobId": "x" * 200000, "rounds": 2, "nodes": NODES},
        {"jobId": "a", "rounds": 2, "nodes": [{"id": "n" * 100000}]},
        {"jobId": "a", "rounds": 2, "nodes": [{"id": f"N{i}"} for i in range(200)]},
        {"jobId": "a", "rounds": 2, "nodes": NODES, "simulatePoison": ["a"]},
        [1, 2],
    ],
)
def test_fl_train_rejects_bad_body(client, body):
    _assert_4xx(client.post(f"{PREFIX}/fl/train", json=body))


@pytest.mark.parametrize("text", ["{{{{", "", '{"jobId":"a","rounds":2,"nodes":[{"id":"Node-A"}],"dp":{"enabled":true,"epsilon":NaN}}'])
def test_fl_train_rejects_non_json_and_nan(client, text):
    _assert_4xx(_raw(client, "/fl/train", text))


@pytest.mark.parametrize("nodes", [[{"id": "Node-Z"}], [{"id": "Node-A", "samples": 0}], [{"id": "Node-A"}, {"id": "Node-A"}, {"id": "Node-A"}], [{"id": "Node-A", "samples": 10_000_000}]])
def test_fl_train_tolerates_edge_nodes(client, nodes, run_job):
    """未知节点 / samples=0 / 重复 id / 超大 samples → 合法处理并正常结束"""
    job = run_job(rounds=1, nodes=nodes)
    assert job["status"] == "success", job
    assert len(job["rounds"]) == 1


def test_fl_rounds_1_and_eps_tiny(client, run_job):
    job = run_job(rounds=1, dp={"enabled": True, "epsilon": 1e-6, "delta": 1e-5})
    assert job["status"] == "success"
    assert json.dumps(job)  # 可序列化、无 NaN


# ---------------- DQN
@pytest.mark.parametrize(
    "nodes",
    [
        [],
        [{"pv": 1}],
        [{"id": "a", "pv": "abc"}],
        [{"id": "a", "pv": 1e308, "load": 1e308}],
        [{"id": "a", "soc": -50}],
        [{"id": "a", "soc": 500}],
        [{"id": "a", "hour": 99}],
        [{"id": "a", "hour": -1}],
        [{"id": "a", "price": -5}],
        [{"id": f"n{i}"} for i in range(501)],
    ],
)
def test_dqn_rejects_bad_nodes(client, nodes):
    _assert_4xx(client.post(f"{PREFIX}/dqn/dispatch", json={"taskId": "t", "nodes": nodes}))


@pytest.mark.parametrize("text", ['{"taskId":"t","nodes":[{"id":"a","soc":NaN}]}', '{"taskId":"t","nodes":[{"id":"a","pv":Infinity}]}'])
def test_dqn_rejects_nan_inf(client, text):
    _assert_4xx(_raw(client, "/dqn/dispatch", text))


@pytest.mark.parametrize("tw", ["Tzz", "", "2026-08-17T", 5, None])
def test_dqn_time_window_junk_ok(client, tw):
    body = {"taskId": "t", "nodes": [{"id": "a", "soc": 50, "load": 100}]}
    if tw is not None:
        body["timeWindow"] = tw
    r = client.post(f"{PREFIX}/dqn/dispatch", json=body)
    assert r.status_code in (200, 400), r.text
    if r.status_code == 200:
        assert r.json()["actions"][0]["action"] in ("charge", "idle", "discharge")


# ---------------- DeepSeek：脏 context 永远 200
@pytest.mark.parametrize(
    "body",
    [
        {"scene": "dispatch", "context": {"nodes": "abc"}},
        {"scene": "dispatch", "context": {"nodes": [1, 2], "actions": [3], "strategy": "x", "constraintsChecked": 7}},
        {"scene": "dispatch", "context": {"nodes": [{"id": "a", "load": "abc", "pv": None}], "totalReward": "x"}},
        {"scene": "risk", "context": {"factors": [1], "features": "x"}},
        {"scene": "risk", "context": {"factors": [{"weight": "x", "score": "y"}]}},
        {"scene": "data", "context": {"byLevel": [1], "byType": "x", "results": [2]}},
        {"scene": "audit", "context": {"identityOps": "x", "permissionOps": [1], "riskEvents": [1], "evidence": {"byCategory": [1]}}},
        {"scene": "zzz", "context": {}},
        {"scene": "qa", "question": "x" * 20000},
    ],
)
def test_deepseek_dirty_context_always_200(client, body):
    r = client.post(f"{PREFIX}/deepseek/analyze", json=body)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["answer"].strip() and len(b["reasoning"]) >= 2 and b["source"] in ("live", "cache", "rule")
    assert isinstance(b["latencyMs"], int) and b["latencyMs"] >= 0


@pytest.mark.parametrize("body", [{"scene": 5}, {"scene": "qa", "context": [1]}, {"scene": "qa", "question": "x" * 20001}, {"scene": "x" * 65}])
def test_deepseek_rejects_wrong_types(client, body):
    _assert_4xx(client.post(f"{PREFIX}/deepseek/analyze", json=body))


# ---------------- classify / risk：脏字段按默认值处理，不 500
@pytest.mark.parametrize(
    "rec",
    [
        {"volume": "abc"},
        {"volume": [1]},
        {"volume": -1},
        {"volume": 1e308},
        {"fields": "gps"},
        {"fields": [{"a": 1}, None, 3]},
        {"freq": {"a": 1}},
        {"dataType": [1]},
        {},
    ],
)
def test_classify_dirty_record_ok(client, rec):
    r = client.post(f"{PREFIX}/classify", json={"records": [rec]})
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["level"] in ("L1", "L2", "L3", "L4") and 0 <= res["score"] <= 1 and res["cluster"] in (0, 1, 2)


def test_classify_nan_volume_and_limits(client):
    assert _raw(client, "/classify", '{"records":[{"volume":NaN}]}').status_code == 200
    _assert_4xx(client.post(f"{PREFIX}/classify", json={"records": [1]}))
    _assert_4xx(client.post(f"{PREFIX}/classify", json={"records": [{}] * 5001}))


@pytest.mark.parametrize(
    "features",
    [{"queryFreq": "abc"}, {"queryFreq": [1]}, {"queryFreq": -100}, {"epsilonRemaining": -5}, {"epsilonRemaining": "x"}, {"dataGranularity": {"a": 1}}, {"exposedFields": 1e308}, None],
)
def test_risk_dirty_features_ok(client, features):
    r = client.post(f"{PREFIX}/risk/assess", json={"nodeId": "n", "features": features})
    assert r.status_code == 200, r.text
    b = r.json()
    assert 0 <= b["riskScore"] <= 100 and b["level"] in ("low", "medium", "high", "critical")
    assert all(0 <= f["score"] <= 100 for f in b["factors"]), b["factors"]


def test_risk_nan_and_wrong_types(client):
    assert _raw(client, "/risk/assess", '{"nodeId":"n","features":{"queryFreq":NaN}}').status_code == 200
    _assert_4xx(client.post(f"{PREFIX}/risk/assess", json={"nodeId": "n", "features": [1]}))
    _assert_4xx(client.post(f"{PREFIX}/risk/assess", json={"features": {}}))
