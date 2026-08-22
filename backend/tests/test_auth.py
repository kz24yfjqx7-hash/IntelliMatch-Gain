"""认证链路测试：登录、Token、当前用户、登出、用户管理。"""


def test_登录成功返回统一包装(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"code", "message", "data", "traceId"}
    assert body["code"] == 0
    assert body["traceId"].startswith("tr-")

    data = body["data"]
    assert data["expiresIn"] == 28800          # 契约 1.3：8 小时
    assert data["user"]["roles"] == ["sys_admin"]
    assert data["user"]["realName"] == "系统管理员"
    assert data["token"].count(".") == 2       # JWT 三段


def test_密码错误返回1002且不泄露账号是否存在(client):
    r1 = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
    r2 = client.post("/api/v1/auth/login", json={"username": "不存在的人", "password": "wrong"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["code"] == r2.json()["code"] == 1002
    assert r1.json()["message"] == r2.json()["message"]


def test_缺少参数返回1001(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin"})
    assert r.status_code == 400
    assert r.json()["code"] == 1001


def test_未登录访问受保护接口返回1002(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["code"] == 1002


def test_伪造token返回1002(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.forged.signature"})
    assert r.status_code == 401
    assert r.json()["code"] == 1002


def test_me返回扁平权限串(client, login):
    r = client.get("/api/v1/auth/me", headers=login("grid"))
    data = r.json()["data"]
    assert data["username"] == "grid"
    assert "dispatch:issue" in data["permissions"]
    assert "asset:write" not in data["permissions"]   # 电网调度员没有写资产的权限


def test_不同角色权限集合不同(client, login):
    admin = client.get("/api/v1/auth/me", headers=login("admin")).json()["data"]["permissions"]
    subject = client.get("/api/v1/auth/me", headers=login("subject")).json()["data"]["permissions"]
    assert "dispatch:issue" in admin
    assert "dispatch:issue" not in subject
    assert len(admin) > len(subject)


def test_客户端可指定traceId并全链路沿用(client, login):
    trace = "tr-20260818-deadbeef"
    r = client.get("/api/v1/auth/me", headers={**login("admin"), "X-Trace-Id": trace})
    assert r.json()["traceId"] == trace
    assert r.headers["X-Trace-Id"] == trace


def test_登出后token失效(client, login, monkeypatch):
    from core import redis_client

    blacklist = set()
    monkeypatch.setattr(redis_client, "safe_set", lambda k, v, ex=None: blacklist.add(k) or True)
    monkeypatch.setattr(redis_client, "safe_exists", lambda k: k in blacklist)

    headers = login("admin")
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).json()["code"] == 0
    r = client.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 401 and r.json()["code"] == 1002


def test_仅管理员可管理用户(client, login):
    assert client.get("/api/v1/users", headers=login("admin")).status_code == 200
    r = client.get("/api/v1/users", headers=login("vpp"))
    assert r.status_code == 403 and r.json()["code"] == 1003


def test_用户列表分页结构(client, login):
    data = client.get("/api/v1/users?page=1&size=3", headers=login("admin")).json()["data"]
    assert set(data) == {"items", "total", "page", "size"}
    assert len(data["items"]) == 3
    assert data["total"] == 6


def test_分页size超上限被截断(client, login):
    data = client.get("/api/v1/users?page=1&size=9999", headers=login("admin"))
    assert data.status_code == 400   # 契约 1.4：size 最大 200，超出按参数错误处理


def test_新建用户与角色校验(client, login):
    headers = login("admin")
    r = client.post("/api/v1/users", headers=headers, json={
        "username": "tester01", "password": "test123456", "realName": "测试员",
        "orgName": "测试机构", "roles": ["regulator"], "bindDid": False,
    })
    assert r.json()["code"] == 0
    assert r.json()["data"]["roles"] == ["regulator"]

    dup = client.post("/api/v1/users", headers=headers, json={
        "username": "tester01", "password": "test123456", "realName": "重名",
        "roles": ["regulator"], "bindDid": False,
    })
    assert dup.status_code == 409 and dup.json()["code"] == 1006

    bad = client.post("/api/v1/users", headers=headers, json={
        "username": "tester02", "password": "test123456", "realName": "测试员",
        "roles": ["不存在的角色"], "bindDid": False,
    })
    assert bad.status_code == 400 and bad.json()["code"] == 1001


def test_不能删除自己和内置管理员(client, login):
    headers = login("admin")
    r = client.delete("/api/v1/users/1", headers=headers)
    assert r.status_code == 400 and "当前登录" in r.json()["message"]
