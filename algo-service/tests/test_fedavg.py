"""
FedAvg 真实性与 DP / Top-k / 投毒检测 / 任务管理行为测试。
依据：contract §3.2「乙必须实现真实的 FedAvg」、DEV-PLAN §2 / §7 DoD。
"""
import re
import time

import numpy as np
import pytest

from conftest import PREFIX, start_job, wait_job

HEX64 = re.compile(r"^(sm3:|sha256:)?[0-9a-f]{64}$")


def _losses(job):
    return [r["loss"] for r in job["rounds"]]


def test_loss_converges_over_10_rounds(run_job):
    """DoD：10 轮 loss 整体趋势下降（末 3 轮均值 < 首 3 轮均值）"""
    job = run_job(rounds=10)
    assert job["status"] == "success"
    losses = _losses(job)
    assert len(losses) == 10
    assert all(np.isfinite(losses))
    head, tail = np.mean(losses[:3]), np.mean(losses[-3:])
    assert tail < head, f"loss 未收敛: {losses}"


def test_acc_in_range_and_not_constant(run_job):
    job = run_job(rounds=8)
    accs = [r["acc"] for r in job["rounds"]]
    assert all(0.0 <= a <= 1.0 for a in accs)
    # 真训练不会每轮 acc 完全相同
    assert len(set(round(a, 6) for a in accs)) > 1, accs


def test_dp_epsilon_monotonic_and_within_budget(run_job):
    eps_target = 1.0
    job = run_job(rounds=10, dp={"enabled": True, "epsilon": eps_target, "delta": 1e-5})
    spent = [r["epsilonSpent"] for r in job["rounds"]]
    assert spent[0] > 0, "DP 开启后第 1 轮 epsilonSpent 应 > 0"
    assert all(b >= a for a, b in zip(spent, spent[1:])), f"epsilonSpent 必须单调递增: {spent}"
    over = spent[-1] > eps_target + 1e-6
    if over:
        # 超预算则必须上报 anomaly
        assert job["anomaly"] and job["anomaly"]["type"] == "privacy_budget_exhausted", job["anomaly"]
    else:
        assert spent[-1] <= eps_target + 1e-6


def test_dp_disabled_epsilon_zero(run_job):
    job = run_job(rounds=3, dp={"enabled": False})
    assert all(r["epsilonSpent"] == 0 for r in job["rounds"])


def test_dp_tiny_budget_triggers_anomaly(run_job):
    """极小预算、多轮：预算必然耗尽，应出现 privacy_budget_exhausted"""
    job = run_job(rounds=10, dp={"enabled": True, "epsilon": 0.01, "delta": 1e-5})
    spent = [r["epsilonSpent"] for r in job["rounds"]]
    assert job["anomaly"] is not None, f"预算 0.01 跑 10 轮应触发 anomaly，spent={spent}"
    assert job["anomaly"]["type"] == "privacy_budget_exhausted"


def test_topk_compression_ratio(run_job):
    job = run_job(rounds=3, topk={"enabled": True, "ratio": 0.1})
    for r in job["rounds"]:
        assert abs(r["compressionRatio"] - 90.0) < 2.0, r["compressionRatio"]
    job = run_job(rounds=2, topk={"enabled": True, "ratio": 0.5})
    for r in job["rounds"]:
        assert abs(r["compressionRatio"] - 50.0) < 2.0, r["compressionRatio"]


def test_topk_disabled_ratio_zero(run_job):
    job = run_job(rounds=3, topk={"enabled": False, "ratio": 0.1})
    assert all(r["compressionRatio"] == 0 for r in job["rounds"])


def test_gradient_hash_is_hex64_and_unique_per_round(run_job):
    job = run_job(rounds=5)
    hashes = [r["gradientHash"] for r in job["rounds"]]
    for h in hashes:
        assert HEX64.match(h), h
    assert len(set(hashes)) == len(hashes), "每轮梯度哈希不应重复"


def test_node_contributions_weights_sum_to_one(run_job):
    nodes = [{"id": "Node-A", "samples": 480}, {"id": "Node-B", "samples": 320}, {"id": "Node-C", "samples": 200}, {"id": "Node-D", "samples": 100}]
    job = run_job(rounds=3, nodes=nodes)
    for r in job["rounds"]:
        ncs = r["nodeContributions"]
        assert {n["nodeId"] for n in ncs} == {n["id"] for n in nodes}
        assert abs(sum(n["weight"] for n in ncs) - 1.0) < 1e-3, ncs
    # 按样本量加权：Node-A 权重应最大
    last = {n["nodeId"]: n["weight"] for n in job["rounds"][-1]["nodeContributions"]}
    assert last["Node-A"] == max(last.values())
    assert last["Node-A"] > last["Node-D"]


def test_duplicate_job_id_returns_409(client):
    job_id = f"fl-dup-{int(time.time()*1000)}"
    r1 = start_job(client, job_id, rounds=30)
    assert r1.status_code == 200
    r2 = start_job(client, job_id, rounds=30)
    assert r2.status_code == 409, r2.text
    assert "error" in r2.json()
    client.post(f"{PREFIX}/fl/jobs/{job_id}/cancel")


def test_cancel_stops_training(client):
    job_id = f"fl-cancel-{int(time.time()*1000)}"
    r = start_job(client, job_id, rounds=200)
    assert r.status_code == 200
    time.sleep(0.05)
    r = client.post(f"{PREFIX}/fl/jobs/{job_id}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    job = wait_job(client, job_id, timeout=10, until=("cancelled", "failed", "success"))
    assert job["status"] == "cancelled"
    n = len(job["rounds"])
    assert n < 200
    time.sleep(0.3)
    again = client.get(f"{PREFIX}/fl/jobs/{job_id}").json()
    assert again["status"] == "cancelled"
    assert len(again["rounds"]) <= n + 1, "取消后不应继续推进轮次"


def test_curves_differ_with_different_params(run_job):
    """非硬编码：改数据/参数后曲线不同"""
    base = run_job(rounds=5)
    other_nodes = run_job(rounds=5, nodes=[{"id": "Node-A", "samples": 60}, {"id": "Node-C", "samples": 400}])
    with_dp = run_job(rounds=5, dp={"enabled": True, "epsilon": 0.5, "delta": 1e-5})
    assert _losses(base) != _losses(other_nodes)
    assert _losses(base) != _losses(with_dp)
    assert [r["gradientHash"] for r in base["rounds"]] != [r["gradientHash"] for r in other_nodes["rounds"]]


def test_job_polling_shows_progress(client):
    """轮询期间 currentRound 递增且 rounds 长度与 currentRound 一致"""
    job_id = f"fl-progress-{int(time.time()*1000)}"
    r = start_job(client, job_id, rounds=20)
    assert r.status_code == 200
    seen = set()
    for _ in range(200):
        j = client.get(f"{PREFIX}/fl/jobs/{job_id}").json()
        assert len(j["rounds"]) == j["currentRound"], j
        seen.add(j["currentRound"])
        if j["status"] == "success":
            break
        time.sleep(0.02)
    assert j["status"] == "success"
    assert j["currentRound"] == 20


def test_simulate_poison_detected(run_job):
    """可选扩展字段 simulatePoison：应检测到 gradient_poisoning 并指向该节点"""
    job = run_job(rounds=5, extra={"simulatePoison": "Node-C"})
    anomaly = job["anomaly"]
    assert anomaly is not None, "投毒演示应产生 anomaly"
    assert anomaly["type"] == "gradient_poisoning"
    assert anomaly["nodeId"] == "Node-C"


def test_no_poison_no_anomaly_by_default(run_job):
    job = run_job(rounds=5)
    assert job["anomaly"] is None, job["anomaly"]


def test_model_version_assigned_on_success(run_job):
    job = run_job(rounds=2)
    assert job["status"] == "success"
    assert isinstance(job["modelVersion"], str) and job["modelVersion"]
