"""权限中心测试：角色矩阵、四段判定顺序、越权拦截。"""
import pytest

from core.middleware import Principal
from modules.permission.service import check_permission, check_permission_detail


def _p(*roles, did="did:vpp:user:0x00000000000000000000000000000001"):
    return Principal(user_id=1, username="t", roles=list(roles), did=did)


# ---------------------------------------------------------------- 服务层

def test_角色矩阵放行(db):
    allowed, _ = check_permission(_p("grid_dispatcher"), "dispatch", "issue")
    assert allowed is True


def test_角色矩阵拒绝并给出可读原因(db):
    allowed, reason = check_permission(_p("vpp_operator"), "dispatch", "issue")
    assert allowed is False
    assert "vpp_operator" in reason and "dispatch:issue" in reason


def test_无角色一律拒绝(db):
    allowed, reason = check_permission(_p(), "asset", "read")
    assert allowed is False and "无角色" in reason


def test_多角色取并集(db):
    allowed, _ = check_permission(_p("vpp_operator", "grid_dispatcher"), "dispatch", "issue")
    assert allowed is True


def test_own范围在无具体资源时放行到service层过滤(db):
    allowed, _, matched = check_permission_detail(_p("energy_subject"), "asset", "read")
    assert allowed is True
    assert matched.endswith(":own")


def test_own范围访问他人资源被拒(db, SessionFactory):
    """能源主体只能读自己的资产，读别人的必须被挡住。"""
    from modules.asset.model import EnergyAsset

    with SessionFactory() as s:
        s.query(EnergyAsset).filter(EnergyAsset.id.in_([9001, 9002])).delete(
            synchronize_session=False)
        s.add(EnergyAsset(id=9001, name="自有资产", data_type="pv", level="L2",
                          source_did="did:vpp:user:0x00000000000000000000000000000001",
                          owner_did="did:vpp:user:0x00000000000000000000000000000001",
                          payload={"v": 1}, payload_hash="sm3:" + "0" * 64))
        s.add(EnergyAsset(id=9002, name="他人资产", data_type="pv", level="L4",
                          source_did="did:vpp:user:0x00000000000000000000000000000009",
                          owner_did="did:vpp:user:0x00000000000000000000000000000009",
                          payload={"v": 2}, payload_hash="sm3:" + "1" * 64))
        s.commit()

    assert check_permission(_p("energy_subject"), "asset", "read", "9001")[0] is True
    allowed, reason = check_permission(_p("energy_subject"), "asset", "read", "9002")
    assert allowed is False and "自有" in reason


def test_显式授权可越过角色矩阵(db, SessionFactory):
    """角色矩阵里没有的权限，可以通过权限申请审批后的授权记录获得。"""
    from datetime import datetime, timedelta

    from modules.permission.model import PermGrant

    subject_did = "did:vpp:user:0x00000000000000000000000000000004"
    assert check_permission(_p("energy_subject", did=subject_did), "model", "read", "v11")[0] is False

    with SessionFactory() as s:
        s.add(PermGrant(grantee_did=subject_did, resource_type="model", resource_id="v11",
                        action="read", status="active",
                        expire_at=datetime.now() + timedelta(days=1)))
        s.commit()

    allowed, _, matched = check_permission_detail(
        _p("energy_subject", did=subject_did), "model", "read", "v11")
    assert allowed is True and matched.startswith("grant:")


def test_过期授权不生效(db, SessionFactory):
    from datetime import datetime, timedelta

    from modules.permission.model import PermGrant

    did = "did:vpp:user:0x00000000000000000000000000000005"
    with SessionFactory() as s:
        s.add(PermGrant(grantee_did=did, resource_type="model", resource_id="v10",
                        action="write", status="active",
                        expire_at=datetime.now() - timedelta(days=1)))
        s.commit()

    allowed, reason = check_permission(_p("energy_subject", did=did), "model", "write", "v10")
    assert allowed is False and "过期" in reason


# ---------------------------------------------------------------- 接口层

def test_角色列表含用户数与权限明细(client, login):
    data = client.get("/api/v1/roles", headers=login("admin")).json()["data"]
    codes = {r["code"] for r in data}
    assert codes == {"sys_admin", "grid_dispatcher", "vpp_operator",
                     "energy_subject", "regulator", "edge_node"}
    admin_role = next(r for r in data if r["code"] == "sys_admin")
    assert admin_role["userCount"] == 1
    assert any(g["resourceType"] == "dispatch" and g["action"] == "issue"
               for g in admin_role["grants"])


def test_权限矩阵结构符合契约(client, login):
    data = client.get("/api/v1/permissions/matrix", headers=login("admin")).json()["data"]
    assert data["resources"] == ["asset", "model", "dispatch", "evidence", "algo"]
    assert data["actions"] == ["read", "write", "execute", "issue", "export"]
    admin = next(r for r in data["roles"] if r["code"] == "sys_admin")
    assert "issue" in admin["grants"]["dispatch"]


def test_权限预判接口(client, login):
    r = client.post("/api/v1/permissions/check", headers=login("vpp"),
                    json={"resourceType": "dispatch", "action": "issue"})
    data = r.json()["data"]
    assert data["allowed"] is False
    assert "vpp_operator" in data["reason"]

    r = client.post("/api/v1/permissions/check", headers=login("grid"),
                    json={"resourceType": "dispatch", "action": "issue"})
    assert r.json()["data"]["allowed"] is True
    assert r.json()["data"]["matchedRule"].startswith("role:grid_dispatcher")


def test_普通角色不能校验他人权限(client, login):
    r = client.post("/api/v1/permissions/check", headers=login("vpp"),
                    json={"did": "did:vpp:user:0x00000000000000000000000000000001",
                          "resourceType": "asset", "action": "read"})
    assert r.status_code == 403 and r.json()["code"] == 1003


def test_监管方可校验他人权限(client, login):
    r = client.post("/api/v1/permissions/check", headers=login("regulator"),
                    json={"did": "did:vpp:user:0x00000000000000000000000000000003",
                          "resourceType": "dispatch", "action": "issue"})
    assert r.status_code == 200
    assert r.json()["data"]["allowed"] is False


def test_仅管理员可改角色权限(client, login):
    r = client.put("/api/v1/roles/vpp_operator", headers=login("grid"),
                   json={"name": "改名尝试"})
    assert r.status_code == 403 and r.json()["code"] == 1003


def test_系统管理员权限不可被改空(client, login):
    r = client.put("/api/v1/roles/sys_admin", headers=login("admin"), json={"grants": []})
    assert r.status_code == 400
    assert "不可修改" in r.json()["message"]


def test_新建自定义角色并生效(client, login):
    headers = login("admin")
    r = client.post("/api/v1/roles", headers=headers, json={
        "code": "auditor_x", "name": "外部审计员",
        "description": "只读存证",
        "grants": [{"resourceType": "evidence", "action": "read", "scope": "all"}],
    })
    assert r.json()["code"] == 0
    roles = client.get("/api/v1/roles", headers=headers).json()["data"]
    assert any(r["code"] == "auditor_x" and r["isBuiltin"] is False for r in roles)


@pytest.mark.parametrize("role,resource,action,expected", [
    ("sys_admin", "dispatch", "issue", True),
    ("grid_dispatcher", "dispatch", "issue", True),
    ("vpp_operator", "dispatch", "issue", False),
    ("energy_subject", "dispatch", "issue", False),
    ("regulator", "dispatch", "issue", False),
    ("edge_node", "dispatch", "issue", False),
    ("regulator", "asset", "export", True),
    ("vpp_operator", "asset", "export", False),
    ("grid_dispatcher", "asset", "write", False),
    ("vpp_operator", "asset", "write", True),
])
def test_权限矩阵与契约逐格一致(db, role, resource, action, expected):
    """DB-SCHEMA.md 里那张矩阵表，逐格验一遍。"""
    assert check_permission(_p(role), resource, action)[0] is expected
