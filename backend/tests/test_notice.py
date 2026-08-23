"""站内消息（铃铛）：申请通知审批人、审批结果通知申请人。

覆盖三条主线 + 两条越权边界：
  * subject 提申请 → admin 铃铛出现待办；subject 自己不该收到
  * admin 通过/驳回 → subject 铃铛出现结果
  * 回收授权 → 被回收人收到
  * 只能读/改自己的消息
"""
import pytest


@pytest.fixture
def asset_id(client, login):
    did = client.post("/api/v1/did/register", headers=login("admin"), json={
        "subjectType": "device", "subjectName": "铃铛测试设备", "custody": True,
    }).json()["data"]["did"]
    r = client.post("/api/v1/assets", headers=login("admin"), json={
        "name": "铃铛测试资产", "dataType": "pv", "sourceDid": did, "level": "L3",
        "payload": {"pvOutput": 1.0}, "recordCount": 10,
    })
    return r.json()["data"]["id"]


def _apply(client, headers, asset_id, action="read", reason="联合建模需要读取"):
    return client.post("/api/v1/permissions/apply", headers=headers, json={
        "resourceType": "asset", "resourceId": str(asset_id), "action": action,
        "reason": reason,
    })


def _notices(client, headers, **params):
    r = client.get("/api/v1/notices", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]["items"]


def _unread(client, headers):
    r = client.get("/api/v1/notices/unread-count", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]["count"]


def test_提交申请后审批人收到待办消息(client, login, asset_id):
    subject, admin = login("subject"), login("admin")
    before = _unread(client, admin)

    app = _apply(client, subject, asset_id).json()["data"]

    assert _unread(client, admin) == before + 1
    top = _notices(client, admin, category="permission_apply")[0]
    assert top["status"] == "unread"
    assert top["refId"] == str(app["id"])
    assert "待审批" in top["title"]
    # 消息要能把人带到申请那一条，否则铃铛只是个数字
    assert top["link"] == f"/permission?tab=apps&id={app['id']}"
    # 申请人自己不该收到「有新申请待审批」
    assert _notices(client, subject, category="permission_apply") == []


def test_审批通过后申请人收到结果消息(client, login, asset_id):
    subject, admin = login("subject"), login("admin")
    app_id = _apply(client, subject, asset_id).json()["data"]["id"]
    before = _unread(client, subject)

    r = client.post(f"/api/v1/permissions/applications/{app_id}/approve",
                    headers=admin, json={"reason": "符合最小必要"})
    assert r.status_code == 200, r.text

    assert _unread(client, subject) == before + 1
    top = _notices(client, subject, category="permission_result")[0]
    assert top["level"] == "success"
    assert "通过" in top["title"]
    assert "符合最小必要" in (top["content"] or "")
    assert top["link"] == "/permission?tab=grants"


def test_驳回后申请人收到驳回消息(client, login, asset_id):
    subject, admin = login("subject"), login("admin")
    app_id = _apply(client, subject, asset_id, action="export").json()["data"]["id"]

    r = client.post(f"/api/v1/permissions/applications/{app_id}/reject",
                    headers=admin, json={"reason": "超出最小必要范围"})
    assert r.status_code == 200, r.text

    top = _notices(client, subject, category="permission_result")[0]
    assert top["level"] == "warning"
    assert "驳回" in top["title"]
    assert "超出最小必要范围" in (top["content"] or "")


def test_回收授权后被回收人收到消息(client, login, asset_id):
    subject, admin = login("subject"), login("admin")
    app_id = _apply(client, subject, asset_id).json()["data"]["id"]
    grant_id = client.post(f"/api/v1/permissions/applications/{app_id}/approve",
                           headers=admin, json={}).json()["data"]["grantId"]

    r = client.post(f"/api/v1/permissions/grants/{grant_id}/revoke",
                    headers=admin, json={"reason": "项目结束"})
    assert r.status_code == 200, r.text

    top = _notices(client, subject, category="permission_revoke")[0]
    assert "回收" in top["title"]
    assert "项目结束" in (top["content"] or "")


def test_标记已读只影响自己(client, login, asset_id):
    subject, admin = login("subject"), login("admin")
    _apply(client, subject, asset_id)

    mine = _notices(client, admin, status="unread")
    assert mine, "管理员应有未读消息"
    admin_unread_before = _unread(client, admin)

    r = client.post("/api/v1/notices/read", headers=admin, json={"ids": [mine[0]["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["updated"] == 1
    assert _unread(client, admin) == admin_unread_before - 1

    # 别人的消息 id 传进来必须无效（where 里带 recipient_did）
    r2 = client.post("/api/v1/notices/read", headers=login("grid"),
                     json={"ids": [mine[0]["id"]]})
    assert r2.json()["data"]["updated"] == 0


def test_全部已读(client, login, asset_id):
    subject, admin = login("subject"), login("admin")
    _apply(client, subject, asset_id)
    assert _unread(client, admin) > 0

    r = client.post("/api/v1/notices/read-all", headers=admin)
    assert r.status_code == 200, r.text
    assert _unread(client, admin) == 0


def test_消息列表只返回自己的(client, login, asset_id):
    subject, admin = login("subject"), login("admin")
    _apply(client, subject, asset_id)

    for item in _notices(client, admin):
        assert item["actorDid"] != item.get("recipientDid")  # 不回显收件人，接口本就按 did 过滤
    # grid 与本流程无关，铃铛应当是空的
    assert _notices(client, login("grid")) == []


def test_通知失败不影响业务(client, login, asset_id, monkeypatch):
    """铃铛写库炸了，权限申请仍然要成功——通知是附属品，不能反向拖垮业务。"""
    from modules.notice import service as notice_service

    def _boom(*a, **kw):
        raise RuntimeError("模拟消息表不可用")

    monkeypatch.setattr(notice_service, "_dids_of_roles", _boom)
    r = _apply(client, login("subject"), asset_id, reason="通知故障容错验证")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "pending"



def test_消息表缺失时服务层降级不抛(monkeypatch):
    """存量库缺 sys_notice 表（未跑迁移，MySQL 1146）时，服务层必须降级返回空/0，
    由此保证 GET /notices、/unread-count 不把首页/页头打成 500。"""
    from sqlalchemy.exc import ProgrammingError

    from modules.notice import service as notice_service

    class _FakeDB:
        def execute(self, *a, **kw):
            raise ProgrammingError("SELECT", {}, Exception(1146, "Table 'x.sys_notice' doesn't exist"))
        def rollback(self):
            pass

    db = _FakeDB()
    items, total = notice_service.list_notices(db, "did:vpp:user:0xabc", 1, 20)
    assert items == [] and total == 0
    assert notice_service.unread_count(db, "did:vpp:user:0xabc") == 0

    # 非 1146 的数据库错误仍要抛出，不能把真问题也吞掉
    class _OtherErr:
        def execute(self, *a, **kw):
            raise ProgrammingError("SELECT", {}, Exception(1064, "syntax error"))
        def rollback(self):
            pass
    import pytest
    with pytest.raises(ProgrammingError):
        notice_service.list_notices(_OtherErr(), "did:vpp:user:0xabc", 1, 20)
