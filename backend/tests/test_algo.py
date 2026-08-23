"""算法代理层测试。契约 2.9 ~ 2.12。

这一层不算算法，所以测的全是「治理」：权限、验签、落库、上链、推送、降级。
"""
import pytest


@pytest.fixture
def admin_with_did(client, login, SessionFactory):
    """给 admin 绑一个真实存在的 DID 和托管密钥，才能演示签名下发。"""
    from modules.auth.model import SysUser

    identity = client.post("/api/v1/did/register", headers=login("admin"), json={
        "subjectType": "user", "subjectName": "系统管理员", "custody": True,
    }).json()["data"]

    with SessionFactory() as db:
        user = db.get(SysUser, 1)
        user.did = identity["did"]
        db.commit()

    headers = login("admin")          # 重新登录，让 token 带上新的 did
    return {"headers": headers, **identity}


# ================================================================ 联邦学习

def _create_fl(client, headers, **kw):
    return client.post("/api/v1/fl/tasks", headers=headers, json={
        "name": kw.get("name", "负荷预测联合建模-测试"),
        "nodeIds": kw.get("nodeIds", ["Node-A", "Node-B", "Node-C", "Node-D"]),
        "rounds": kw.get("rounds", 10),
        "dp": {"enabled": True, "epsilon": 1.0, "delta": 1e-5},
        "topk": {"enabled": True, "ratio": 0.1},
    })


def test_创建训练任务(client, login):
    data = _create_fl(client, login("admin")).json()["data"]
    assert data["id"].startswith("fl-")
    assert data["status"] == "created"
    assert data["traceId"].startswith("tr-")


def test_节点不存在则拒绝创建(client, login):
    r = _create_fl(client, login("admin"), nodeIds=["Node-A", "Node-Z"])
    assert r.status_code == 404 and "Node-Z" in r.json()["message"]


def test_无algo_execute权限不能创建(client, login):
    """契约明确：联邦聚合仅 sys_admin 与 grid_dispatcher 可解锁。"""
    r = _create_fl(client, login("vpp"))
    assert r.status_code == 403 and r.json()["code"] == 1003
    assert client.post("/api/v1/fl/tasks", headers=login("grid"), json={
        "name": "调度员建模", "nodeIds": ["Node-A"], "rounds": 3,
    }).status_code == 200


def test_任务详情含节点与样本量(client, login):
    task_id = _create_fl(client, login("admin")).json()["data"]["id"]
    data = client.get(f"/api/v1/fl/tasks/{task_id}", headers=login("admin")).json()["data"]
    assert data["totalRounds"] == 10
    assert data["dp"] == {"enabled": True, "epsilon": 1.0, "delta": 1e-05, "epsilonSpent": 0.0}
    assert len(data["nodes"]) == 4
    # 样本量是真实的历史指标条数，不是编的
    assert all(n["samples"] == 48 for n in data["nodes"])
    assert data["rounds"] == []


def test_启动训练后状态变running(client, login, algo_stub):
    task_id = _create_fl(client, login("admin")).json()["data"]["id"]
    r = client.post(f"/api/v1/fl/tasks/{task_id}/start", headers=login("admin"))
    assert r.json()["data"]["status"] == "running"
    detail = client.get(f"/api/v1/fl/tasks/{task_id}", headers=login("admin")).json()["data"]
    assert detail["status"] in ("running", "success")


def test_算法服务不可用时启动失败返回2001(client, login, algo_down):
    task_id = _create_fl(client, login("admin")).json()["data"]["id"]
    r = client.post(f"/api/v1/fl/tasks/{task_id}/start", headers=login("admin"))
    assert r.status_code == 502 and r.json()["code"] == 2001


def test_每轮结果落库并逐轮上链(client, login):
    """直接喂一份算法服务的响应，验证落库 / 上链 / 幂等。"""
    from modules.algo.service import persist_fl_progress

    admin = login("admin")
    task_id = _create_fl(client, admin, rounds=3).json()["data"]["id"]

    job = {
        "jobId": task_id, "status": "running", "currentRound": 2, "totalRounds": 3,
        "rounds": [
            {"round": 1, "loss": 0.412, "acc": 0.783, "compressionRatio": 90.0,
             "epsilonSpent": 0.10, "gradientHash": "sha256:aaa",
             "nodeContributions": [{"nodeId": "Node-A", "weight": 0.4, "localLoss": 0.43}]},
            {"round": 2, "loss": 0.331, "acc": 0.842, "compressionRatio": 90.5,
             "epsilonSpent": 0.20, "gradientHash": "sha256:bbb",
             "nodeContributions": [{"nodeId": "Node-A", "weight": 0.4, "localLoss": 0.35}]},
        ],
        "modelVersion": None, "anomaly": None,
    }
    assert persist_fl_progress(task_id, job, "tr-fl-test") is False

    rounds = client.get(f"/api/v1/fl/tasks/{task_id}/rounds",
                        headers=admin).json()["data"]["items"]
    assert len(rounds) == 2
    assert rounds[0]["loss"] == 0.412
    assert rounds[0]["evidenceId"].startswith("ev-")     # 每轮梯度哈希上链
    assert rounds[1]["gradientHash"] == "sha256:bbb"

    # 轮询会反复拿到旧数据，重复落库必须是幂等的
    persist_fl_progress(task_id, job, "tr-fl-test")
    again = client.get(f"/api/v1/fl/tasks/{task_id}/rounds",
                       headers=admin).json()["data"]["items"]
    assert len(again) == 2


def test_任务完成生成模型版本(client, login):
    from modules.algo.service import persist_fl_progress

    admin = login("admin")
    task_id = _create_fl(client, admin, rounds=1).json()["data"]["id"]
    job = {
        "jobId": task_id, "status": "success", "currentRound": 1, "totalRounds": 1,
        "rounds": [{"round": 1, "loss": 0.21, "acc": 0.91, "compressionRatio": 90.0,
                    "epsilonSpent": 1.0, "gradientHash": "sha256:ccc"}],
        "modelVersion": "v99", "anomaly": None,
    }
    assert persist_fl_progress(task_id, job, "tr-fl-done") is True

    detail = client.get(f"/api/v1/fl/tasks/{task_id}", headers=admin).json()["data"]
    assert detail["status"] == "success"
    assert detail["modelVersion"] == "v99"

    models = client.get("/api/v1/fl/models", headers=admin).json()["data"]["items"]
    v99 = next(m for m in models if m["version"] == "v99")
    assert v99["status"] == "draft"
    assert v99["metrics"]["acc"] == 0.91

    published = client.post("/api/v1/fl/models/v99/publish", headers=admin).json()["data"]
    assert published["status"] == "published"
    assert published["evidenceId"].startswith("ev-")


def test_发布模型需要algo_execute权限(client, login):
    """发布模型是 high 风险写操作，权限 algo:execute（admin/grid），与前端 v-permission 一致。
    持 model:read 但无 algo:execute 的 vpp/regulator/edge 直接调接口必须被 1003 拦（原来 model:read 太松）。"""
    for role in ("vpp", "subject", "regulator", "edge"):
        r = client.post("/api/v1/fl/models/v-any/publish", headers=login(role))
        assert r.status_code == 403 and r.json()["code"] == 1003, f"{role} 竟能发布模型：{r.json()}"
    # grid 有 algo:execute：不会被 1003 拦（版本不存在会走到业务层，另一种错误码，但不是权限拒绝）
    r = client.post("/api/v1/fl/models/v-none/publish", headers=login("grid"))
    assert r.json()["code"] != 1003, f"grid 应有发布权限：{r.json()}"


def test_梯度异常触发R05高危告警(client, login):
    """契约 3.2 明确要求：anomaly 非 null 必须生成 R05_SUSPICIOUS_GRAD 高危审计日志。"""
    from unittest.mock import patch

    from modules.algo.service import persist_fl_progress

    admin = login("admin")
    task_id = _create_fl(client, admin, rounds=1).json()["data"]["id"]
    job = {
        "jobId": task_id, "status": "running", "currentRound": 1, "totalRounds": 1,
        "rounds": [{"round": 1, "loss": 0.4, "acc": 0.8, "compressionRatio": 90.0,
                    "epsilonSpent": 0.1, "gradientHash": "sha256:ddd"}],
        "anomaly": {"type": "gradient_poisoning", "nodeId": "Node-C",
                    "detail": "梯度范数超出中位数 8 倍"},
    }
    with patch("modules.audit.rules.fire") as fire:
        persist_fl_progress(task_id, job, "tr-fl-anomaly")
        assert fire.called
        args, kwargs = fire.call_args
        assert args[0] == "R05_SUSPICIOUS_GRAD"
        assert kwargs["immediate"] is True
        assert "Node-C" in kwargs["message"]


def test_B029_异常与失败原因落库并随详情返回_R05按任务去重(client, login, fake_redis):
    """B-029：算法服务上报的 anomaly / error 原来只用来触发 R05 就丢了，任务详情永远没有异常；
    且 R05 的去重主体为空，所有任务共用一个桶——10 分钟内第二个异常任务不告警。"""
    from modules.algo.service import persist_fl_progress

    admin = login("admin")
    before = client.get("/api/v1/audit/alerts?ruleCode=R05_SUSPICIOUS_GRAD&size=50",
                        headers=admin).json()["data"]["total"]
    anomaly = {"type": "training_diverged", "nodeId": None, "round": 4,
               "detail": "第 4 轮测试 loss=23.56 已达历史最优 0.2091 的 20 倍以上；参与节点仅 1 个。建议增加参与节点或放宽 ε 后重试"}
    ids = []
    for i in range(2):                                   # 两个任务，同一窗口内先后发散
        task_id = _create_fl(client, admin, rounds=4).json()["data"]["id"]
        ids.append(task_id)
        job = {"jobId": task_id, "status": "running", "currentRound": 4, "totalRounds": 4,
               "rounds": [{"round": 4, "loss": 23.56, "acc": 0.0, "compressionRatio": 90.0,
                           "epsilonSpent": 0.3, "gradientHash": f"sha256:{i}{i}{i}"}],
               "anomaly": anomaly}
        persist_fl_progress(task_id, job, f"tr-fl-div-{i}")
        persist_fl_progress(task_id, job, f"tr-fl-div-{i}")        # 轮询重复送同一异常：不能再告警
        done = {**job, "status": "failed", "error": "训练发散：" + anomaly["detail"] + "，训练已熔断停止"}
        persist_fl_progress(task_id, done, f"tr-fl-div-{i}")

    for task_id in ids:
        d = client.get(f"/api/v1/fl/tasks/{task_id}", headers=admin).json()["data"]
        assert d["status"] == "failed"
        assert d["anomaly"]["type"] == "training_diverged" and "建议" in d["anomaly"]["detail"]
        assert d["error"].startswith("训练发散：")

    after = client.get("/api/v1/audit/alerts?ruleCode=R05_SUSPICIOUS_GRAD&size=50",
                       headers=admin).json()["data"]
    assert after["total"] == before + 2, "两个任务各告警一次；同一任务重复轮询不重复告警"
    msgs = [a["message"] for a in after["items"][:2]]
    assert all("训练发散" in m for m in msgs) and any(ids[0] in m for m in msgs) and any(ids[1] in m for m in msgs)


def test_取消训练(client, login, algo_stub):
    admin = login("admin")
    task_id = _create_fl(client, admin).json()["data"]["id"]
    client.post(f"/api/v1/fl/tasks/{task_id}/start", headers=admin)
    r = client.post(f"/api/v1/fl/tasks/{task_id}/cancel", headers=admin)
    assert r.json()["data"]["status"] == "cancelled"
    again = client.post(f"/api/v1/fl/tasks/{task_id}/cancel", headers=admin)
    assert again.status_code == 409


# ================================================================ 智能调度

def _create_dispatch(client, headers, nodes=None):
    return client.post("/api/v1/dispatch/tasks", headers=headers, json={
        "name": "晚高峰削峰调度-测试",
        "nodeIds": nodes or ["Node-A", "Node-C", "Node-D"],
        "timeWindow": "2026-08-18T19:00~20:00+08:00",
    })


def test_运行dqn生成策略并解释(client, login, algo_stub):
    admin = login("admin")
    task_id = _create_dispatch(client, admin).json()["data"]["id"]

    data = client.post(f"/api/v1/dispatch/tasks/{task_id}/run",
                       headers=admin).json()["data"]
    assert data["status"] == "success"
    actions = data["strategy"]["actions"]
    assert {a["action"] for a in actions} <= {"charge", "idle", "discharge"}

    # Node-C 负荷 150kW 全网最高、SOC 42% 有裕度 → 必须是放电
    node_c = next(a for a in actions if a["nodeId"] == "Node-C")
    assert node_c["action"] == "discharge"
    assert node_c["powerKw"] > 0

    assert data["explanationSource"] == "cache"
    assert data["explanation"]
    assert data["evidenceId"].startswith("ev-")
    assert data["signPayload"].startswith(f"dispatch:issue:{task_id}:sm3:")


def test_算法服务挂掉时解释降级为规则文本(client, login, algo_down, monkeypatch):
    """断网演示时，策略解释不能是空的。"""
    from modules.algo import client as algo_client

    calls = {"n": 0}
    real = algo_client.call

    def _selective(method, path, *, json=None, timeout=None, raise_on_error=True):
        # DQN 照常返回，只让 DeepSeek 挂掉
        if path == "/dqn/dispatch":
            return {
                "taskId": json["taskId"],
                "actions": [{"nodeId": "Node-C", "action": "discharge", "powerKw": 24.0,
                             "qValue": 8.42, "reason": "负荷最高且SOC充足"}],
                "totalReward": 15.7, "qTable": [],
                "constraintsChecked": {"violations": []},
            }
        calls["n"] += 1
        return None if not raise_on_error else real(method, path, json=json)

    monkeypatch.setattr(algo_client, "call", _selective)

    admin = login("admin")
    task_id = _create_dispatch(client, admin).json()["data"]["id"]
    data = client.post(f"/api/v1/dispatch/tasks/{task_id}/run", headers=admin).json()["data"]

    assert data["explanationSource"] == "rule"
    assert "Node-C" in data["explanation"] and "放电" in data["explanation"]


def test_越权下发被拦截且记高危日志(client, login, algo_stub):
    """答辩演示第 5 步：vpp 尝试下发调度指令 → 被拦 → 审计告警。"""
    admin = login("admin")
    task_id = _create_dispatch(client, admin).json()["data"]["id"]
    client.post(f"/api/v1/dispatch/tasks/{task_id}/run", headers=admin)

    r = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue",
                    headers=login("vpp"), json={})
    assert r.status_code == 403 and r.json()["code"] == 1003
    assert "dispatch:issue" in r.json()["message"]

    logs = client.get("/api/v1/audit/logs?action=dispatch:issue&result=denied&size=10",
                      headers=admin).json()["data"]
    assert logs["total"] >= 1
    assert logs["items"][0]["riskLevel"] == "high"
    assert logs["items"][0]["evidenceId"].startswith("ev-")


def test_有权限但签名无效返回1004(client, login, algo_stub, admin_with_did):
    headers = admin_with_did["headers"]
    task_id = _create_dispatch(client, headers).json()["data"]["id"]
    client.post(f"/api/v1/dispatch/tasks/{task_id}/run", headers=headers)

    r = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers,
                    json={"signature": "ab" * 64})
    assert r.status_code == 403 and r.json()["code"] == 1004


def test_完整下发与回执链路(client, login, algo_stub, admin_with_did):
    from core.gm_crypto import sign as sm2_sign

    headers = admin_with_did["headers"]
    task_id = _create_dispatch(client, headers).json()["data"]["id"]
    run = client.post(f"/api/v1/dispatch/tasks/{task_id}/run", headers=headers).json()["data"]

    # 前端拿 signPayload 用自己保存的私钥签名，平台侧验签
    signature = sm2_sign(run["signPayload"], admin_with_did["privateKey"],
                         admin_with_did["publicKey"])
    issued = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers,
                         json={"signature": signature}).json()["data"]
    assert issued["issued"] is True
    assert issued["commandId"].startswith("cmd-")
    assert issued["signerDid"] == admin_with_did["did"]
    assert issued["targets"]

    # 重复下发要挡住
    again = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers,
                        json={"signature": signature})
    assert again.status_code == 409

    # 边缘节点逐个回执
    for i, node_id in enumerate(issued["targets"]):
        ack = client.post(f"/api/v1/dispatch/tasks/{task_id}/ack", headers=headers,
                          json={"nodeId": node_id, "accepted": True}).json()["data"]
        expected = "all" if i == len(issued["targets"]) - 1 else "partial"
        assert ack["ackStatus"] == expected


def test_没保存私钥时用托管密钥补签(client, login, algo_stub, admin_with_did):
    """前端没存私钥也要能走通完整的签名→验签→上链流程，验签环节一个都不少。"""
    headers = admin_with_did["headers"]
    task_id = _create_dispatch(client, headers).json()["data"]["id"]
    client.post(f"/api/v1/dispatch/tasks/{task_id}/run", headers=headers)

    issued = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers,
                         json={}).json()["data"]
    assert issued["issued"] is True


def test_未生成策略不能下发(client, login, admin_with_did):
    headers = admin_with_did["headers"]
    task_id = _create_dispatch(client, headers).json()["data"]["id"]
    r = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers, json={})
    assert r.status_code == 409 and "先运行" in r.json()["message"]


def test_签名绑定策略内容(client, login, algo_stub):
    """策略变了签名必须失效，否则改了内容还能复用旧签名。"""
    from modules.algo.service import build_sign_message

    admin = login("admin")
    task_id = _create_dispatch(client, admin).json()["data"]["id"]
    before = build_sign_message(task_id)
    client.post(f"/api/v1/dispatch/tasks/{task_id}/run", headers=admin)
    after = build_sign_message(task_id)
    assert before != after


# ================================================================ AI 分析与风险评估

def test_ai分析走算法服务(client, login, algo_stub):
    data = client.post("/api/v1/ai/analyze", headers=login("admin"), json={
        "scene": "dispatch", "context": {"taskId": "dp-000001"},
        "question": "为什么选择节点C放电？",
    }).json()["data"]
    assert data["source"] == "cache"
    assert data["answer"]
    assert isinstance(data["reasoning"], list) and data["reasoning"]
    assert data["evidenceId"].startswith("ev-")


def test_ai分析在算法服务挂掉时仍返回200(client, login, algo_down):
    """契约 2.11：离线兜底是硬性要求，绝不返回错误。"""
    r = client.post("/api/v1/ai/analyze", headers=login("admin"), json={
        "scene": "risk", "context": {}, "question": "当前风险来自哪里？",
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["source"] == "rule"
    assert "隐私" in data["answer"] or "风险" in data["answer"]


def test_ai分析所有角色只读且不能触发下发(client, login, algo_stub):
    """契约约束：DeepSeek 分析接口对所有角色只读，无调度下发权限。"""
    for role in ("admin", "grid", "vpp", "regulator"):
        r = client.post("/api/v1/ai/analyze", headers=login(role), json={
            "scene": "qa", "context": {}, "question": "什么是可信数据空间？",
        })
        assert r.status_code == 200, role


def test_ai历史可按场景筛选(client, login, algo_stub):
    admin = login("admin")
    client.post("/api/v1/ai/analyze", headers=admin,
                json={"scene": "audit", "context": {}, "question": "今日审计情况"})
    data = client.get("/api/v1/ai/history?scene=audit&size=10", headers=admin).json()["data"]
    assert data["total"] >= 1
    assert all(i["scene"] == "audit" for i in data["items"])


def test_风险评估结构符合契约(client, login, algo_stub):
    data = client.post("/api/v1/risk/assess", headers=login("admin"), json={
        "nodeId": "Node-A",
        "features": {"queryFreq": 12, "dataGranularity": "minute", "exposedFields": 6},
    }).json()["data"]
    assert data["nodeId"] == "Node-A"
    assert data["level"] in ("low", "medium", "high", "critical")
    assert 0 <= data["riskScore"] <= 100
    assert sum(f["weight"] for f in data["factors"]) == pytest.approx(1.0)
    assert data["suggestion"]
    assert data["evidenceId"].startswith("ev-")


def test_风险评估本地兜底(client, login, algo_down):
    data = client.post("/api/v1/risk/assess", headers=login("admin"), json={
        "nodeId": "Node-C",
        "features": {"queryFreq": 20, "dataGranularity": "second",
                     "exposedFields": 8, "epsilonRemaining": 0.05},
    }).json()["data"]
    assert data["source"] == "rule"
    assert data["level"] in ("high", "critical")     # 全是高风险特征
    assert "ε" in data["suggestion"]


def test_风险历史可按节点筛选(client, login, algo_down):
    admin = login("admin")
    client.post("/api/v1/risk/assess", headers=admin,
                json={"nodeId": "Node-B", "features": {"queryFreq": 3}})
    data = client.get("/api/v1/risk/history?nodeId=Node-B&size=10",
                      headers=admin).json()["data"]
    assert data["total"] >= 1
    assert all(i["nodeId"] == "Node-B" for i in data["items"])


def test_不存在的节点无法评估(client, login, algo_stub):
    r = client.post("/api/v1/risk/assess", headers=login("admin"),
                    json={"nodeId": "Node-Z", "features": {}})
    assert r.status_code == 404 and r.json()["code"] == 1005
