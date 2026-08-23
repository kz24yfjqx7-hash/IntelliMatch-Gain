"""测试夹具。

用 SQLite 内存库跑业务逻辑测试，不依赖 MySQL 和 Redis——
这样在任何机器上 `pytest` 都能直接跑，不用先起容器。
MySQL 专有的建表语句由 sql/01_schema.sql 负责，在真机上用部署验证清单核对。
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import redis_client  # noqa: E402
from core.database import Base  # noqa: E402
from core.security import hash_password  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _disable_redis_cache():
    """禁用 Redis 缓存与计数，避免测试之间互相污染。"""
    redis_client.safe_get = lambda *a, **kw: None
    redis_client.safe_set = lambda *a, **kw: True
    redis_client.safe_exists = lambda *a, **kw: False
    redis_client.safe_delete = lambda *a, **kw: False
    redis_client.safe_incr_window = lambda *a, **kw: 0
    redis_client.safe_lpush_trim = lambda *a, **kw: None
    redis_client.ping = lambda: False
    yield


@pytest.fixture(scope="session")
def engine():
    # StaticPool 必须加：TestClient 会把同步接口丢到线程池里跑，
    # 默认连接池会给每个线程开一条新连接，而 :memory: 的每条连接都是一个独立的空库。
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    # 导入所有模型，让 metadata 齐全
    import modules.algo.model  # noqa: F401
    import modules.asset.model  # noqa: F401
    import modules.audit.model  # noqa: F401
    import modules.auth.model  # noqa: F401
    import modules.did.model  # noqa: F401
    import modules.evidence.model  # noqa: F401
    import modules.node.model  # noqa: F401
    import modules.notice.model  # noqa: F401
    import modules.permission.model  # noqa: F401

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="session")
def SessionFactory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@pytest.fixture(scope="session", autouse=True)
def _patch_session_factory(SessionFactory, engine):
    """把各处直接使用的 SessionLocal 换成测试用的工厂。"""
    import core.database as database
    import modules.permission.service as perm_service

    database.SessionLocal = SessionFactory
    database.engine = engine
    perm_service.SessionLocal = SessionFactory
    yield


@pytest.fixture(scope="session", autouse=True)
def seed(SessionFactory, _patch_session_factory):
    """最小种子：6 个角色 + 权限矩阵 + 6 个演示账号，与 02_seed.sql 保持一致。"""
    from modules.auth.model import SysRole, SysUser, SysUserRole
    from modules.permission.model import SysRolePermission

    roles = [
        ("sys_admin", "系统管理员"), ("grid_dispatcher", "电网调度员"),
        ("vpp_operator", "虚拟电厂运营商"), ("energy_subject", "能源主体"),
        ("regulator", "监管方"), ("edge_node", "边缘节点"),
    ]
    perms = {
        "sys_admin": [("asset", "read", "all"), ("asset", "write", "all"), ("asset", "export", "all"),
                      ("model", "read", "all"), ("dispatch", "read", "all"), ("dispatch", "issue", "all"),
                      ("evidence", "read", "all"), ("algo", "execute", "all"), ("user", "manage", "all")],
        "grid_dispatcher": [("asset", "read", "all"), ("asset", "export", "all"), ("model", "read", "all"),
                            ("dispatch", "read", "all"), ("dispatch", "issue", "all"),
                            ("evidence", "read", "all"), ("algo", "execute", "all")],
        "vpp_operator": [("asset", "read", "all"), ("asset", "write", "all"), ("model", "read", "all"),
                         ("dispatch", "read", "all"), ("evidence", "read", "all")],
        "energy_subject": [("asset", "read", "own"), ("asset", "write", "own"), ("evidence", "read", "own")],
        "regulator": [("asset", "read", "all"), ("asset", "export", "all"), ("model", "read", "all"),
                      ("dispatch", "read", "all"), ("evidence", "read", "all")],
        "edge_node": [("asset", "read", "own"), ("asset", "write", "own"), ("model", "read", "all"),
                      ("dispatch", "read", "all")],
    }
    users = [
        (1, "admin", "admin123", "系统管理员", "sys_admin"),
        (2, "grid", "grid123", "电网调度员", "grid_dispatcher"),
        (3, "vpp", "vpp123", "虚拟电厂运营商", "vpp_operator"),
        (4, "subject", "subject123", "能源主体", "energy_subject"),
        (5, "regulator", "reg123", "监管方", "regulator"),
        (6, "edge", "edge123", "边缘节点", "edge_node"),
    ]

    nodes = [
        ("Node-A", "虚拟电厂节点A", "online", "VPP-2000", 45.3, -12.0, 120.0, 65.0),
        ("Node-B", "虚拟电厂节点B", "online", "VPP-2000", 32.1, 8.5, 85.0, 78.0),
        ("Node-C", "虚拟电厂节点C", "warning", "VPP-3000", 28.7, -25.3, 150.0, 42.0),
        ("Node-D", "虚拟电厂节点D", "online", "VPP-2000", 38.9, 5.2, 95.0, 82.0),
    ]

    with SessionFactory() as db:
        for i, (code, name) in enumerate(roles):
            db.add(SysRole(id=i + 1, code=code, name=name, is_builtin=1))
        for code, plist in perms.items():
            for res, act, scope in plist:
                db.add(SysRolePermission(role_code=code, resource_type=res, action=act, scope=scope))
        for uid, uname, pwd, real, role in users:
            db.add(SysUser(id=uid, username=uname, password_hash=hash_password(pwd),
                           real_name=real, org_name="测试机构",
                           did=f"did:vpp:user:0x{uid:032d}", status="active"))
            db.add(SysUserRole(user_id=uid, role_code=role))

        # 四个节点，字段与 raspi/public/data/mock.json 对齐
        from datetime import datetime, timedelta

        from modules.node.model import NodeInfo, NodeMetric

        base = datetime(2026, 8, 18, 0, 0, 0)
        for node_id, name, status, model, pv, storage, load, soc in nodes:
            db.add(NodeInfo(id=node_id, name=name, status=status, model=model,
                            capacity_kw=200, pv_output=pv, storage_output=storage,
                            load_kw=load, soc=soc, location="测试园区"))
            for h in range(48):
                db.add(NodeMetric(node_id=node_id, ts=base - timedelta(hours=h),
                                  pv_output=pv, storage_output=storage,
                                  load_kw=load, soc=soc, price=0.62))
        db.commit()
    yield


@pytest.fixture
def db(SessionFactory, seed):
    with SessionFactory() as session:
        yield session


@pytest.fixture
def client(SessionFactory, seed):
    """带依赖覆盖的 TestClient，跳过启动时的 MySQL 等待。"""
    from fastapi.testclient import TestClient

    import main
    from core.database import get_db

    main.app.router.on_startup.clear()

    def _override_get_db():
        with SessionFactory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


@pytest.fixture
def login(client):
    """返回一个 login(username, password) -> headers 的辅助函数。"""

    def _login(username: str, password: str = None):
        default = {"admin": "admin123", "grid": "grid123", "vpp": "vpp123",
                   "subject": "subject123", "regulator": "reg123", "edge": "edge123"}
        resp = client.post("/api/v1/auth/login",
                           json={"username": username, "password": password or default[username]})
        assert resp.status_code == 200, resp.text
        token = resp.json()["data"]["token"]
        return {"Authorization": f"Bearer {token}"}

    return _login


@pytest.fixture
def algo_stub(monkeypatch):
    """把算法代理层的出口接到 tests/fake_algo.py 上，不用真起一个 8100 端口的服务。"""
    from fastapi.testclient import TestClient

    from core.exceptions import AlgoUnavailableError
    from modules.algo import client as algo_client
    from tests import fake_algo

    fake_algo._jobs.clear()
    fake_algo.ROUND_SECONDS = 0.0
    stub = TestClient(fake_algo.app)

    def _call(method, path, *, json=None, timeout=None, raise_on_error=True):
        resp = stub.request(method, "/algo/v1" + path, json=json)
        if resp.status_code >= 400:
            if raise_on_error:
                raise AlgoUnavailableError(f"算法服务返回 {resp.status_code}")
            return None
        return resp.json()

    monkeypatch.setattr(algo_client, "call", _call)
    return {"client": stub, "jobs": fake_algo._jobs}


@pytest.fixture
def algo_down(monkeypatch):
    """模拟算法服务整个不可达，用来验证各处的降级兜底。"""
    from core.exceptions import AlgoUnavailableError
    from modules.algo import client as algo_client

    def _call(method, path, *, json=None, timeout=None, raise_on_error=True):
        if raise_on_error:
            raise AlgoUnavailableError("算法服务不可用：连接被拒绝")
        return None

    monkeypatch.setattr(algo_client, "call", _call)


@pytest.fixture
def fake_redis(monkeypatch):
    """内存版 Redis：滑动窗口计数要真的能计数，风控规则和登出黑名单才测得动。"""
    from core import redis_client

    store: dict[str, str] = {}
    counters: dict[str, int] = {}

    monkeypatch.setattr(redis_client, "safe_set",
                        lambda k, v, ex=None: store.__setitem__(k, v) or True)
    monkeypatch.setattr(redis_client, "safe_get", lambda k: store.get(k))
    monkeypatch.setattr(redis_client, "safe_exists", lambda k: k in store)
    monkeypatch.setattr(redis_client, "safe_delete",
                        lambda k: store.pop(k, None) is not None)

    def incr(key, window):
        # 和真 Redis 一样：INCR 把计数写进同一个键，safe_get 读得到，safe_delete 清得掉
        n = int(store.get(key) or 0) + 1
        store[key] = str(n)
        counters[key] = n
        return n

    monkeypatch.setattr(redis_client, "safe_incr_window", incr)
    return {"store": store, "counters": counters}
