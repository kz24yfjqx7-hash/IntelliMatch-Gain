"""权限申请 → 审批 → 授权 → 回收 全流程测试。

对应答辩演示脚本第 4 步：subject 申请读权限 → admin 审批 → 权限生效。
"""
import pytest


@pytest.fixture
def asset_id(client, login):
    """造一条属于 admin 名下设备的资产，供 subject 申请。"""
    did = client.post("/api/v1/did/register", headers=login("admin"), json={
        "subjectType": "device", "subjectName": "权限流转测试设备", "custody": True,
    }).json()["data"]["did"]
    r = client.post("/api/v1/assets", headers=login("admin"), json={
        "name": "权限流转测试资产", "dataType": "pv", "sourceDid": did, "level": "L3",
        "payload": {"pvOutput": 12.3}, "recordCount": 100,
    })
    return r.json()["data"]["id"]


def _apply(client, headers, asset_id, action="read"):
    return client.post("/api/v1/permissions/apply", headers=headers, json={
        "resourceType": "asset", "resourceId": str(asset_id), "action": action,
        "reason": "联合建模需要读取该资产",
    })


def test_申请前无权访问他人资产(client, login, asset_id):
    r = client.get(f"/api/v1/assets/{asset_id}", headers=login("subject"))
    assert r.status_code == 403 and r.json()["code"] == 1003


def test_完整流转链路(client, login, asset_id):
    subject, admin = login("subject"), login("admin")

    # 1. 申请
    app = _apply(client, subject, asset_id).json()["data"]
    assert app["status"] == "pending"
    assert app["evidenceId"].startswith("ev-")

    # 2. 审批
    approved = client.post(f"/api/v1/permissions/applications/{app['id']}/approve",
                           headers=admin, json={"reason": "符合最小必要原则"}).json()["data"]
    assert approved["status"] == "approved"
    assert approved["grantId"] > 0

    # 3. 授权立刻生效：原本 403 的接口现在能访问了
    r = client.get(f"/api/v1/assets/{asset_id}", headers=subject)
    assert r.status_code == 200, r.text

    # 4. 资产授权状态同步更新，溯源链多了一段 authorize
    detail = client.get(f"/api/v1/assets/{asset_id}", headers=admin).json()["data"]
    assert detail["authStatus"] == "authorized"
    chain = client.get(f"/api/v1/assets/{asset_id}/lineage",
                       headers=admin).json()["data"]["chain"]
    assert "authorize" in [c["stage"] for c in chain]

    # 5. 回收后立刻失效
    revoked = client.post(f"/api/v1/permissions/grants/{approved['grantId']}/revoke",
                          headers=admin, json={"reason": "业务已结束"}).json()["data"]
    assert revoked["status"] == "revoked"
    assert client.get(f"/api/v1/assets/{asset_id}", headers=subject).status_code == 403


def test_驳回后依然无权访问(client, login, asset_id):
    subject, admin = login("subject"), login("admin")
    app = _apply(client, subject, asset_id).json()["data"]
    rejected = client.post(f"/api/v1/permissions/applications/{app['id']}/reject",
                           headers=admin, json={"reason": "范围超出最小必要"}).json()["data"]
    assert rejected["status"] == "rejected"
    assert client.get(f"/api/v1/assets/{asset_id}", headers=subject).status_code == 403


def test_不能重复提交相同申请(client, login, asset_id):
    subject = login("subject")
    assert _apply(client, subject, asset_id).status_code == 200
    dup = _apply(client, subject, asset_id)
    assert dup.status_code == 409 and dup.json()["code"] == 1006


def test_不能重复审批(client, login, asset_id):
    subject, admin = login("subject"), login("admin")
    app = _apply(client, subject, asset_id).json()["data"]
    client.post(f"/api/v1/permissions/applications/{app['id']}/approve",
                headers=admin, json={})
    again = client.post(f"/api/v1/permissions/applications/{app['id']}/reject",
                        headers=admin, json={})
    assert again.status_code == 409 and again.json()["code"] == 1006


def test_非管理员不能审批(client, login, asset_id):
    subject = login("subject")
    app = _apply(client, subject, asset_id).json()["data"]
    r = client.post(f"/api/v1/permissions/applications/{app['id']}/approve",
                    headers=login("vpp"), json={})
    assert r.status_code == 403 and r.json()["code"] == 1003


def test_普通用户只能看到自己的申请(client, login, asset_id):
    subject, vpp, admin = login("subject"), login("vpp"), login("admin")
    _apply(client, subject, asset_id)
    _apply(client, vpp, asset_id)

    mine = client.get("/api/v1/permissions/applications?page=1&size=50",
                      headers=subject).json()["data"]
    subject_did = client.get("/api/v1/auth/me", headers=subject).json()["data"]["did"]
    assert all(i["applicantDid"] == subject_did for i in mine["items"])

    all_apps = client.get("/api/v1/permissions/applications?page=1&size=50",
                          headers=admin).json()["data"]
    assert all_apps["total"] >= mine["total"] + 1


def test_授权列表可按状态筛选(client, login, asset_id):
    subject, admin = login("subject"), login("admin")
    app = _apply(client, subject, asset_id).json()["data"]
    client.post(f"/api/v1/permissions/applications/{app['id']}/approve",
                headers=admin, json={})

    active = client.get("/api/v1/permissions/grants?status=active&page=1&size=50",
                        headers=admin).json()["data"]
    assert active["total"] >= 1
    assert all(g["status"] == "active" for g in active["items"])


def test_每步流转都留了变更痕迹(client, login, asset_id, SessionFactory):
    from modules.permission.model import PermChangeLog

    subject, admin = login("subject"), login("admin")
    app = _apply(client, subject, asset_id).json()["data"]
    client.post(f"/api/v1/permissions/applications/{app['id']}/approve",
                headers=admin, json={})

    subject_did = client.get("/api/v1/auth/me", headers=subject).json()["data"]["did"]
    with SessionFactory() as db:
        logs = db.query(PermChangeLog).filter(
            PermChangeLog.target_did == subject_did).all()
    types = {log.change_type for log in logs}
    assert {"apply", "approve"} <= types
    assert all(log.evidence_id for log in logs)   # 每条留痕都有对应存证


def test_审批人与授权人信息在列表里可见(client, login, asset_id):
    """回归：申请列表要能看到审批人（approverName/approveReason/approvedAt），
    授权列表要能看到授权人（grantedBy/grantedByName）——两者原来因字段名不匹配/未返回而空白。"""
    subject, admin = login("subject"), login("admin")
    app = _apply(client, subject, asset_id).json()["data"]
    approved = client.post(f"/api/v1/permissions/applications/{app['id']}/approve",
                           headers=admin, json={"reason": "同意授权"}).json()["data"]

    # 申请列表：审批后带审批人信息
    apps = client.get("/api/v1/permissions/applications?size=50", headers=admin).json()["data"]["items"]
    mine = next(a for a in apps if a["id"] == app["id"])
    assert mine["status"] == "approved"
    assert mine["approverName"] and mine["approverDid"], mine
    assert mine["approveReason"] == "同意授权"
    assert mine["approvedAt"]

    # 授权列表：授权人 = 审批的管理员
    grants = client.get("/api/v1/permissions/grants?size=50", headers=admin).json()["data"]["items"]
    g = next(x for x in grants if x.get("applicationId") == app["id"])
    assert g["grantedBy"] == mine["approverDid"], g
    assert g["grantedByName"] == mine["approverName"]

    # 回收后返回体也带授权人
    revoked = client.post(f"/api/v1/permissions/grants/{approved['grantId']}/revoke",
                          headers=admin, json={"reason": "结束"}).json()["data"]
    assert revoked["grantedBy"] == mine["approverDid"]
