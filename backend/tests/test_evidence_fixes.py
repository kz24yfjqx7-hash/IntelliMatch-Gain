"""2026-08-22 缺陷修复轮的回归用例（fix-evidence）。

用例名带缺陷编号，一一对应：

- B-002 / API-EV-17、17b  energy_subject 的 evidence:read 是 scope='own'，
  列表与详情都必须按 actorDid 过滤，sys_admin / regulator / grid 仍读全量
- B-001（测试文档-接口与安全 §4）/ API-AUD-01  require_roles 路径的 1003 也要写审计日志
- B-001 / B-025（BACKEND-ISSUES）篡改演示必须可逆：连续篡改两次仍能还原到最初状态
- B-017  chain/status 的 brokenAt 是区块高度，evidenceId 另放 brokenAtEvidenceId
- API-PERF-04  chain/status 增量校验：不重算已验证过的块，但篡改照样检出
- B-010  并发写存证的锁冲突要重试，重试用尽要告警且调用方可感知
"""
import threading

import pytest
from sqlalchemy import select, text

from modules.evidence.model import ChainEvidence


# ---------------------------------------------------------------- 夹具

@pytest.fixture(autouse=True)
def _clean_backups(SessionFactory, seed):
    """别的用例可能留下没还原的快照备份，restore-all 会把它们一起写回去，
    盖到早已被重写过的区块上 —— 先清干净，保证本文件的用例互不干扰。"""
    from modules.evidence.model import ChainEvidenceBackup

    try:
        with SessionFactory() as db:
            db.query(ChainEvidenceBackup).delete()
            db.commit()
    except Exception:  # noqa: BLE001  表可能被「迁移未执行」用例临时删掉了
        pass
    yield


def _new_chain_db(tmp_path, name="chain.db"):
    """给链本身的用例开一个独立的 SQLite 库，不跟共享种子库互相干扰。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / name}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    ChainEvidence.__table__.create(engine, checkfirst=True)
    return engine, sessionmaker(bind=engine, autoflush=False,
                                expire_on_commit=False, future=True)


def _did_of(SessionFactory, username: str) -> str:
    from modules.auth.model import SysUser

    with SessionFactory() as db:
        return db.execute(
            select(SysUser.did).where(SysUser.username == username)
        ).scalar_one()


def _make_evidence(SessionFactory, actor_did: str, ref_id: str) -> str:
    """直接上链造一条指定属主的存证（单测种子里 sys_admin 没有 evidence:write）。"""
    from modules.evidence.chain import LocalHashChain

    with SessionFactory() as db:
        result = LocalHashChain().write(
            db, category="data", ref_id=ref_id, payload={"pvOutput": 12.5, "ref": ref_id},
            actor_did=actor_did, trace_id="tr-20260822-fixevid",
        )
        db.commit()
    return result["evidenceId"]


@pytest.fixture
def admin_did(SessionFactory, seed):
    return _did_of(SessionFactory, "admin")


@pytest.fixture
def subject_did(SessionFactory, seed):
    return _did_of(SessionFactory, "subject")


@pytest.fixture
def admin_evidence(SessionFactory, admin_did):
    """admin 名下的一条存证。"""
    return _make_evidence(SessionFactory, admin_did, "fix-admin-1")


@pytest.fixture
def subject_evidence(SessionFactory, subject_did):
    """挂在 subject 名下的一条存证。"""
    return _make_evidence(SessionFactory, subject_did, "fix-subject-1")


# ---------------------------------------------------------------- B-002 仅自有

def test_B002_能源主体的存证列表只有自己的(client, login, subject_did,
                                             admin_evidence, subject_evidence):
    data = client.get("/api/v1/evidence?size=200", headers=login("subject")).json()["data"]
    assert data["total"] >= 1
    assert all(i["actorDid"] == subject_did for i in data["items"]), \
        "energy_subject 读到了他人的存证（B-002 / API-EV-17）"
    assert subject_evidence in [i["evidenceId"] for i in data["items"]]
    assert admin_evidence not in [i["evidenceId"] for i in data["items"]]


def test_B002_能源主体读他人存证详情返回1003(client, login, admin_evidence):
    r = client.get(f"/api/v1/evidence/{admin_evidence}", headers=login("subject"))
    assert r.status_code == 403 and r.json()["code"] == 1003, \
        "energy_subject 能读他人存证详情（B-002 / API-EV-17b）"


def test_B002_能源主体读自己的存证详情正常(client, login, subject_evidence):
    r = client.get(f"/api/v1/evidence/{subject_evidence}", headers=login("subject"))
    assert r.status_code == 200 and r.json()["code"] == 0
    assert r.json()["data"]["verification"]["intact"] is True


def test_B002_他人凭证导出同样被拦(client, login, admin_evidence, subject_evidence):
    assert client.get(f"/api/v1/evidence/{admin_evidence}/certificate",
                      headers=login("subject")).status_code == 403
    assert client.get(f"/api/v1/evidence/{subject_evidence}/certificate",
                      headers=login("subject")).status_code == 200


@pytest.mark.parametrize("role", ["admin", "grid", "regulator", "vpp"])
def test_B002_全量范围的角色不受影响(client, login, role, admin_evidence, subject_evidence):
    """scope='all' 的角色必须还能读到全部存证，别把「仅自有」误伤到监管方。"""
    data = client.get("/api/v1/evidence?size=200", headers=login(role)).json()["data"]
    ids = [i["evidenceId"] for i in data["items"]]
    assert admin_evidence in ids and subject_evidence in ids
    assert client.get(f"/api/v1/evidence/{subject_evidence}",
                      headers=login(role)).status_code == 200


# ------------------------------------------- B-001(§4) require_roles 拒绝要留痕

def test_B001_require_roles拒绝也写审计日志(client, login):
    """API-AUD-01：vpp GET /users 被 require_roles 拒绝，必须能按 traceId 查到 denied 日志。"""
    trace = "tr-20260822-a0000001"
    r = client.get("/api/v1/users", headers={**login("vpp"), "X-Trace-Id": trace})
    assert r.status_code == 403 and r.json()["code"] == 1003

    data = client.get(f"/api/v1/audit/logs?traceId={trace}",
                      headers=login("admin")).json()["data"]
    assert data["total"] >= 1, "require_roles 路径的 1003 没有写审计日志（B-001 / API-AUD-01）"
    item = data["items"][0]
    assert item["result"] == "denied"
    assert item["riskLevel"] == "high"          # denied 一律抬到 high
    assert item["module"] == "auth"             # /users 归属 auth 模块
    assert item["action"] == "auth:read"
    assert item["actorName"] == "虚拟电厂运营商"
    assert item["evidenceId"].startswith("ev-")  # 高危自动上链，日志本身防篡改


def test_B001_audit接口的越权也留痕(client, login):
    trace = "tr-20260822-a0000002"
    r = client.get("/api/v1/audit/logs", headers={**login("subject"), "X-Trace-Id": trace})
    assert r.status_code == 403 and r.json()["code"] == 1003

    data = client.get(f"/api/v1/audit/logs?traceId={trace}",
                      headers=login("admin")).json()["data"]
    assert data["total"] >= 1
    assert data["items"][0]["module"] == "audit"
    assert data["items"][0]["result"] == "denied"


def test_B001_同一次拒绝只记一条不重复(client, login, admin_evidence):
    """demo/tamper 同时挂了 @audited 和 require_roles，依赖先执行，只应留一条。"""
    trace = "tr-20260822-a0000003"
    r = client.post("/api/v1/evidence/demo/tamper",
                    headers={**login("grid"), "X-Trace-Id": trace},
                    json={"evidenceId": admin_evidence, "newValue": {"x": 1}})
    assert r.status_code == 403

    data = client.get(f"/api/v1/audit/logs?traceId={trace}",
                      headers=login("admin")).json()["data"]
    assert data["total"] == 1, f"同一次拒绝被记了 {data['total']} 条"
    assert data["items"][0]["module"] == "evidence"


# ------------------------------------------- B-001 / B-025 篡改演示必须可逆

def _tamper(client, admin, evidence_id, value):
    return client.post("/api/v1/evidence/demo/tamper", headers=admin, json={
        "evidenceId": evidence_id, "newValue": value}).json()["data"]


def test_B001_连续篡改两次仍能还原到最初状态(client, login, admin_evidence, SessionFactory):
    admin = login("admin")
    first = _tamper(client, admin, admin_evidence, {"pvOutput": 999.9})
    原始 = first["originalPayload"]
    assert first["backupStore"] == "db"

    # 第二次篡改：备份不能被「第一次篡改后的数据」覆盖掉
    second = _tamper(client, admin, admin_evidence, {"pvOutput": 111.1, "extra": "x"})
    assert second["backupStore"] == "db(kept)"

    restored = client.post("/api/v1/evidence/demo/restore", headers=admin,
                           json={"evidenceId": admin_evidence}).json()["data"]
    assert restored["restored"] is True
    assert restored["verification"]["intact"] is True, \
        "连续篡改两次后还原不回最初状态（B-001 / B-025）"

    with SessionFactory() as db:
        snapshot = db.execute(
            select(ChainEvidence.payload_snapshot)
            .where(ChainEvidence.evidence_id == admin_evidence)
        ).scalar_one()
    assert snapshot == 原始

    status = client.get("/api/v1/evidence/chain/status", headers=admin).json()["data"]
    assert status["intact"] is True and status["brokenAt"] is None


def test_B025_还原成功后备份被删除下轮演示可重来(client, login, admin_evidence, SessionFactory):
    from modules.evidence.model import ChainEvidenceBackup

    admin = login("admin")
    _tamper(client, admin, admin_evidence, {"pvOutput": 999.9})
    client.post("/api/v1/evidence/demo/restore", headers=admin,
                json={"evidenceId": admin_evidence})

    with SessionFactory() as db:
        left = db.execute(
            select(ChainEvidenceBackup)
            .where(ChainEvidenceBackup.evidence_id == admin_evidence)
        ).scalar_one_or_none()
    assert left is None, "restore 之后备份行没有删除"

    # 第二轮演示照常可用
    again = _tamper(client, admin, admin_evidence, {"pvOutput": 777.7})
    assert again["backupStore"] == "db"
    assert client.post("/api/v1/evidence/demo/restore", headers=admin, json={
        "evidenceId": admin_evidence}).json()["data"]["verification"]["intact"] is True


def test_B025_一键还原全部(client, login, admin_evidence, subject_evidence):
    admin = login("admin")
    _tamper(client, admin, admin_evidence, {"pvOutput": 999.9})
    _tamper(client, admin, subject_evidence, {"pvOutput": 888.8})
    assert client.get("/api/v1/evidence/chain/status",
                      headers=admin).json()["data"]["intact"] is False

    data = client.post("/api/v1/evidence/demo/restore-all", headers=admin).json()["data"]
    assert data["restoredCount"] == 2
    assert not data["failed"]
    assert data["chain"]["intact"] is True and data["chain"]["brokenAt"] is None


def test_B025_迁移未执行时探测备份表不抛异常也不500(client, login, admin_evidence,
                                                     SessionFactory, monkeypatch,
                                                     fake_redis):
    """chain_evidence_backup 还没建、又没有建表权限时，必须降级回 Redis，而不是 500。"""
    from modules.evidence import service as ev_service
    from modules.evidence.model import ChainEvidenceBackup

    def 没有建表权限(*a, **kw):
        raise PermissionError("CREATE command denied")

    monkeypatch.setattr(ChainEvidenceBackup.__table__, "create", 没有建表权限)

    admin = login("admin")
    with SessionFactory() as db:
        ChainEvidenceBackup.__table__.drop(db.bind, checkfirst=True)
        try:
            assert ev_service._db_backup_available(db) is False

            first = _tamper(client, admin, admin_evidence, {"pvOutput": 999.9})
            assert first["backupStore"] == "redis"
            second = _tamper(client, admin, admin_evidence, {"pvOutput": 111.1})
            assert second["backupStore"] == "redis(kept)"   # 降级路径同样不覆盖

            restored = client.post("/api/v1/evidence/demo/restore", headers=admin, json={
                "evidenceId": admin_evidence}).json()["data"]
            assert restored["backupStore"] == "redis"
            assert restored["verification"]["intact"] is True
        finally:
            monkeypatch.undo()
            ChainEvidenceBackup.__table__.create(db.bind, checkfirst=True)


# ------------------------------------------- B-017 brokenAt 语义

def test_B017_brokenAt是高度另有brokenAtEvidenceId(client, login, admin_evidence):
    admin = login("admin")
    tampered = _tamper(client, admin, admin_evidence, {"pvOutput": 999.9})
    try:
        data = client.get("/api/v1/evidence/chain/status", headers=admin).json()["data"]
        assert data["intact"] is False
        assert isinstance(data["brokenAt"], int), \
            f"brokenAt 应为区块高度而不是 {data['brokenAt']!r}（B-017）"
        assert data["brokenAt"] == tampered["blockHeight"]
        assert data["brokenAtEvidenceId"] == admin_evidence
    finally:
        client.post("/api/v1/evidence/demo/restore", headers=admin,
                    json={"evidenceId": admin_evidence})


# ------------------------------------------- API-PERF-04 增量校验

def test_PERF04_已校验过的块不再重算SM3(tmp_path, monkeypatch):
    """第二次 status 不该再对老块算 SM3；新增一个块也只该算这个新块。"""
    from modules.evidence import chain as chain_mod

    engine, Factory = _new_chain_db(tmp_path, "perf.db")
    chain = chain_mod.LocalHashChain()
    with Factory() as db:
        for i in range(5):
            chain.write(db, category="data", ref_id=f"p-{i}", payload={"i": i, "u": str(tmp_path)})
        db.commit()

    calls = {"n": 0}
    真payload_hash = chain_mod.calc_payload_hash

    def counting(payload):
        calls["n"] += 1
        return 真payload_hash(payload)

    monkeypatch.setattr(chain_mod, "calc_payload_hash", counting)
    with Factory() as db:
        assert chain.status(db)["intact"] is True      # 冷启动：全量重算
        第一次 = calls["n"]
        assert 第一次 >= 5

        calls["n"] = 0
        assert chain.status(db)["intact"] is True      # 链没变：全部命中记忆
        assert calls["n"] == 0, f"第二次仍重算了 {calls['n']} 次 SM3"

        chain.write(db, category="data", ref_id="p-new", payload={"i": 99, "u": str(tmp_path)})
        db.commit()
        calls["n"] = 0
        assert chain.status(db)["intact"] is True
        assert calls["n"] <= 1, f"只加了一个块却重算了 {calls['n']} 次 SM3"
    engine.dispose()


def test_PERF04_增量校验后篡改照样被检出(tmp_path):
    """性能优化不能牺牲检出能力——这是答辩现场的核心桥段。"""
    from modules.evidence.chain import LocalHashChain

    engine, Factory = _new_chain_db(tmp_path, "detect.db")
    chain = LocalHashChain()
    with Factory() as db:
        for i in range(5):
            chain.write(db, category="data", ref_id=f"d-{i}", payload={"i": i, "u": str(tmp_path)})
        db.commit()
        assert chain.status(db)["intact"] is True      # 先把整条链喂进记忆

    # 绕过应用层直接改库（第 2 块）
    with Factory() as w:
        record = w.execute(select(ChainEvidence)
                           .where(ChainEvidence.block_height == 2)).scalar_one()
        record.payload_snapshot = {"i": 2, "pvOutput": 999.9}
        target = record.evidence_id
        w.commit()

    with Factory() as r:
        status = chain.status(r)
    assert status["intact"] is False, "走了缓存就抓不到篡改了"
    assert status["brokenAt"] == 2
    assert status["brokenAtEvidenceId"] == target

    # 改回去之后又该恢复完整
    with Factory() as w:
        w.execute(select(ChainEvidence)
                  .where(ChainEvidence.block_height == 2)).scalar_one().payload_snapshot = {
            "i": 2, "u": str(tmp_path)}
        w.commit()
    with Factory() as r:
        assert chain.status(r)["intact"] is True
    engine.dispose()


def test_PERF04_结果缓存只在指纹一致时命中():
    from modules.evidence.chain import _StatusResultCache

    cache = _StatusResultCache()
    assert cache.get((1, 2, 3, 4)) is None
    cache.put((1, 2, 3, 4), {"intact": True})
    assert cache.get((1, 2, 3, 4)) == {"intact": True}
    assert cache.get((1, 2, 3, 5)) is None, "指纹变了还命中缓存，篡改就漏了"
    cache.invalidate()
    assert cache.get((1, 2, 3, 4)) is None


# ------------------------------------------- B-010 并发写存证

def test_B010_死锁后自动重试最终写入成功(db, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from modules.evidence.chain import LocalHashChain

    chain = LocalHashChain()
    真写入 = LocalHashChain._write_once
    calls = {"n": 0}

    def flaky(self, db_, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OperationalError("INSERT", {},
                                   Exception(1213, "Deadlock found when trying to get lock"))
        return 真写入(self, db_, **kwargs)

    monkeypatch.setattr(LocalHashChain, "_write_once", flaky)
    monkeypatch.setattr("modules.evidence.chain.time.sleep", lambda *_: None)

    result = chain.write(db, category="data", ref_id="b010-retry", payload={"v": 1})
    db.commit()
    assert calls["n"] == 3, "死锁没有被重试（B-010）"
    assert result["evidenceId"].startswith("ev-")


def test_B010_重试用尽后告警且调用方可感知(client, login, db, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from modules.evidence import service as ev_service
    from modules.evidence.chain import LocalHashChain

    def always_deadlock(self, db_, **kwargs):
        raise OperationalError("INSERT", {}, Exception(1213, "Deadlock found"))

    monkeypatch.setattr(LocalHashChain, "_write_once", always_deadlock)
    monkeypatch.setattr("modules.evidence.chain.time.sleep", lambda *_: None)

    trace = "tr-20260822-a0000009"
    from core.middleware import current_trace_id

    token = current_trace_id.set(trace)
    try:
        result = ev_service.write_evidence(db, category="data", ref_id="b010-lost",
                                           payload={"v": 1})
    finally:
        current_trace_id.reset(token)

    # 调用方能感知：不再是一个静悄悄的 None
    assert result["evidenceId"] is None
    assert "Deadlock" in result["chainError"]

    monkeypatch.undo()
    data = client.get(f"/api/v1/audit/logs?traceId={trace}",
                      headers=login("admin")).json()["data"]
    assert data["total"] >= 1, "存证被丢弃却没有任何告警（B-010）"
    item = data["items"][0]
    assert item["result"] == "failed" and item["riskLevel"] == "critical"
    assert item["evidenceId"] is None       # 链都写不动了，这条日志不能再去上链


def test_B010_多线程并发写存证不丢块不重号(tmp_path):
    """B-010 的正面回归：并发上链必须条条落库、高度连续、链完整。"""
    from modules.evidence.chain import LocalHashChain

    engine, Factory = _new_chain_db(tmp_path, "concurrent.db")
    chain = LocalHashChain()

    线程数, 每线程 = 4, 3
    errors: list[Exception] = []

    def worker(n: int) -> None:
        for i in range(每线程):
            try:
                with Factory() as session:
                    chain.write(session, category="data", ref_id=f"c-{n}-{i}",
                                payload={"thread": n, "seq": i})
                    session.commit()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(线程数)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"并发写存证失败 {len(errors)} 次：{errors[:3]}"
    with Factory() as session:
        heights = sorted(session.execute(select(ChainEvidence.block_height)).scalars().all())
        assert heights == list(range(线程数 * 每线程)), "并发下高度重号或丢块"
        assert chain.status(session)["intact"] is True
    engine.dispose()


def test_B010_非锁冲突异常不重试直接抛(db, monkeypatch):
    """只重试死锁/锁超时，别把参数错误之类的异常也重试三遍。"""
    from modules.evidence.chain import LocalHashChain

    chain = LocalHashChain()
    calls = {"n": 0}

    def boom(self, db_, **kwargs):
        calls["n"] += 1
        raise ValueError("payload 非法")

    monkeypatch.setattr(LocalHashChain, "_write_once", boom)
    with pytest.raises(ValueError):
        chain.write(db, category="data", ref_id="x", payload={})
    assert calls["n"] == 1


def test_B010_可重试判定只认锁冲突():
    from sqlalchemy.exc import OperationalError

    from modules.evidence.chain import _is_retryable

    assert _is_retryable(OperationalError("s", {}, Exception(1213, "Deadlock found")))
    assert _is_retryable(OperationalError("s", {}, Exception(1205, "Lock wait timeout exceeded")))
    assert _is_retryable(OperationalError("s", {}, Exception("database is locked")))
    assert not _is_retryable(OperationalError("s", {}, Exception(1062, "Duplicate entry")))
    assert not _is_retryable(ValueError("别的错"))


def test_B010_并发写链的串行化锁存在():
    """回归护栏：进程内串行化是 B-010 的第一道防线，别被顺手删掉。"""
    from modules.evidence import chain as chain_mod

    assert isinstance(chain_mod._WRITE_LOCK, type(threading.Lock()))
    assert chain_mod.MAX_WRITE_RETRY >= 2


def test_迁移脚本与建表语句都包含备份表():
    import pathlib

    sql_dir = pathlib.Path(__file__).resolve().parents[1] / "sql"
    for name in ("01_schema.sql", "03_migrate_20260822.sql"):
        assert "chain_evidence_backup" in (sql_dir / name).read_text(encoding="utf-8"), \
            f"{name} 缺少 chain_evidence_backup 建表语句"
    assert text is not None
