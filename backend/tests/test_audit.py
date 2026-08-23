"""安全审计测试：声明式埋点、按月分表、五类风险规则、追踪、报告、导出。"""
import time
from datetime import datetime, timedelta

import pytest


def _logs(client, headers, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/api/v1/audit/logs?{query}", headers=headers).json()["data"]


# ---------------------------------------------------------------- 声明式埋点

def test_登录成功自动写审计日志(client, login):
    login("grid")
    data = _logs(client, login("admin"), action="login", size=50)
    assert data["total"] >= 1
    item = data["items"][0]
    assert item["module"] == "auth"
    assert item["result"] == "success"
    assert item["traceId"].startswith("tr-")
    assert item["hash"].startswith("sm3:")


def test_登录失败记录尝试者(client, login):
    client.post("/api/v1/auth/login", json={"username": "vpp", "password": "错误密码"})
    data = _logs(client, login("admin"), action="login", result="failed", size=20)
    assert data["total"] >= 1
    assert data["items"][0]["riskLevel"] == "high"   # 失败的认证自动升级为高危


def test_越权被拒记denied并上链(client, login):
    """@audited 捕获 1003 后应记成 denied，并因高危自动上链。"""
    client.post("/api/v1/assets", headers=login("regulator"), json={
        "name": "越权登记", "dataType": "pv",
        "sourceDid": "did:vpp:device:0x" + "a" * 32,
        "payload": {"x": 1},
    })
    data = _logs(client, login("admin"), result="denied", size=20)
    assert data["total"] >= 1
    item = next(i for i in data["items"] if i["action"] == "asset:register")
    assert item["riskLevel"] == "high"
    assert item["evidenceId"].startswith("ev-")      # 高危日志自动上链
    assert "asset:write" in item["detail"]


def test_业务代码里没有手写审计调用():
    """架构约束：审计必须是声明式的，不允许散落在业务代码里。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "modules"
    offenders = []
    for path in root.rglob("*.py"):
        if path.parts[-2] == "audit":       # 审计模块自己除外
            continue
        text = path.read_text(encoding="utf-8")
        if "write_audit_log(" in text:
            offenders.append(str(path))
    assert not offenders, f"这些文件里出现了手写审计调用：{offenders}"


# ---------------------------------------------------------------- 按月分表

def test_当月分表自动创建(db):
    from modules.audit import sharding
    from core.response import now_cst

    table = sharding.table_of(now_cst())
    assert table.startswith("audit_log_") and len(table) == 16
    sharding.ensure_table(db, table)
    assert table in sharding.existing_tables(db)


def test_非法表名被拒(db):
    from core.exceptions import ParamError
    from modules.audit import sharding

    for bad in ["audit_log_2026", "audit_log_202608; DROP TABLE sys_user", "sys_user"]:
        with pytest.raises(ParamError):
            sharding.assert_valid(bad)


def test_跨月查询合并结果(db, client, login):
    """往上个月的分表塞一条，确认 UNION ALL 能把它查出来。"""
    from sqlalchemy import text

    from core.response import now_cst
    from modules.audit import sharding

    last_month = (now_cst().replace(day=1) - timedelta(days=1)).replace(tzinfo=None)
    table = sharding.ensure_table(db, sharding.table_of(last_month))
    db.execute(text(
        f"INSERT INTO {table} (trace_id, actor_name, module, action, result, risk_level, "
        f"detail, created_at) VALUES ('tr-lastmonth', '上月的人', 'auth', 'login', "
        f"'success', 'low', '上个月的日志', :ts)"
    ), {"ts": last_month})
    db.commit()

    data = _logs(client, login("admin"), traceId="tr-lastmonth", size=20)
    assert data["total"] == 1
    assert data["items"][0]["detail"] == "上个月的日志"


# ---------------------------------------------------------------- 五类风险规则

def test_规则清单符合契约(client, login):
    data = client.get("/api/v1/audit/rules", headers=login("admin")).json()["data"]
    codes = {r["ruleCode"] for r in data["items"]}
    assert codes == {"R01_UNAUTHORIZED", "R02_ABNORMAL_DID", "R03_PERM_CHURN",
                     "R04_BULK_EXPORT", "R05_SUSPICIOUS_GRAD"}
    r01 = next(r for r in data["items"] if r["ruleCode"] == "R01_UNAUTHORIZED")
    assert r01["threshold"] == 3 and r01["window"] == 300 and r01["level"] == "high"


def test_R01越权累计三次才告警(client, login, fake_redis):
    """前两次只计数，第三次才产生告警——避免误触一次就报警。"""
    vpp = login("vpp")
    before = client.get("/api/v1/audit/alerts?ruleCode=R01_UNAUTHORIZED&size=50",
                        headers=login("admin")).json()["data"]["total"]

    # 前两次拒绝：一次栽在角色依赖上，一次栽在权限装饰器上，两条路径都要能被计数
    assert client.get("/api/v1/users", headers=vpp).status_code == 403
    assert client.post("/api/v1/evidence", headers=vpp, json={
        "category": "data", "refId": "1", "payload": {}}).status_code == 403
    mid = client.get("/api/v1/audit/alerts?ruleCode=R01_UNAUTHORIZED&size=50",
                     headers=login("admin")).json()["data"]["total"]
    assert mid == before, "两次拒绝就告警的话阈值形同虚设"

    # 第三次拒绝，跨过阈值
    assert client.get("/api/v1/users", headers=vpp).status_code == 403

    after = client.get("/api/v1/audit/alerts?ruleCode=R01_UNAUTHORIZED&size=50",
                       headers=login("admin")).json()["data"]
    assert after["total"] > before
    alert = after["items"][0]
    assert alert["riskLevel"] == "high"
    assert alert["ruleName"] == "越权访问"
    assert alert["evidenceId"].startswith("ev-")     # 告警本身也上链


def test_R02冻结身份接入立刻告警(client, login, fake_redis):
    from modules.audit.rules import fire

    result = fire("R02_ABNORMAL_DID", actor_did="did:vpp:device:0xfrozen",
                  message="已冻结身份尝试接入", immediate=True)
    assert result is not None
    assert result["riskLevel"] == "critical"
    assert result["hitCount"] == 1


def test_R03权限变更五次触发(client, login, fake_redis):
    from modules.audit.rules import fire_perm_change

    did = "did:vpp:user:0xchurn"
    results = [fire_perm_change(did, "grant") for _ in range(5)]
    assert all(r is None for r in results[:4])
    assert results[4] is not None
    assert results[4]["ruleCode"] == "R03_PERM_CHURN"


def test_R04单次导出超千条立刻告警(client, login, fake_redis):
    from core.middleware import Principal
    from modules.audit.rules import fire_bulk_export

    p = Principal(user_id=1, username="admin", roles=["sys_admin"], did="did:vpp:user:0x01")
    assert fire_bulk_export(p, 5) is None            # 少量导出不告警
    result = fire_bulk_export(p, 1500)
    assert result is not None and "1500" in result["message"]


def test_R05梯度异常立刻告警(fake_redis):
    from modules.audit.rules import fire_suspicious_gradient

    result = fire_suspicious_gradient("fl-000012", {
        "type": "gradient_poisoning", "nodeId": "Node-C", "detail": "梯度范数超出阈值 8 倍"})
    assert result["ruleCode"] == "R05_SUSPICIOUS_GRAD"
    assert result["riskLevel"] == "high"
    assert "Node-C" in result["message"]


def test_同一窗口内不重复告警(client, login, fake_redis):
    from modules.audit.rules import fire

    first = fire("R02_ABNORMAL_DID", actor_did="did:vpp:device:0xdup",
                 message="第一次", immediate=True)
    second = fire("R02_ABNORMAL_DID", actor_did="did:vpp:device:0xdup",
                  message="第二次", immediate=True)
    assert first is not None and second is None


def test_R03借请求会话落告警且不自行提交(db, fake_redis):
    """权限变更留痕发生在同一事务刚写完存证之后，告警必须借请求会话上链。

    真库上另开连接会等自己的链尾行锁（3 秒 × 3 次重试），最后告警行随回滚丢失；
    SQLite 没有行锁复现不了等待，这里锁定的是「走的是请求会话、且不替调用方 commit」。
    """
    from modules.audit.model import AuditAlert
    from modules.audit.rules import fire_perm_change
    from sqlalchemy import func, select

    did = "did:vpp:user:0xchurn-shared-session"
    results = [fire_perm_change(did, "revoke", db=db) for _ in range(5)]
    assert results[4] is not None and results[4]["evidenceId"].startswith("ev-")
    # 告警行在本会话里可见（flush 过），但事务仍未提交——由业务函数统一 commit
    assert db.in_transaction()
    count = db.execute(select(func.count()).select_from(AuditAlert)
                       .where(AuditAlert.actor_did == did)).scalar_one()
    assert count == 1
    db.rollback()


def test_告警落库失败不会把规则静默整个窗口(client, login, fake_redis, monkeypatch):
    """原实现先种 Redis 标记再落库：落库失败标记却留下，该主体 10 分钟内再也不告警。"""
    from modules.audit import rules

    did = "did:vpp:device:0xflaky-chain"
    calls = {"n": 0}
    real = rules._insert_alert

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("模拟上链锁超时导致的落库失败")
        return real(*args, **kwargs)

    monkeypatch.setattr(rules, "_insert_alert", flaky)
    assert rules.fire("R02_ABNORMAL_DID", actor_did=did, immediate=True, message="第一次") is None
    second = rules.fire("R02_ABNORMAL_DID", actor_did=did, immediate=True, message="第二次")
    assert second is not None and second["ruleCode"] == "R02_ABNORMAL_DID"
    assert calls["n"] == 2


def test_五次申请驳回经接口触发R03并上链(client, login, fake_redis):
    """端到端：subject 连续 5 次申请、admin 逐次驳回 → 告警列表出现 R03 且 evidenceId 非空。"""
    admin, subject = login("admin"), login("subject")
    did = client.post("/api/v1/did/register", headers=admin, json={
        "subjectType": "device", "subjectName": "R03 触发测试设备", "custody": True,
    }).json()["data"]["did"]
    asset_id = client.post("/api/v1/assets", headers=admin, json={
        "name": "R03 触发测试资产", "dataType": "pv", "sourceDid": did, "level": "L3",
        "payload": {"pvOutput": 1.0}, "recordCount": 10,
    }).json()["data"]["id"]
    for i in range(5):
        app = client.post("/api/v1/permissions/apply", headers=subject, json={
            "resourceType": "asset", "resourceId": str(asset_id), "action": "read",
            "reason": f"churn-{i}"}).json()["data"]
        r = client.post(f"/api/v1/permissions/applications/{app['id']}/reject",
                        headers=admin, json={"reason": "churn"})
        assert r.status_code == 200, r.text
    alerts = client.get("/api/v1/audit/alerts?ruleCode=R03_PERM_CHURN&size=50",
                        headers=admin).json()["data"]["items"]
    mine = [a for a in alerts if "churn" in a["message"] or a["hitCount"] >= 5]
    assert mine, alerts
    assert mine[0]["evidenceId"] and mine[0]["evidenceId"].startswith("ev-")


def test_确认告警(client, login, fake_redis):
    from modules.audit.rules import fire

    fire("R02_ABNORMAL_DID", actor_did="did:vpp:device:0xack", immediate=True)
    admin = login("admin")
    alerts = client.get("/api/v1/audit/alerts?status=open&size=50",
                        headers=admin).json()["data"]["items"]
    target = alerts[0]

    acked = client.post(f"/api/v1/audit/alerts/{target['id']}/ack",
                        headers=admin).json()["data"]
    assert acked["status"] == "acked"
    assert acked["ackedBy"] == "系统管理员"

    again = client.post(f"/api/v1/audit/alerts/{target['id']}/ack", headers=admin)
    assert again.status_code == 409


# ---------------------------------------------------------------- 追踪与看板

def test_按traceid追出完整链路(client, login):
    """答辩演示第 9 步。"""
    trace = "tr-20260818-cafebabe"
    admin = login("admin")
    headers = {**admin, "X-Trace-Id": trace}

    did = client.post("/api/v1/did/register", headers=headers, json={
        "subjectType": "device", "subjectName": "链路追踪-审计", "custody": True,
    }).json()["data"]["did"]
    client.post("/api/v1/assets", headers=headers, json={
        "name": "链路追踪资产", "dataType": "pv", "sourceDid": did,
        "level": "L2", "payload": {"v": 1},
    })

    data = client.get(f"/api/v1/audit/trace/{trace}", headers=admin).json()["data"]
    assert data["traceId"] == trace
    assert data["summary"]["steps"] >= 2
    assert data["summary"]["result"] == "success"
    actions = [s["action"] for s in data["steps"]]
    assert "did:register" in actions and "asset:register" in actions
    assert [s["seq"] for s in data["steps"]] == list(range(1, len(data["steps"]) + 1))


def test_不存在的traceid返回1005(client, login):
    r = client.get("/api/v1/audit/trace/tr-00000000-00000000", headers=login("admin"))
    assert r.status_code == 404 and r.json()["code"] == 1005


def test_看板统计结构(client, login):
    login("grid")
    data = client.get("/api/v1/audit/stats", headers=login("admin")).json()["data"]
    assert set(data) == {"todayLogs", "highRiskLogs", "openAlerts", "onChainLogs",
                         "byModule", "byRisk", "trend"}
    assert data["todayLogs"] >= 1
    assert len(data["trend"]) == 7
    assert all({"date", "total", "high"} == set(d) for d in data["trend"])


def test_审计报告在算法服务不可用时降级(client, login, algo_down):
    """离线环境下 narrative 不能是空的，必须给规则化中文结论。

    必须显式用 algo_down 把算法服务打成不可达：原来靠"本机 8100 没起"来隐式成立，
    联调机上算法服务一开着这条用例就红，掩盖真实回归。
    """
    data = client.get("/api/v1/audit/report?period=day", headers=login("admin")).json()["data"]
    assert data["period"] == "day"
    assert data["narrativeSource"] == "rule"        # 算法服务没起，退回规则模板
    assert "审计日志" in data["narrative"]
    assert set(data["identityOps"]) == {"register", "freeze", "revoke", "rotate"}
    assert set(data["permissionOps"]) == {"applied", "approved", "rejected", "revoked"}


def test_报告周期参数校验(client, login):
    r = client.get("/api/v1/audit/report?period=year", headers=login("admin"))
    assert r.status_code == 400 and r.json()["code"] == 1001


# ---------------------------------------------------------------- 导出与权限

def test_导出csv返回文件流(client, login, fake_redis):
    r = client.get("/api/v1/audit/logs/export?riskLevel=high", headers=login("admin"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.startswith("﻿")              # 带 BOM，Excel 不乱码
    assert "追踪ID" in r.text
    assert int(r.headers["X-Total-Rows"]) >= 0


def test_导出触发R04告警(client, login, fake_redis):
    admin = login("admin")
    before = client.get("/api/v1/audit/alerts?ruleCode=R04_BULK_EXPORT&size=50",
                        headers=admin).json()["data"]["total"]
    for _ in range(3):
        client.get("/api/v1/audit/logs/export", headers=admin)
    after = client.get("/api/v1/audit/alerts?ruleCode=R04_BULK_EXPORT&size=50",
                       headers=admin).json()["data"]["total"]
    assert after > before


def test_监管方可查审计业务角色不可(client, login):
    assert client.get("/api/v1/audit/stats", headers=login("regulator")).status_code == 200
    r = client.get("/api/v1/audit/stats", headers=login("vpp"))
    assert r.status_code == 403 and r.json()["code"] == 1003
