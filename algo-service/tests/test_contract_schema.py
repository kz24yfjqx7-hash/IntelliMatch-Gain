"""
契约第三部分 6 个接口的响应结构逐字段断言（字段名 / 类型 / 枚举）。
依据：contract/API-CONTRACT.md §3.1~§3.6。
"""
import re
import time

import pytest

from conftest import PREFIX, start_job, wait_job

HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX_ANY = re.compile(r"^(sm3:|sha256:)?[0-9a-f]{64}$")


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# ---------------------------------------------------------------- 3.1 health
def test_health_schema(client, trace_id):
    r = client.get(f"{PREFIX}/health", headers={"X-Trace-Id": trace_id})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]
    assert isinstance(body["models"], dict)
    assert body["models"]["dqn"] == "loaded"
    assert body["models"]["deepseek"] in ("live", "cache")
    # X-Trace-Id 透传回写
    assert r.headers.get("X-Trace-Id") == trace_id


def test_trace_id_echo_on_every_endpoint(client, trace_id):
    """所有接口都应回写 X-Trace-Id（契约 §1.2 / DEV-PLAN §2）"""
    calls = [
        ("get", f"{PREFIX}/health", None),
        ("post", f"{PREFIX}/classify", {"records": [{"dataType": "pv", "fields": ["power"], "freq": "hour", "volume": 24}]}),
        ("post", f"{PREFIX}/risk/assess", {"nodeId": "Node-A", "features": {"queryFreq": 1, "dataGranularity": "hour", "exposedFields": 1, "epsilonRemaining": 0.9}}),
        ("post", f"{PREFIX}/deepseek/analyze", {"scene": "qa", "context": {}, "question": "hi"}),
        ("post", f"{PREFIX}/dqn/dispatch", {"taskId": "dp-trace", "timeWindow": "x", "nodes": [{"id": "Node-A", "pv": 45.3, "load": 120, "soc": 65, "storage": -12.0, "price": 0.62}]}),
    ]
    for method, url, body in calls:
        r = getattr(client, method)(url, json=body, headers={"X-Trace-Id": trace_id}) if body is not None else client.get(url, headers={"X-Trace-Id": trace_id})
        assert r.status_code == 200, (url, r.text)
        assert r.headers.get("X-Trace-Id") == trace_id, url


def test_trace_id_generated_when_absent(client):
    """未带 X-Trace-Id 时也应返回一个（可选但推荐）；至少不能报错"""
    r = client.get(f"{PREFIX}/health")
    assert r.status_code == 200


# ---------------------------------------------------------------- 3.2 FL
def test_fl_train_and_jobs_schema(client, trace_id):
    job_id = f"fl-schema-{int(time.time()*1000)}"
    r = start_job(client, job_id, rounds=3, dp={"enabled": True, "epsilon": 1.0, "delta": 1e-5}, topk={"enabled": True, "ratio": 0.1}, headers={"X-Trace-Id": trace_id})
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Trace-Id") == trace_id
    body = r.json()
    assert body == {"jobId": job_id, "status": "running"} or (body["jobId"] == job_id and body["status"] in ("running", "created"))

    job = wait_job(client, job_id)
    assert job["jobId"] == job_id
    assert job["status"] in ("created", "running", "success", "failed", "cancelled")
    assert job["status"] == "success"
    assert job["totalRounds"] == 3
    assert isinstance(job["currentRound"], int) and job["currentRound"] == 3
    assert isinstance(job["rounds"], list) and len(job["rounds"]) == 3
    assert "modelVersion" in job and isinstance(job["modelVersion"], str)
    assert "anomaly" in job
    assert job["anomaly"] is None or (isinstance(job["anomaly"], dict) and "type" in job["anomaly"])
    for i, rd in enumerate(job["rounds"], start=1):
        assert rd["round"] == i
        for k in ("loss", "acc", "compressionRatio", "epsilonSpent"):
            assert _is_num(rd[k]), (k, rd)
        assert 0.0 <= rd["acc"] <= 1.0
        assert 0.0 <= rd["compressionRatio"] <= 100.0
        assert isinstance(rd["gradientHash"], str) and HEX_ANY.match(rd["gradientHash"]), rd["gradientHash"]
        assert isinstance(rd["nodeContributions"], list) and rd["nodeContributions"]
        for nc in rd["nodeContributions"]:
            assert set(nc) >= {"nodeId", "weight", "localLoss"}
            assert isinstance(nc["nodeId"], str)
            assert _is_num(nc["weight"]) and 0 <= nc["weight"] <= 1
            assert _is_num(nc["localLoss"])


def test_fl_job_not_found(client):
    r = client.get(f"{PREFIX}/fl/jobs/fl-not-exist-xyz")
    assert r.status_code == 404
    assert "error" in r.json()


def test_fl_cancel_schema(client):
    job_id = f"fl-cancel-schema-{int(time.time()*1000)}"
    r = start_job(client, job_id, rounds=50)
    assert r.status_code == 200
    r = client.post(f"{PREFIX}/fl/jobs/{job_id}/cancel")
    assert r.status_code == 200, r.text
    assert r.json() == {"jobId": job_id, "status": "cancelled"}
    r = client.post(f"{PREFIX}/fl/jobs/fl-not-exist-xyz/cancel")
    assert r.status_code == 404


def test_fl_train_bad_request(client):
    r = client.post(f"{PREFIX}/fl/train", json={"rounds": 3})
    assert r.status_code in (400, 422)
    # 契约：失败返回 {"error": "..."}
    assert "error" in r.json(), r.text


# ---------------------------------------------------------------- 3.3 DQN
DQN_NODES = [
    {"id": "Node-A", "pv": 45.3, "load": 120, "soc": 65, "storage": -12.0, "price": 0.62},
    {"id": "Node-B", "pv": 32.1, "load": 85, "soc": 78, "storage": 8.5, "price": 0.62},
    {"id": "Node-C", "pv": 28.7, "load": 150, "soc": 42, "storage": -25.3, "price": 0.62},
    {"id": "Node-D", "pv": 38.9, "load": 95, "soc": 82, "storage": 5.2, "price": 0.62},
]


def test_dqn_dispatch_schema(client, trace_id):
    r = client.post(f"{PREFIX}/dqn/dispatch", json={"taskId": "dp-000009", "timeWindow": "2026-08-17T15:00~16:00+08:00", "nodes": DQN_NODES}, headers={"X-Trace-Id": trace_id})
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Trace-Id") == trace_id
    b = r.json()
    assert b["taskId"] == "dp-000009"
    assert isinstance(b["actions"], list) and len(b["actions"]) == len(DQN_NODES)
    ids = {a["nodeId"] for a in b["actions"]}
    assert ids == {n["id"] for n in DQN_NODES}
    for a in b["actions"]:
        assert a["action"] in ("charge", "idle", "discharge")
        assert _is_num(a["powerKw"]) and 0 <= a["powerKw"] <= 30
        assert _is_num(a["qValue"])
        assert isinstance(a["reason"], str) and a["reason"]
    assert _is_num(b["totalReward"])
    assert isinstance(b["qTable"], list) and len(b["qTable"]) == len(DQN_NODES)
    for q in b["qTable"]:
        assert set(q) >= {"nodeId", "charge", "idle", "discharge"}
        for k in ("charge", "idle", "discharge"):
            assert _is_num(q[k])
    cc = b["constraintsChecked"]
    assert cc["socMin"] == 20 and cc["socMax"] == 95 and cc["maxPowerKw"] == 30
    assert isinstance(cc["violations"], list)


def test_dqn_dispatch_bad_request(client):
    r = client.post(f"{PREFIX}/dqn/dispatch", json={"taskId": "x", "nodes": []})
    assert r.status_code in (400, 422)
    assert "error" in r.json()


# ---------------------------------------------------------------- 3.4 DeepSeek
@pytest.mark.parametrize("scene", ["dispatch", "risk", "data", "qa", "audit"])
def test_deepseek_analyze_schema(client, scene, trace_id):
    r = client.post(f"{PREFIX}/deepseek/analyze", json={"scene": scene, "context": {"taskId": "dp-000009"}, "question": "为什么选择节点C放电？"}, headers={"X-Trace-Id": trace_id})
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Trace-Id") == trace_id
    b = r.json()
    assert isinstance(b["answer"], str) and b["answer"].strip()
    assert isinstance(b["reasoning"], list) and all(isinstance(x, str) for x in b["reasoning"])
    assert b["source"] in ("live", "cache", "rule")
    assert isinstance(b["latencyMs"], int) and b["latencyMs"] >= 0


def test_deepseek_unknown_scene_still_200(client):
    r = client.post(f"{PREFIX}/deepseek/analyze", json={"scene": "weird", "context": {}, "question": "?"})
    assert r.status_code == 200
    assert r.json()["answer"]


# ---------------------------------------------------------------- 3.5 classify
def test_classify_schema(client, trace_id):
    records = [
        {"dataType": "pv", "fields": ["power", "voltage", "gps"], "freq": "minute", "volume": 1440},
        {"dataType": "load", "fields": ["kwh"], "freq": "day", "volume": 30},
        {"dataType": "dispatch", "fields": ["cmd", "owner_id"], "freq": "second", "volume": 86400},
    ]
    r = client.post(f"{PREFIX}/classify", json={"records": records}, headers={"X-Trace-Id": trace_id})
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Trace-Id") == trace_id
    b = r.json()
    assert isinstance(b["results"], list) and len(b["results"]) == len(records)
    for i, res in enumerate(b["results"]):
        assert res["index"] == i
        assert res["level"] in ("L1", "L2", "L3", "L4")
        assert _is_num(res["score"]) and 0 <= res["score"] <= 1
        assert isinstance(res["reason"], str) and res["reason"]
        assert isinstance(res["cluster"], int) and 0 <= res["cluster"] <= 2
        f = res["factors"]
        assert set(f) >= {"sensitivity", "granularity", "volume"}
        for k in ("sensitivity", "granularity", "volume"):
            assert _is_num(f[k]) and 0 <= f[k] <= 1
    cc = b["clusterCenters"]
    assert isinstance(cc, list) and 1 <= len(cc) <= 3
    for c in cc:
        assert isinstance(c, list) and len(c) >= 2 and all(_is_num(v) for v in c)


def test_classify_bad_request(client):
    r = client.post(f"{PREFIX}/classify", json={"records": []})
    assert r.status_code in (400, 422)
    assert "error" in r.json()


# ---------------------------------------------------------------- 3.6 risk
def test_risk_assess_schema(client, trace_id):
    r = client.post(
        f"{PREFIX}/risk/assess",
        json={"nodeId": "Node-A", "features": {"queryFreq": 12, "dataGranularity": "minute", "exposedFields": 6, "epsilonRemaining": 0.58}},
        headers={"X-Trace-Id": trace_id},
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Trace-Id") == trace_id
    b = r.json()
    assert b["nodeId"] == "Node-A"
    assert _is_num(b["riskScore"]) and 0 <= b["riskScore"] <= 100
    assert b["level"] in ("low", "medium", "high", "critical")
    assert isinstance(b["factors"], list) and b["factors"]
    for f in b["factors"]:
        assert set(f) >= {"name", "weight", "score"}
        assert isinstance(f["name"], str)
        assert _is_num(f["weight"]) and 0 <= f["weight"] <= 1
        assert _is_num(f["score"]) and 0 <= f["score"] <= 100
        if "desc" in f:
            assert isinstance(f["desc"], str)
    assert isinstance(b["suggestion"], str) and b["suggestion"]


def test_risk_assess_missing_features_defaults_or_400(client):
    """features 缺省字段：应容错（给默认值）或 400，不得 500"""
    r = client.post(f"{PREFIX}/risk/assess", json={"nodeId": "Node-B", "features": {}})
    assert r.status_code in (200, 400, 422), r.text
    if r.status_code == 200:
        assert r.json()["level"] in ("low", "medium", "high", "critical")
