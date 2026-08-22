"""缺陷修复回归测试（fix-algo）。

覆盖 B-014（并发 500 + traceId 全零）、B-012（并发审批 500）、B-015（全流程追踪只有 1 步）、
B-008（非目标节点回执）、B-026（回执不上链）、B-004（托管代签签名不落库）、
B-003（expireAt 允许过去时间）。

并发用例用真线程 + 真会话（TestClient 的每个请求都在独立线程里跑完整中间件链），
不是靠 mock 假装并发。
"""
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.orm.exc import StaleDataError

TRACE_RE = re.compile(r"^tr-\d{8}-[0-9a-f]{8}$")


@pytest.fixture
def concurrent_client(tmp_path, seed, SessionFactory, monkeypatch):
    """并发用例专用的 TestClient：业务表落在**文件版 SQLite** 上。

    conftest 的内存库用 StaticPool，所有会话共用同一条连接，
    多线程同时提交会直接撞 `cannot commit transaction - SQL statements in progress`，
    压根测不出并发语义。这里换成文件库 + 每线程一条连接，
    才是「多线程 + 真实会话 + 真实事务」的并发。

    角色/用户/权限矩阵仍留在内存库（登录与鉴权走那边），
    业务表（存证链、资产、申请、调度）走文件库，两边互不干扰。
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import main
    from core.database import Base, get_db

    engine = create_engine(f"sqlite:///{tmp_path}/concurrency.db",
                           connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    # 把内存库里的账号、角色、权限矩阵与节点原样搬过来（登录与鉴权都要用）
    from modules.auth.model import SysRole, SysUser, SysUserRole
    from modules.node.model import NodeInfo
    from modules.permission.model import SysRolePermission

    def _clone(src, dst, model, columns):
        for row in src.execute(__import__("sqlalchemy").select(model)).scalars().all():
            dst.add(model(**{c: getattr(row, c) for c in columns}))

    with SessionFactory() as src, Factory() as dst:
        _clone(src, dst, SysRole, ["id", "code", "name", "description", "is_builtin"])
        _clone(src, dst, SysUser,
               ["id", "username", "password_hash", "real_name", "org_name", "did", "status"])
        _clone(src, dst, SysUserRole, ["user_id", "role_code"])
        _clone(src, dst, SysRolePermission, ["role_code", "resource_type", "action", "scope"])
        _clone(src, dst, NodeInfo,
               ["id", "name", "status", "model", "capacity_kw", "pv_output",
                "storage_output", "load_kw", "soc", "location"])
        dst.commit()

    # 中间件加载用户资料、权限中心判权、审计落库都直接用 SessionLocal，
    # 并发下它们再去挤内存库那条唯一连接就会随机报错，一并指到文件库上
    import sys

    import core.database as database

    monkeypatch.setattr(database, "SessionLocal", Factory)
    monkeypatch.setattr(database, "engine", engine)
    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", "")
        if name.startswith(("core.", "modules.")) and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", Factory)

    main.app.router.on_startup.clear()

    def _override_get_db():
        with Factory() as session:
            yield session

    # 审计分表用进程内缓存记「这张表已经建过了」，换库必须把缓存清掉，
    # 否则文件库建过之后内存库那边就再也不会建表（反之亦然）
    from modules.audit import sharding

    sharding._known_tables.clear()

    main.app.dependency_overrides[get_db] = _override_get_db
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()
    sharding._known_tables.clear()
    engine.dispose()


def _headers(c, username: str) -> dict:
    default = {"admin": "admin123", "subject": "subject123"}
    resp = c.post("/api/v1/auth/login",
                  json={"username": username, "password": default[username]})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


@pytest.fixture
def admin_with_did(client, login, SessionFactory):
    """给 admin 绑定真实 DID + 托管密钥，才能演示托管代签下发。"""
    from modules.auth.model import SysUser

    identity = client.post("/api/v1/did/register", headers=login("admin"), json={
        "subjectType": "user", "subjectName": "系统管理员", "custody": True,
    }).json()["data"]

    with SessionFactory() as db:
        user = db.get(SysUser, 1)
        user.did = identity["did"]
        db.commit()

    return {"headers": login("admin"), **identity}


def _create_dispatch(client, headers, name="回归调度任务"):
    return client.post("/api/v1/dispatch/tasks", headers=headers, json={
        "name": name, "nodeIds": ["Node-A", "Node-C", "Node-D"],
        "timeWindow": "2026-08-22T19:00~20:00+08:00",
    }).json()["data"]


# ================================================================ B-014

def test_B014_并发风险评估不返回500(concurrent_client, algo_stub):
    """多个客户端同时提交风险评估：要么成功，要么 1006，绝不能 5000。"""
    client = concurrent_client
    headers = _headers(client, "admin")
    barrier = threading.Barrier(6)

    def _assess(i: int):
        barrier.wait()
        return client.post("/api/v1/risk/assess", headers=headers, json={
            "nodeId": "Node-A",
            "features": {"queryFreq": 5 + i, "dataGranularity": "minute",
                         "exposedFields": 4, "epsilonRemaining": 0.5},
        })

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(_assess, range(6)))

    codes = [r.json()["code"] for r in responses]
    assert all(c in (0, 1006) for c in codes), codes
    assert 5000 not in codes
    assert codes.count(0) >= 1
    for r in responses:
        assert TRACE_RE.match(r.json()["traceId"]), r.json()


def test_B014_并发资产登记不返回500(concurrent_client, algo_stub):
    """POST /assets 与风险评估同源：flush 后再 UPDATE，撞锁会退化成 StaleDataError。"""
    client = concurrent_client
    headers = _headers(client, "admin")
    identity = client.post("/api/v1/did/register", headers=headers, json={
        "subjectType": "device", "subjectName": "并发登记设备",
    }).json()["data"]
    barrier = threading.Barrier(6)

    def _register(i: int):
        barrier.wait()
        return client.post("/api/v1/assets", headers=headers, json={
            "name": f"并发登记-{i}", "dataType": "pv", "sourceDid": identity["did"],
            "payload": {"pvOutput": 12.3 + i, "interval": "1min"}, "recordCount": 10,
        })

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(_register, range(6)))

    codes = [r.json()["code"] for r in responses]
    assert all(c in (0, 1006) for c in codes), codes
    assert 5000 not in codes


def test_B014_存证撞锁后重放一次仍然成功(client, login, algo_stub, monkeypatch):
    """模拟「第一次写存证撞死锁 → 事务被 MySQL 回滚」：整段写事务重放即可恢复。"""
    from modules.algo import analysis

    real_write = analysis.write_evidence
    state = {"n": 0}

    def _flaky(db, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise StaleDataError("UPDATE statement on table 'algo_risk_assessment' "
                                 "expected to update 1 row(s); 0 were matched.")
        return real_write(db, **kwargs)

    monkeypatch.setattr(analysis, "write_evidence", _flaky)

    r = client.post("/api/v1/risk/assess", headers=login("admin"), json={
        "nodeId": "Node-A", "features": {"queryFreq": 3, "dataGranularity": "hour"},
    })
    assert r.status_code == 200 and r.json()["code"] == 0
    assert state["n"] == 2                       # 确实重放了一次
    assert r.json()["data"]["evidenceId"].startswith("ev-")


def test_B014_始终冲突时返回1006而不是5000(db):
    """重试用尽以后按契约 1.1 返回 1006（状态冲突），绝不冒泡成 500。"""
    from core.exceptions import ConflictError
    from core.retry import run_with_retry

    calls = {"n": 0}

    def _always_stale():
        calls["n"] += 1
        raise StaleDataError("expected to update 1 row(s); 0 were matched.")

    with pytest.raises(ConflictError) as exc:
        run_with_retry(db, _always_stale, what="单测")
    assert exc.value.code == 1006
    assert calls["n"] == 3


def test_B014_未捕获异常的traceId不退化为全零(SessionFactory, seed, monkeypatch, login):
    """500 响应也必须带真实 traceId，否则 /audit/trace 无从追踪。"""
    from fastapi.testclient import TestClient

    import main
    from core.database import get_db
    from modules.algo import analysis

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟未捕获异常")

    monkeypatch.setattr(analysis, "assess", _boom)
    main.app.router.on_startup.clear()

    def _override_get_db():
        with SessionFactory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(main.app, raise_server_exceptions=False) as c:
            token = c.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin123"}
                           ).json()["data"]["token"]
            r = c.post("/api/v1/risk/assess",
                       headers={"Authorization": f"Bearer {token}",
                                "X-Trace-Id": "tr-20260822-abcdef12"},
                       json={"nodeId": "Node-A", "features": {}})
    finally:
        main.app.dependency_overrides.clear()

    body = r.json()
    assert r.status_code == 500 and body["code"] == 5000
    assert body["traceId"] != "tr-00000000-00000000"
    assert body["traceId"] == "tr-20260822-abcdef12"


# ================================================================ B-012

def _make_application(client, subject_headers):
    return client.post("/api/v1/permissions/apply", headers=subject_headers, json={
        "resourceType": "asset", "resourceId": "1", "action": "read",
        "reason": "并发审批回归用例",
    }).json()["data"]


def test_B012_并发审批同一申请只有一个成功(concurrent_client):
    client = concurrent_client
    admin, subject = _headers(client, "admin"), _headers(client, "subject")
    app_id = _make_application(client, subject)["id"]
    barrier = threading.Barrier(5)

    def _approve(_):
        barrier.wait()
        return client.post(f"/api/v1/permissions/applications/{app_id}/approve", headers=admin,
                           json={"reason": "并发审批"})

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(_approve, range(5)))

    codes = [r.json()["code"] for r in responses]
    assert 5000 not in codes, codes
    assert codes.count(0) == 1, codes
    assert all(c == 1006 for c in codes if c != 0), codes


def test_B012_并发审批与驳回不产生500(concurrent_client):
    client = concurrent_client
    admin, subject = _headers(client, "admin"), _headers(client, "subject")
    app_id = client.post("/api/v1/permissions/apply", headers=subject, json={
        "resourceType": "asset", "resourceId": "2", "action": "read",
        "reason": "审批驳回竞态",
    }).json()["data"]["id"]
    barrier = threading.Barrier(2)

    def _call(action):
        barrier.wait()
        return client.post(f"/api/v1/permissions/applications/{app_id}/{action}", headers=admin,
                           json={"reason": "竞态"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(_call, ["approve", "reject"]))

    codes = [r.json()["code"] for r in responses]
    assert 5000 not in codes, codes
    assert codes.count(0) == 1, codes


# ================================================================ B-003

def test_B003_申请过去的expireAt返回1001(client, login):
    r = client.post("/api/v1/permissions/apply", headers=login("subject"), json={
        "resourceType": "asset", "resourceId": "9", "action": "read",
        "reason": "过期时间校验", "expireAt": "2020-01-01T00:00:00+08:00",
    })
    assert r.status_code == 400 and r.json()["code"] == 1001
    assert "expireAt" in r.json()["message"]


def test_B003_合法的将来expireAt正常受理(client, login):
    r = client.post("/api/v1/permissions/apply", headers=login("subject"), json={
        "resourceType": "asset", "resourceId": "10", "action": "read",
        "reason": "过期时间校验", "expireAt": "2099-01-01T00:00:00+08:00",
    })
    assert r.json()["code"] == 0
    assert r.json()["data"]["expireAt"].startswith("2099-01-01")


# ================================================================ B-008 / B-026 / B-004

def test_B008_非目标节点回执被拒(client, algo_stub, admin_with_did):
    headers = admin_with_did["headers"]
    task_id = _create_dispatch(client, headers, "非目标节点回执")["id"]
    client.post(f"/api/v1/dispatch/tasks/{task_id}/run", headers=headers)
    issued = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers,
                         json={}).json()["data"]

    r = client.post(f"/api/v1/dispatch/tasks/{task_id}/ack", headers=headers,
                    json={"nodeId": "Node-Z", "accepted": True})
    assert r.status_code == 400 and r.json()["code"] == 1001
    assert "Node-Z" in r.json()["message"]

    # 目标节点仍然正常
    ok = client.post(f"/api/v1/dispatch/tasks/{task_id}/ack", headers=headers,
                     json={"nodeId": issued["targets"][0], "accepted": True})
    assert ok.json()["code"] == 0

    # 被拒的回执不能污染 ackDetail
    detail = client.get(f"/api/v1/dispatch/tasks/{task_id}", headers=headers).json()["data"]
    assert all(a["nodeId"] != "Node-Z" for a in detail["ackDetail"])


def test_B026_回执上链并返回evidenceId(client, algo_stub, admin_with_did):
    headers = admin_with_did["headers"]
    task_id = _create_dispatch(client, headers, "回执上链")["id"]
    client.post(f"/api/v1/dispatch/tasks/{task_id}/run", headers=headers)
    issued = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers,
                         json={}).json()["data"]

    ack = client.post(f"/api/v1/dispatch/tasks/{task_id}/ack", headers=headers,
                      json={"nodeId": issued["targets"][0], "accepted": True,
                            "detail": "已按指令放电"}).json()["data"]
    assert ack["evidenceId"].startswith("ev-")

    evidence = client.get(f"/api/v1/evidence/{ack['evidenceId']}",
                          headers=headers).json()["data"]
    assert evidence["payload"]["action"] == "dispatch:ack"
    assert evidence["payload"]["nodeId"] == issued["targets"][0]


def test_B004_托管代签的签名落库且详情回传(client, algo_stub, admin_with_did, SessionFactory):
    """签名留空走托管代签：签名字节必须落库，并在详情里回传给边端复核。"""
    from core.gm_crypto import verify as sm2_verify
    from modules.algo.model import AlgoDispatchTask

    headers = admin_with_did["headers"]
    task_id = _create_dispatch(client, headers, "托管代签落库")["id"]
    client.post(f"/api/v1/dispatch/tasks/{task_id}/run", headers=headers)
    client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers, json={})

    with SessionFactory() as db:
        row = db.query(AlgoDispatchTask).filter_by(task_id=task_id).one()
        assert row.signature, "托管代签的签名必须落库，否则不可追溯"

    detail = client.get(f"/api/v1/dispatch/tasks/{task_id}", headers=headers).json()["data"]
    assert detail["signature"] == row.signature
    # 边端拿 signature + signPayload 就能做真验签
    assert sm2_verify(detail["signPayload"], detail["signature"],
                      admin_with_did["publicKey"]) is True


# ================================================================ B-015

def test_B015_调度全流程共用同一traceId(client, algo_stub, admin_with_did):
    headers = admin_with_did["headers"]
    task = _create_dispatch(client, headers, "全流程追踪")
    trace_id = task["traceId"]
    task_id = task["id"]

    run = client.post(f"/api/v1/dispatch/tasks/{task_id}/run", headers=headers).json()
    assert run["traceId"] == trace_id
    issued = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers,
                         json={}).json()
    assert issued["traceId"] == trace_id
    ack = client.post(f"/api/v1/dispatch/tasks/{task_id}/ack", headers=headers,
                      json={"nodeId": issued["data"]["targets"][0], "accepted": True}).json()
    assert ack["traceId"] == trace_id

    trace = client.get(f"/api/v1/audit/trace/{trace_id}", headers=headers).json()["data"]
    actions = [s["action"] for s in trace["steps"]]
    assert "dispatch:create" in actions
    assert "dispatch:run" in actions
    assert "dispatch:issue" in actions
    assert "dispatch:ack" in actions
    assert trace["summary"]["steps"] >= 4


def test_B015_联邦学习每轮都进入追踪时间轴(client, login, algo_stub):
    from modules.algo.service import persist_fl_progress

    admin = login("admin")
    created = client.post("/api/v1/fl/tasks", headers=admin, json={
        "name": "全流程追踪-FL", "nodeIds": ["Node-A", "Node-B"], "rounds": 2,
    }).json()["data"]
    trace_id, task_id = created["traceId"], created["id"]

    started = client.post(f"/api/v1/fl/tasks/{task_id}/start", headers=admin).json()
    # 启动请求沿用创建时的 traceId，后台每轮上链也用它
    assert started["data"]["traceId"] == trace_id

    persist_fl_progress(task_id, {
        "jobId": task_id, "status": "success", "currentRound": 2, "totalRounds": 2,
        "rounds": [
            {"round": 1, "loss": 0.5, "acc": 0.7, "gradientHash": "sha256:r1"},
            {"round": 2, "loss": 0.3, "acc": 0.9, "gradientHash": "sha256:r2"},
        ],
        "modelVersion": None, "anomaly": None,
    }, trace_id)

    trace = client.get(f"/api/v1/audit/trace/{trace_id}", headers=admin).json()["data"]
    actions = [s["action"] for s in trace["steps"]]
    assert "fl:create" in actions
    assert "fl:train" in actions
    assert actions.count("fl:round") == 2
    assert "fl:finish" in actions
    assert trace["summary"]["steps"] >= 5
