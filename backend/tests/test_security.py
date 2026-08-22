"""安全测试专项——按攻击面组织，而不是按模块组织。

其他 test_*.py 是「功能对不对」，这个文件是「有人存心攻击时挡不挡得住」。
评委问「你们凭什么说这套东西是安全的」时，直接跑这一个文件给他看。

七个攻击面：
    A 认证绕过      伪造 / 篡改 / 过期 Token，不带凭证直接打接口
    B 垂直越权      低权限角色调高权限接口
    C 水平越权      同角色之间互相翻数据
    D 身份伪造      DID 与签名层面的冒名、重放、越权下发
    E 注入与恶意输入 SQL 注入、路径穿越、超长输入、参数越界
    F 信息泄露      口令哈希、私钥、堆栈、账号枚举
    G 审计绕过      能不能做了坏事不留痕、能不能事后抹掉痕迹
"""
import base64
import json
import re
import time

import pytest


# ================================================================ 工具

def _b64url_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def _b64url_encode(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _payload_of(headers: dict) -> dict:
    token = headers["Authorization"].removeprefix("Bearer ")
    return json.loads(_b64url_decode(token.split(".")[1]))


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _api_routes():
    """列出所有 /api/v1 接口，供全量扫描用。"""
    import main

    for route in main.app.routes:
        if not hasattr(route, "methods") or not route.path.startswith("/api/v1"):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, route.path


def _fill(path: str) -> str:
    """把 /assets/{asset_id} 这类路径参数填成一个占位值。"""
    return re.sub(r"\{[^}]+\}", "1", path)


@pytest.fixture
def admin_did(client, login, SessionFactory):
    """给 admin 绑一个真实身份和托管密钥，用来做签名相关的攻击测试。"""
    from modules.auth.model import SysUser

    identity = client.post("/api/v1/did/register", headers=login("admin"), json={
        "subjectType": "user", "subjectName": "系统管理员", "custody": True,
    }).json()["data"]

    with SessionFactory() as db:
        db.get(SysUser, 1).did = identity["did"]
        db.commit()
    return {"headers": login("admin"), **identity}


def _new_dispatch(client, headers, algo=True):
    task_id = client.post("/api/v1/dispatch/tasks", headers=headers, json={
        "name": "安全测试用调度任务",
        "nodeIds": ["Node-A", "Node-C"],
        "timeWindow": "2026-08-18T19:00~20:00+08:00",
    }).json()["data"]["id"]
    if algo:
        client.post(f"/api/v1/dispatch/tasks/{task_id}/run", headers=headers)
    return task_id


# ================================================================ A. 认证绕过

def test_全部接口不带token一律1002(client):
    """全量扫描：只要有一个接口漏了鉴权，这条就红。

    这是整个安全测试里最值钱的一条——它不依赖我记得给哪个接口加过鉴权，
    新加接口忘了挂依赖时会立刻被抓出来。
    """
    checked, leaked = [], []
    for method, path in _api_routes():
        if path == "/api/v1/auth/login":   # 登录本身必须公开
            continue
        resp = client.request(method, _fill(path), json={})
        checked.append(f"{method} {path}")
        if resp.status_code != 401 or resp.json().get("code") != 1002:
            leaked.append(f"{method} {path} -> {resp.status_code}/{resp.json().get('code')}")

    assert not leaked, "以下接口没有拦住未登录请求：\n" + "\n".join(leaked)
    assert len(checked) >= 70, f"只扫到 {len(checked)} 个接口，路由注册可能出问题了"


def test_公开接口清单是收敛的():
    """免鉴权路径必须是白名单，且只有这几条。新增一条就得改这个测试。"""
    from core.middleware import PUBLIC_PATHS

    assert set(PUBLIC_PATHS) == {
        "/health", "/api/v1/auth/login", "/docs", "/redoc", "/openapi.json", "/ws",
    }


@pytest.mark.parametrize("value", [
    "",                                  # 空头
    "Bearer",                            # 只有前缀
    "Bearer ",                           # 前缀 + 空 token
    "Bearer abc",                        # 不是 JWT
    "Bearer a.b.c",                      # 三段但不是合法 base64
    "Token eyJhbGciOiJIUzI1NiJ9.e30.x",  # 认证方案不对
    "Basic YWRtaW46YWRtaW4xMjM=",        # HTTP Basic 蒙混过关
])
def test_畸形Authorization头一律当未登录(client, value):
    r = client.get("/api/v1/auth/me", headers={"Authorization": value} if value else {})
    assert r.status_code == 401 and r.json()["code"] == 1002


def test_篡改token提权被签名挡住(client, login):
    """最典型的一种攻击：把 payload 里的 roles 改成 sys_admin，签名段原样保留。"""
    token = login("subject")["Authorization"].removeprefix("Bearer ")
    head, body, sig = token.split(".")

    payload = json.loads(_b64url_decode(body))
    assert payload["roles"] == ["energy_subject"], "前提变了：subject 不再是能源主体"
    payload["roles"] = ["sys_admin"]

    forged = f"{head}.{_b64url_encode(payload)}.{sig}"
    r = client.get("/api/v1/users", headers=_bearer(forged))
    assert r.status_code == 401 and r.json()["code"] == 1002
    assert "Token" in r.json()["message"] or "失效" in r.json()["message"]


def test_alg_none攻击无效(client):
    """去掉签名算法、留空签名段，是 JWT 最经典的一个坑。

    pyjwt 解码时显式传了 algorithms=["HS256"]，alg=none 会被直接拒绝。
    """
    payload = {"sub": "1", "username": "admin", "roles": ["sys_admin"],
               "did": None, "exp": int(time.time()) + 3600, "jti": "deadbeef"}
    forged = f"{_b64url_encode({'alg': 'none', 'typ': 'JWT'})}.{_b64url_encode(payload)}."

    r = client.get("/api/v1/users", headers=_bearer(forged))
    assert r.status_code == 401 and r.json()["code"] == 1002


def test_用别的密钥签的token无效(client):
    """攻击者自己搭一套签发逻辑，密钥猜不中就进不来。"""
    import jwt

    payload = {"sub": "1", "username": "admin", "roles": ["sys_admin"],
               "did": None, "exp": int(time.time()) + 3600, "jti": "cafebabe"}
    forged = jwt.encode(payload, "attacker-guessed-secret", algorithm="HS256")

    r = client.get("/api/v1/users", headers=_bearer(forged))
    assert r.status_code == 401 and r.json()["code"] == 1002


def test_过期token被拒(client, login):
    """把有效期改到一小时前，其余原样重签——签名是对的，但过期了。"""
    import jwt

    from core.config import settings

    payload = _payload_of(login("admin"))
    payload["exp"] = int(time.time()) - 3600
    expired = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    r = client.get("/api/v1/users", headers=_bearer(expired))
    assert r.status_code == 401 and r.json()["code"] == 1002
    assert "过期" in r.json()["message"]


def test_登出后token对所有接口都失效(client, login, fake_redis):
    """登出是把 jti 拉黑，不是让前端自己删——服务端说了才算。"""
    headers = login("admin")
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    client.post("/api/v1/auth/logout", headers=headers)

    for path in ("/api/v1/auth/me", "/api/v1/users", "/api/v1/audit/logs",
                 "/api/v1/assets", "/api/v1/did"):
        r = client.get(path, headers=headers)
        assert r.status_code == 401 and r.json()["code"] == 1002, path


def test_token里的用户被停用后立刻失效(client, login, SessionFactory):
    """签发之后账号被停用，旧 token 不能继续用。"""
    from modules.auth.model import SysUser

    headers = login("vpp")
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    with SessionFactory() as db:
        db.get(SysUser, 3).status = "disabled"
        db.commit()
    try:
        r = client.get("/api/v1/auth/me", headers=headers)
        assert r.status_code == 401 and r.json()["code"] == 1002
    finally:
        with SessionFactory() as db:
            db.get(SysUser, 3).status = "active"
            db.commit()


def test_连续猜密码会被临时锁定(client, fake_redis):
    """撞库防护：同一账号 5 分钟内错 5 次就锁到窗口结束，错误码 1007。"""
    for i in range(5):
        r = client.post("/api/v1/auth/login",
                        json={"username": "admin", "password": f"wrong-{i}"})
        assert r.json()["code"] == 1002, f"第 {i + 1} 次应该只是密码错"

    r = client.post("/api/v1/auth/login",
                    json={"username": "admin", "password": "wrong-6"})
    assert r.status_code == 429 and r.json()["code"] == 1007
    assert "锁定" in r.json()["message"]

    # 锁的是账号不是整个系统，别的账号照常能登
    assert client.post("/api/v1/auth/login",
                       json={"username": "vpp", "password": "vpp123"}).status_code == 200

    # 锁住期间就算密码对了也进不来
    assert client.post("/api/v1/auth/login",
                       json={"username": "admin", "password": "admin123"}
                       ).json()["code"] == 1007


def test_登录成功会清掉失败计数(client, fake_redis):
    """错了几次又想起来了，不该留着计数等下次凑够 5 次。"""
    for i in range(3):
        client.post("/api/v1/auth/login",
                    json={"username": "regulator", "password": f"wrong-{i}"})

    assert client.post("/api/v1/auth/login",
                       json={"username": "regulator", "password": "reg123"}
                       ).status_code == 200
    assert fake_redis["store"].get("auth:login:fail:regulator") is None

    # 计数清零了，再错三次也还没到阈值
    for i in range(3):
        assert client.post("/api/v1/auth/login",
                           json={"username": "regulator", "password": f"wrong-{i}"}
                           ).json()["code"] == 1002


def test_Redis挂掉时登录不被限流拖死(client):
    """限流是加固手段，不能让它变成新的单点——缓存不可用时自动放行。"""
    for i in range(8):
        r = client.post("/api/v1/auth/login",
                        json={"username": "admin", "password": f"wrong-{i}"})
        assert r.json()["code"] == 1002, "Redis 不可用时不应该出现 1007"

    assert client.post("/api/v1/auth/login",
                       json={"username": "admin", "password": "admin123"}).status_code == 200


# ================================================================ B. 垂直越权

_越权用例 = [
    ("vpp", "GET", "/api/v1/users", None, "运营商查用户列表"),
    ("subject", "POST", "/api/v1/users",
     {"username": "hacker", "password": "hacker123", "realName": "入侵者",
      "roles": ["sys_admin"]}, "能源主体给自己开管理员账号"),
    ("grid", "PUT", "/api/v1/roles/energy_subject",
     {"permissions": [{"resourceType": "asset", "action": "read", "scope": "all"}]},
     "调度员改别人的角色权限"),
    ("subject", "POST", "/api/v1/did/register",
     {"subjectType": "device", "subjectName": "伪造设备"}, "能源主体自己签发身份"),
    ("edge", "POST", "/api/v1/did/did:vpp:user:0x00000000000000000000000000000001/status",
     {"action": "revoke", "reason": "捣乱"}, "边缘节点注销别人的身份"),
    ("regulator", "POST", "/api/v1/assets",
     {"name": "监管方登记的资产", "dataType": "pv",
      "sourceDid": "did:vpp:device:0x00000000000000000000000000000001",
      "payload": {"pvOutput": 1.0}}, "只读的监管方写数据"),
    ("subject", "POST", "/api/v1/fl/tasks",
     {"name": "越权发起的训练", "nodeIds": ["Node-A"], "rounds": 3}, "能源主体发起联邦训练"),
    ("vpp", "GET", "/api/v1/audit/logs", None, "运营商翻审计日志"),
    ("edge", "GET", "/api/v1/audit/report?period=day", None, "边缘节点拉审计报告"),
    ("subject", "POST", "/api/v1/evidence/demo/tamper",
     {"evidenceId": "ev-1", "newValue": {"x": 1}}, "非管理员调篡改接口"),
    ("edge", "POST", "/api/v1/evidence",
     {"category": "data", "refId": "1", "payload": {"x": 1}}, "边缘节点直接写存证"),
]


@pytest.mark.parametrize("role,method,path,body,场景", _越权用例,
                         ids=[c[4] for c in _越权用例])
def test_垂直越权一律1003(client, login, role, method, path, body, 场景):
    """低权限角色调高权限接口。前端把按钮藏了不算防护，直接发包也得挡住。"""
    r = client.request(method, path, headers=login(role), json=body or {})
    assert r.status_code == 403, f"{场景}：期望 403，实得 {r.status_code} {r.text[:200]}"
    assert r.json()["code"] == 1003
    assert r.json()["message"], "拒绝理由不能为空，前端要显示给用户看"


def test_拒绝理由说清楚缺哪个权限(client, login):
    """审计日志和前端提示都要能看懂，不能只说一句「无权限」。"""
    r = client.post("/api/v1/fl/tasks", headers=login("vpp"),
                    json={"name": "x", "nodeIds": ["Node-A"], "rounds": 3})
    assert r.json()["code"] == 1003
    assert "algo:execute" in r.json()["message"]


def test_越权尝试必然留痕且判为高危(client, login):
    """做了坏事就一定有记录——denied + high + 上链，三样都不能少。"""
    admin = login("admin")
    before = client.get("/api/v1/audit/logs?result=denied&size=1",
                        headers=admin).json()["data"]["total"]

    client.post("/api/v1/evidence", headers=login("vpp"),
                json={"category": "data", "refId": "1", "payload": {"x": 1}})

    logs = client.get("/api/v1/audit/logs?result=denied&size=5",
                      headers=admin).json()["data"]
    assert logs["total"] > before
    latest = logs["items"][0]
    assert latest["result"] == "denied"
    assert latest["riskLevel"] in ("high", "critical")
    assert latest["evidenceId"], "高危审计必须上链，否则日志本身可被抵赖"


def test_连续越权触发R01告警(client, login, fake_redis):
    """契约 R01：同一主体 5 分钟内 3 次越权 → 高危告警。"""
    admin = login("admin")
    vpp = login("vpp")
    for _ in range(3):
        client.get("/api/v1/users", headers=vpp)

    alerts = client.get("/api/v1/audit/alerts?size=20", headers=admin).json()["data"]
    hits = [a for a in alerts["items"] if a["ruleCode"] == "R01_UNAUTHORIZED"]
    assert hits, "连续三次越权没有触发 R01"
    assert hits[0]["riskLevel"] == "high"


# ================================================================ C. 水平越权

@pytest.fixture
def 两份资产(client, login, SessionFactory):
    """给 subject 绑一个真身份，再造两条资产：一条是他的，一条是别人的。"""
    from modules.auth.model import SysUser

    admin = login("admin")
    mine = client.post("/api/v1/did/register", headers=admin, json={
        "subjectType": "device", "subjectName": "属于能源主体的表计", "custody": True,
    }).json()["data"]["did"]
    others = client.post("/api/v1/did/register", headers=admin, json={
        "subjectType": "device", "subjectName": "属于别人的表计", "custody": True,
    }).json()["data"]["did"]

    with SessionFactory() as db:
        原did = db.get(SysUser, 4).did
        db.get(SysUser, 4).did = mine
        db.commit()

    def _reg(did, name):
        return client.post("/api/v1/assets", headers=admin, json={
            "name": name, "dataType": "pv", "sourceDid": did,
            "payload": {"pvOutput": 1.0}, "recordCount": 10, "level": "L2",
        }).json()["data"]["id"]

    ids = {"mine": _reg(mine, "我的光伏数据"), "others": _reg(others, "别人的光伏数据"),
           "myDid": mine, "othersDid": others}
    yield ids

    with SessionFactory() as db:
        db.get(SysUser, 4).did = 原did
        db.commit()


def test_能源主体拿不到别人的资产详情(client, login, 两份资产):
    """own 范围的角色，改一下 URL 里的 id 也翻不出别人的数据。"""
    headers = login("subject")

    assert client.get(f"/api/v1/assets/{两份资产['mine']}",
                      headers=headers).status_code == 200

    r = client.get(f"/api/v1/assets/{两份资产['others']}", headers=headers)
    assert r.status_code == 403 and r.json()["code"] == 1003


def test_溯源链也受同一套属主校验(client, login, 两份资产):
    """详情挡住了、溯源接口漏了，等于没挡。"""
    headers = login("subject")
    r = client.get(f"/api/v1/assets/{两份资产['others']}/lineage", headers=headers)
    assert r.status_code == 403 and r.json()["code"] == 1003


def test_列表接口在SQL层就过滤掉别人的数据(client, login, 两份资产):
    """不是查出来再删，是根本不查出来——分页总数也不能泄露别人有多少条。"""
    data = client.get("/api/v1/assets?page=1&size=200",
                      headers=login("subject")).json()["data"]
    assert data["items"], "至少应该看得到自己那条"
    assert all(i["ownerDid"] == 两份资产["myDid"] for i in data["items"])
    assert data["total"] == len(data["items"])


def test_显式授权后才能看到别人的数据(client, login, 两份资产, SessionFactory):
    """水平越权的正当出口是走申请-审批，不是绕过权限中心。"""
    from datetime import datetime, timedelta

    from modules.permission.model import PermGrant

    headers = login("subject")
    assert client.get(f"/api/v1/assets/{两份资产['others']}",
                      headers=headers).status_code == 403

    with SessionFactory() as db:
        db.add(PermGrant(grantee_did=两份资产["myDid"], grantee_name="能源主体",
                         resource_type="asset", resource_id=str(两份资产["others"]),
                         action="read", status="active",
                         expire_at=datetime.now() + timedelta(days=1)))
        db.commit()

    assert client.get(f"/api/v1/assets/{两份资产['others']}",
                      headers=headers).status_code == 200


def test_授权过期后自动失效(client, login, 两份资产, SessionFactory):
    """授权是有期限的，到点自动关门，不需要人去回收。"""
    from datetime import datetime, timedelta

    from modules.permission.model import PermGrant

    with SessionFactory() as db:
        db.add(PermGrant(grantee_did=两份资产["myDid"], grantee_name="能源主体",
                         resource_type="asset", resource_id=str(两份资产["others"]),
                         action="read", status="active",
                         expire_at=datetime.now() - timedelta(minutes=1)))
        db.commit()

    r = client.get(f"/api/v1/assets/{两份资产['others']}", headers=login("subject"))
    assert r.status_code == 403 and r.json()["code"] == 1003


# ================================================================ D. 身份伪造

def test_伪造签名下发被拒(client, login, algo_stub, admin_did):
    """有下发权限，但签名是编的 → 1004，不是 1003。两种拒绝要分得清。"""
    headers = admin_did["headers"]
    task_id = _new_dispatch(client, headers)

    r = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers,
                    json={"signature": "ab" * 64})
    assert r.status_code == 403 and r.json()["code"] == 1004


def test_用自己的私钥冒充别人的身份签名(client, login, algo_stub, admin_did):
    """拿到别人的 DID 不等于拿到别人的私钥。"""
    from core.gm_crypto import generate_keypair, sign as sm2_sign

    headers = admin_did["headers"]
    task_id = _new_dispatch(client, headers)
    payload = client.get(f"/api/v1/dispatch/tasks/{task_id}",
                         headers=headers).json()["data"]["signPayload"]

    攻击者公钥, 攻击者私钥 = generate_keypair()
    signature = sm2_sign(payload, 攻击者私钥, 攻击者公钥)

    r = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers,
                    json={"signature": signature})
    assert r.status_code == 403 and r.json()["code"] == 1004


def test_签名不能跨任务重放(client, login, algo_stub, admin_did):
    """待签原文里绑了任务号，A 任务的合法签名搬到 B 任务上就失效。"""
    from core.gm_crypto import sign as sm2_sign

    headers = admin_did["headers"]
    任务A = _new_dispatch(client, headers)
    任务B = _new_dispatch(client, headers)

    payloadA = client.get(f"/api/v1/dispatch/tasks/{任务A}",
                          headers=headers).json()["data"]["signPayload"]
    payloadB = client.get(f"/api/v1/dispatch/tasks/{任务B}",
                          headers=headers).json()["data"]["signPayload"]
    assert payloadA != payloadB, "两个任务的待签原文必须不同，否则签名可以随便搬"

    签名A = sm2_sign(payloadA, admin_did["privateKey"], admin_did["publicKey"])
    assert client.post(f"/api/v1/dispatch/tasks/{任务A}/issue", headers=headers,
                       json={"signature": 签名A}).status_code == 200

    r = client.post(f"/api/v1/dispatch/tasks/{任务B}/issue", headers=headers,
                    json={"signature": 签名A})
    assert r.status_code == 403 and r.json()["code"] == 1004


def test_策略被改后旧签名自动失效(client, login, algo_stub, admin_did, SessionFactory):
    """签的是策略摘要而不是任务号——内容被改过，旧签名自动作废。

    模拟的场景是：调度员签好了字，指令还没发出去，有人把策略里的功率改大了。
    如果待签原文只有任务号，这一改是察觉不到的。
    """
    from sqlalchemy import select

    from core.gm_crypto import sign as sm2_sign
    from modules.algo.model import AlgoDispatchTask

    headers = admin_did["headers"]
    task_id = _new_dispatch(client, headers)
    旧原文 = client.get(f"/api/v1/dispatch/tasks/{task_id}",
                        headers=headers).json()["data"]["signPayload"]
    旧签名 = sm2_sign(旧原文, admin_did["privateKey"], admin_did["publicKey"])

    # 绕过接口直接改库里的策略，把某个节点的功率放大十倍
    with SessionFactory() as db:
        task = db.execute(select(AlgoDispatchTask)
                          .where(AlgoDispatchTask.task_id == task_id)).scalars().one()
        策略 = json.loads(json.dumps(task.strategy))
        策略["actions"][0]["powerKw"] = 策略["actions"][0]["powerKw"] * 10 + 1
        task.strategy = 策略
        db.commit()

    新原文 = client.get(f"/api/v1/dispatch/tasks/{task_id}",
                        headers=headers).json()["data"]["signPayload"]
    assert 新原文 != 旧原文, "策略变了但待签原文没变，签名就锁不住内容"

    r = client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers,
                    json={"signature": 旧签名})
    assert r.status_code == 403 and r.json()["code"] == 1004

    # 换成对新策略的签名才放行
    新签名 = sm2_sign(新原文, admin_did["privateKey"], admin_did["publicKey"])
    assert client.post(f"/api/v1/dispatch/tasks/{task_id}/issue", headers=headers,
                       json={"signature": 新签名}).status_code == 200


def test_设备上线的nonce不能重放(client, login, SessionFactory, fake_redis):
    """截获一次合法的上线请求，重放第二次要被挡。"""
    from core.gm_crypto import sign as sm2_sign

    admin = login("admin")
    identity = client.post("/api/v1/did/register", headers=admin, json={
        "subjectType": "device", "subjectName": "重放测试网关", "custody": True,
    }).json()["data"]

    # 别的用例可能已经把节点绑给了另一个身份，这里先解绑，只测重放这一件事
    from modules.node.model import NodeInfo

    with SessionFactory() as db:
        db.get(NodeInfo, "Node-B").did = None
        db.commit()

    nonce = "replay-nonce-0001"
    body = {"did": identity["did"], "nonce": nonce,
            "signature": sm2_sign(nonce, identity["privateKey"], identity["publicKey"])}

    assert client.post("/api/v1/nodes/Node-B/online", headers=admin,
                       json=body).status_code == 200
    r = client.post("/api/v1/nodes/Node-B/online", headers=admin, json=body)
    assert r.status_code == 403 and r.json()["code"] == 1004
    assert "nonce" in r.json()["message"] or "重放" in r.json()["message"]


def test_冻结的身份持有效token也进不来(client, login, SessionFactory, fake_redis):
    """Token 没过期不代表身份还有效——身份状态是每次请求都查的。"""
    from modules.auth.model import SysUser

    admin = login("admin")
    identity = client.post("/api/v1/did/register", headers=admin, json={
        "subjectType": "user", "subjectName": "边缘节点", "custody": True,
    }).json()["data"]

    with SessionFactory() as db:
        原did = db.get(SysUser, 6).did
        db.get(SysUser, 6).did = identity["did"]
        db.commit()
    try:
        headers = login("edge")
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

        client.post(f"/api/v1/did/{identity['did']}/status", headers=admin,
                    json={"action": "freeze", "reason": "安全测试"})

        r = client.get("/api/v1/assets", headers=headers)
        assert r.status_code == 403 and r.json()["code"] == 1004
        assert "frozen" in r.json()["message"] or "冻结" in r.json()["message"]

        alerts = client.get("/api/v1/audit/alerts?size=20", headers=admin).json()["data"]
        hits = [a for a in alerts["items"] if a["ruleCode"] == "R02_ABNORMAL_DID"]
        assert hits and hits[0]["riskLevel"] == "critical", "异常身份接入必须立刻告警"
    finally:
        with SessionFactory() as db:
            db.get(SysUser, 6).did = 原did
            db.commit()


def test_轮换密钥后旧私钥签不动了(client, login):
    """密钥轮换是止损手段——私钥泄露后换一把，旧的立刻作废。"""
    from core.gm_crypto import sign as sm2_sign

    admin = login("admin")
    old = client.post("/api/v1/did/register", headers=admin, json={
        "subjectType": "device", "subjectName": "轮换测试设备", "custody": True,
    }).json()["data"]

    message = "rotate-check-0001"
    def _verify(sig):
        return client.post("/api/v1/did/verify", headers=admin, json={
            "did": old["did"], "message": message, "signature": sig,
        }).json()["data"]["valid"]

    旧签名 = sm2_sign(message, old["privateKey"], old["publicKey"])
    assert _verify(旧签名) is True

    client.post(f"/api/v1/did/{old['did']}/rotate-key", headers=admin,
                json={"reason": "私钥疑似泄露"})
    assert _verify(旧签名) is False


# ================================================================ E. 注入与恶意输入

@pytest.mark.parametrize("username", [
    "admin' OR '1'='1",
    "admin'--",
    "admin'; DROP TABLE sys_user; --",
    "' UNION SELECT 1,2,3 --",
    "admin\" OR \"\"=\"",
])
def test_登录用户名注入无效(client, username):
    """ORM 参数化查询，注入串只会被当成一个普通用户名。"""
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "x"})
    assert r.status_code == 401 and r.json()["code"] == 1002


def test_注入尝试之后系统还活着(client, login):
    """DROP TABLE 打过去之后，正常登录必须照常工作——表没被删掉。"""
    client.post("/api/v1/auth/login",
                json={"username": "admin'; DROP TABLE sys_user; --", "password": "x"})
    assert client.post("/api/v1/auth/login",
                       json={"username": "admin", "password": "admin123"}).status_code == 200


@pytest.mark.parametrize("keyword", [
    "' OR 1=1 --",
    "'; DELETE FROM energy_asset WHERE '1'='1",
    "\\",
    "1' UNION SELECT password_hash FROM sys_user --",
])
def test_搜索关键字注入无效(client, login, keyword):
    """关键字走 LIKE 绑定参数，注入串只会被当成一个搜不到的普通字符串。"""
    r = client.get("/api/v1/assets", headers=login("admin"), params={"keyword": keyword})
    assert r.status_code == 200 and r.json()["code"] == 0
    assert r.json()["data"]["items"] == []


def test_LIKE通配符不会越过数据范围(client, login, 两份资产):
    """% 能匹配到更多行，但匹配得再多也越不过 own 范围的属主过滤。"""
    data = client.get("/api/v1/assets", headers=login("subject"),
                      params={"keyword": "%", "size": 200}).json()["data"]
    assert all(i["ownerDid"] == 两份资产["myDid"] for i in data["items"])


@pytest.mark.parametrize("table", [
    "audit_log_202608; DROP TABLE sys_user",
    "audit_log_202608 UNION SELECT * FROM sys_user",
    "sys_user",
    "audit_log_20260",
    "audit_log_2026081",
    "../../etc/passwd",
    "",
])
def test_审计分表名白名单挡住注入(db, table):
    """分表名是拼进 SQL 的，所以只认 audit_log_ + 6 位数字，别的一律拒。"""
    from core.exceptions import ParamError
    from modules.audit import sharding

    with pytest.raises(ParamError):
        sharding.assert_valid(table)


def test_合法分表名照常放行(db):
    from modules.audit import sharding

    assert sharding.assert_valid("audit_log_202608") == "audit_log_202608"


@pytest.mark.parametrize("value", ["high' OR '1'='1", "HIGH", "extreme", "low;--"])
def test_枚举参数只认契约里的值(client, login, value):
    """riskLevel / result 这类枚举用正则卡死，非法值直接 1001。"""
    r = client.get("/api/v1/audit/logs", headers=login("admin"),
                   params={"riskLevel": value})
    assert r.status_code == 400 and r.json()["code"] == 1001


@pytest.mark.parametrize("params", [
    {"page": 0, "size": 20},        # 页码从 1 开始
    {"page": 1, "size": 0},         # 每页至少 1 条
    {"page": 1, "size": 100000},    # 上限 200，树莓派禁止拉全表
    {"page": -1, "size": 20},
    {"page": "abc", "size": 20},
])
def test_分页参数越界返回1001(client, login, params):
    r = client.get("/api/v1/assets", headers=login("admin"), params=params)
    assert r.status_code == 400 and r.json()["code"] == 1001


def test_路径穿越取不到东西(client, login):
    """DID 路由用了 {did:path}，得确认它不会变成一个任意读文件的口子。"""
    for evil in ("../../../etc/passwd", "..%2f..%2fetc%2fpasswd", "%2e%2e/%2e%2e/main.py"):
        r = client.get(f"/api/v1/did/{evil}", headers=login("admin"))
        assert r.status_code in (400, 404), f"{evil} -> {r.status_code}"
        assert "root:" not in r.text


def test_超长输入不会打穿(client, login):
    """字段长度在 pydantic 层就卡住，不会带着 10 万字符去撞数据库。"""
    r = client.post("/api/v1/assets", headers=login("admin"), json={
        "name": "长" * 100000, "dataType": "pv",
        "sourceDid": "did:vpp:device:0x00000000000000000000000000000001",
        "payload": {"pvOutput": 1.0},
    })
    assert r.status_code in (400, 403, 404), r.status_code
    assert r.json()["code"] in (1001, 1003, 1004, 1005)


def test_畸形json返回1001而不是500(client, login):
    r = client.post("/api/v1/auth/login", content=b"{not json at all",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["code"] == 1001


def test_脚本内容原样存取不被执行(client, login):
    """存进去什么样，取出来什么样——后端不做二次解释，转义交给前端。

    这里断言的是「不当成代码执行」，不是「过滤掉」——过滤会破坏数据真实性。
    """
    admin = login("admin")
    did = client.post("/api/v1/did/register", headers=admin, json={
        "subjectType": "device", "subjectName": "<script>alert(1)</script>",
        "custody": True,
    }).json()["data"]["did"]

    detail = client.get(f"/api/v1/did/{did}", headers=admin).json()["data"]
    assert detail["subjectName"] == "<script>alert(1)</script>"


# ================================================================ F. 信息泄露

def test_登录失败不区分账号存不存在(client):
    """不能让人拿登录接口当用户名字典跑。"""
    不存在 = client.post("/api/v1/auth/login",
                         json={"username": "nobody-here", "password": "x"}).json()
    密码错 = client.post("/api/v1/auth/login",
                         json={"username": "admin", "password": "wrong"}).json()

    assert 不存在["code"] == 密码错["code"] == 1002
    assert 不存在["message"] == 密码错["message"] == "用户名或密码错误"


def test_任何响应都不含口令哈希与平台私钥(client, login):
    """全量扫一遍 GET 接口，看响应体里有没有不该出现的东西。"""
    admin = login("admin")
    敏感词 = ["password_hash", "passwordHash", "$2b$", "$2a$",
              "private_key_enc", "JWT_SECRET", "KEY_CUSTODY_SECRET"]

    命中 = []
    for method, path in _api_routes():
        if method != "GET":
            continue
        resp = client.get(_fill(path), headers=admin)
        for 词 in 敏感词:
            if 词 in resp.text:
                命中.append(f"{path} 泄露了 {词}")
    assert not 命中, "\n".join(命中)


def test_did详情不返回私钥(client, login):
    """非托管密钥平台根本不存；托管的也只在签发那一刻返回一次。"""
    admin = login("admin")
    did = client.post("/api/v1/did/register", headers=admin, json={
        "subjectType": "device", "subjectName": "私钥泄露测试", "custody": True,
    }).json()["data"]["did"]

    for path in (f"/api/v1/did/{did}", f"/api/v1/did/{did}/document", "/api/v1/did"):
        text = client.get(path, headers=admin).text
        assert "privateKey" not in text, path
        assert "private_key" not in text, path


def test_内部异常不泄露堆栈(client, login, SessionFactory, monkeypatch):
    """500 也要是统一信封，不能把文件路径和 SQL 抛给前端。

    这里得另起一个 raise_server_exceptions=False 的 TestClient——
    默认的那个会把服务端异常原样抛回测试进程，测不到真实的 500 响应。
    """
    from fastapi.testclient import TestClient

    import main
    from core.database import get_db
    from modules.asset import service as asset_service

    def _boom(*a, **kw):
        raise RuntimeError("SELECT * FROM energy_asset WHERE secret='xxx' 崩了")

    monkeypatch.setattr(asset_service, "stats", _boom)

    def _override_get_db():
        with SessionFactory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    with TestClient(main.app, raise_server_exceptions=False) as c:
        r = c.get("/api/v1/assets/stats", headers=login("admin"))

    assert r.status_code == 500 and r.json()["code"] == 5000
    assert r.json()["message"] == "服务器内部错误"
    assert "SELECT" not in r.text and "Traceback" not in r.text
    assert "energy_asset" not in r.text


def test_所有错误响应都是统一信封(client, login):
    """契约 1.1：无论成功失败，四个字段一个不少。"""
    样本 = [
        client.get("/api/v1/auth/me"),                                   # 1002
        client.get("/api/v1/users", headers=login("vpp")),               # 1003
        client.get("/api/v1/assets/99999999", headers=login("admin")),   # 1005
        client.get("/api/v1/audit/logs?riskLevel=bad", headers=login("admin")),  # 1001
        client.get("/api/v1/这个接口不存在", headers=login("admin")),      # 404
    ]
    for r in 样本:
        body = r.json()
        assert set(body) == {"code", "message", "data", "traceId"}, body
        assert body["code"] != 0
        assert body["traceId"].startswith("tr-")
        assert r.headers.get("X-Trace-Id") == body["traceId"]


def test_健康检查不泄露内部信息(client):
    """/health 是公开的，不能顺手把版本号之外的东西吐出去。"""
    body = client.get("/health").json()
    assert "password" not in json.dumps(body).lower()
    assert "secret" not in json.dumps(body).lower()


# ================================================================ G. 审计绕过

def test_没有任何接口能删改审计日志(client):
    """审计日志只进不出。有一个写接口，整套追责就不成立了。"""
    可写的审计接口 = [
        f"{m} {p}" for m, p in _api_routes()
        if p.startswith("/api/v1/audit/logs") and m in ("POST", "PUT", "PATCH", "DELETE")
    ]
    assert not 可写的审计接口, f"审计日志出现了写接口：{可写的审计接口}"


def test_没有任何接口能删改存证(client):
    """存证同理。demo/tamper 是演示专用的反面教材，且只有 sys_admin 能调。"""
    可写的存证接口 = [
        f"{m} {p}" for m, p in _api_routes()
        if p.startswith("/api/v1/evidence/") and m in ("PUT", "PATCH", "DELETE")
    ]
    assert not 可写的存证接口, f"存证出现了改写接口：{可写的存证接口}"


def test_审计埋点是声明式的没有漏埋(client, login):
    """所有高危动作都靠 @audited 装饰器埋点，不靠人记得写。"""
    import inspect

    from modules.algo import router as algo_router
    from modules.did import router as did_router
    from modules.evidence import router as evidence_router

    for module in (did_router, evidence_router, algo_router):
        源码 = inspect.getsource(module)
        assert "write_audit(" not in 源码, f"{module.__name__} 里出现了手写审计调用"


def test_绕过应用层改数据库会被链校验抓出来(client, login, SessionFactory):
    """最狠的一种攻击：数据库被人直接改了。哈希链是最后一道防线。"""
    from sqlalchemy import select

    from modules.evidence.model import ChainEvidence

    admin = login("admin")
    did = client.post("/api/v1/did/register", headers=admin, json={
        "subjectType": "device", "subjectName": "链校验测试设备", "custody": True,
    }).json()["data"]["did"]
    client.post("/api/v1/assets", headers=admin, json={
        "name": "被篡改的数据", "dataType": "pv", "sourceDid": did,
        "payload": {"pvOutput": 45.3}, "level": "L2",
    })

    before = client.get("/api/v1/evidence/chain/status", headers=admin).json()["data"]
    assert before["intact"] is True

    with SessionFactory() as db:
        row = db.execute(select(ChainEvidence)
                         .order_by(ChainEvidence.block_height.desc())).scalars().first()
        原快照 = dict(row.payload_snapshot)
        改后 = dict(原快照)
        改后["pvOutput"] = 999.9
        row.payload_snapshot = 改后
        db.commit()
        evidence_id, height = row.evidence_id, row.block_height

    try:
        after = client.get("/api/v1/evidence/chain/status", headers=admin).json()["data"]
        assert after["intact"] is False
        assert after["brokenAt"] == evidence_id or after["brokenAt"] == str(height)

        single = client.post("/api/v1/evidence/verify", headers=admin,
                             json={"evidenceId": evidence_id}).json()["data"]
        assert single["intact"] is False
        assert "篡改" in single["message"]
    finally:
        with SessionFactory() as db:
            回滚 = db.execute(select(ChainEvidence)
                              .where(ChainEvidence.evidence_id == evidence_id)) \
                     .scalars().first()
            回滚.payload_snapshot = 原快照
            db.commit()

    assert client.get("/api/v1/evidence/chain/status",
                      headers=admin).json()["data"]["intact"] is True


def test_伪造一个新块插不进链里(client, login, SessionFactory):
    """攻击者想补一条「我有权限」的存证，prev_hash 对不上就露馅。"""
    from sqlalchemy import select

    from modules.evidence.model import ChainEvidence

    admin = login("admin")
    with SessionFactory() as db:
        tip = db.execute(select(ChainEvidence)
                         .order_by(ChainEvidence.block_height.desc())).scalars().first()
        伪块 = ChainEvidence(
            evidence_id="ev-forged-0001", category="permission", ref_id="0",
            actor_did="did:vpp:user:0xdeadbeef",
            payload_hash="sm3:" + "0" * 64,
            prev_hash="sm3:" + "f" * 64,          # 随手编的前哈希
            block_hash="sm3:" + "1" * 64,
            block_height=tip.block_height + 1, tx_id="blk-forged",
            payload_snapshot={"granted": True},
        )
        db.add(伪块)
        db.commit()

    try:
        status = client.get("/api/v1/evidence/chain/status",
                            headers=admin).json()["data"]
        assert status["intact"] is False, "凭空插入的块没有被检出"
    finally:
        with SessionFactory() as db:
            obj = db.execute(select(ChainEvidence)
                             .where(ChainEvidence.evidence_id == "ev-forged-0001")) \
                    .scalars().first()
            if obj:
                db.delete(obj)
                db.commit()

    assert client.get("/api/v1/evidence/chain/status",
                      headers=admin).json()["data"]["intact"] is True
