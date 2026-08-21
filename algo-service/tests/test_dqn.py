"""
DQN 调度：约束校验、Q 表结构、推理耗时、checkpoint 存在。
依据：contract §3.3、DEV-PLAN §2 / §7。
"""
import time
from pathlib import Path

import pytest

from conftest import ALGO_DIR, PREFIX

BASE_NODES = [
    {"id": "Node-A", "pv": 45.3, "load": 120, "soc": 65, "storage": -12.0, "price": 0.62},
    {"id": "Node-B", "pv": 32.1, "load": 85, "soc": 78, "storage": 8.5, "price": 0.62},
    {"id": "Node-C", "pv": 28.7, "load": 150, "soc": 42, "storage": -25.3, "price": 0.62},
    {"id": "Node-D", "pv": 38.9, "load": 95, "soc": 82, "storage": 5.2, "price": 0.62},
]


def _dispatch(client, nodes, task_id="dp-qa"):
    r = client.post(f"{PREFIX}/dqn/dispatch", json={"taskId": task_id, "timeWindow": "2026-08-21T15:00~16:00+08:00", "nodes": nodes})
    assert r.status_code == 200, r.text
    return r.json()


def test_checkpoint_exists():
    p = ALGO_DIR / "models" / "dqn.npz"
    assert p.exists(), f"缺少 DQN checkpoint {p}"
    assert p.stat().st_size > 1000


def test_low_soc_never_discharges(client):
    nodes = [dict(n) for n in BASE_NODES]
    nodes[2]["soc"] = 15  # Node-C
    for price in (0.3, 0.62, 1.2):
        for n in nodes:
            n["price"] = price
        b = _dispatch(client, nodes)
        act = {a["nodeId"]: a for a in b["actions"]}
        assert act["Node-C"]["action"] != "discharge", act["Node-C"]
        viol = [v for v in b["constraintsChecked"]["violations"] if v["nodeId"] == "Node-C"]
        assert viol, "SOC=15 触发 socMin 约束应记入 violations"


def test_high_soc_never_charges(client):
    nodes = [dict(n) for n in BASE_NODES]
    nodes[0]["soc"] = 97  # Node-A
    for price in (0.3, 0.62, 1.2):
        for n in nodes:
            n["price"] = price
        b = _dispatch(client, nodes)
        act = {a["nodeId"]: a for a in b["actions"]}
        assert act["Node-A"]["action"] != "charge", act["Node-A"]
        viol = [v for v in b["constraintsChecked"]["violations"] if v["nodeId"] == "Node-A"]
        assert viol, "SOC=97 触发 socMax 约束应记入 violations"


def test_power_within_limit(client):
    extremes = [
        {"id": "N1", "pv": 0, "load": 300, "soc": 94, "storage": 0, "price": 1.5},
        {"id": "N2", "pv": 90, "load": 10, "soc": 21, "storage": 0, "price": 0.1},
        {"id": "N3", "pv": 50, "load": 50, "soc": 50, "storage": 0, "price": 0.6},
    ]
    b = _dispatch(client, extremes)
    for a in b["actions"]:
        assert 0 <= a["powerKw"] <= 30, a
        if a["action"] == "idle":
            assert a["powerKw"] == 0, a
        else:
            assert a["powerKw"] > 0, a
    # 若越限 (不应发生) 必须被记录
    for v in b["constraintsChecked"]["violations"]:
        assert v["constraint"] in ("socMin", "socMax", "maxPowerKw")
        assert "nodeId" in v


def test_qtable_has_three_actions_per_node(client):
    b = _dispatch(client, BASE_NODES)
    ids = [q["nodeId"] for q in b["qTable"]]
    assert sorted(ids) == sorted(n["id"] for n in BASE_NODES)
    act = {a["nodeId"]: a for a in b["actions"]}
    for q in b["qTable"]:
        assert set(q) >= {"nodeId", "charge", "idle", "discharge"}
        # 所选动作的 qValue 与 qTable 中对应项一致
        a = act[q["nodeId"]]
        assert abs(q[a["action"]] - a["qValue"]) < 1e-3, (q, a)


def test_action_enum_and_reason(client):
    b = _dispatch(client, BASE_NODES)
    for a in b["actions"]:
        assert a["action"] in ("charge", "idle", "discharge")
        assert isinstance(a["reason"], str) and len(a["reason"]) >= 4


def test_inference_latency_under_200ms(client):
    _dispatch(client, BASE_NODES)  # 预热
    t0 = time.perf_counter()
    for _ in range(5):
        _dispatch(client, BASE_NODES)
    avg_ms = (time.perf_counter() - t0) / 5 * 1000
    assert avg_ms < 200, f"平均推理耗时 {avg_ms:.1f}ms"


def test_policy_reacts_to_state(client):
    """非硬编码：不同状态（SOC/负荷/电价）下 Q 值应不同"""
    a = _dispatch(client, BASE_NODES)
    nodes2 = [dict(n, soc=min(94, n["soc"] + 20), load=n["load"] * 2, price=1.3) for n in BASE_NODES]
    b = _dispatch(client, nodes2)
    assert a["qTable"] != b["qTable"]


def test_dispatch_is_deterministic(client):
    a = _dispatch(client, BASE_NODES)
    b = _dispatch(client, BASE_NODES)
    assert a["actions"] == b["actions"]
    assert a["qTable"] == b["qTable"]


def test_high_price_high_load_prefers_discharge_somewhere(client):
    """高电价 + 高负荷 + SOC 充足：至少有一个节点放电（策略合理性弱检验）"""
    nodes = [dict(n, load=n["load"] * 1.8, soc=80, price=1.4, pv=5.0) for n in BASE_NODES]
    b = _dispatch(client, nodes)
    acts = [a["action"] for a in b["actions"]]
    assert "discharge" in acts, acts


def test_dispatcher_module_direct():
    """直接加载模块：checkpoint 能被 load_checkpoint 读出"""
    from algorithms import dqn

    q, meta = dqn.load_checkpoint()
    assert q is not None
    assert isinstance(meta, dict)
