"""本地哈希链测试：写入、校验、篡改检测、断裂点定位。

第四步会补上存证的 HTTP 接口与 /evidence/demo/tamper 演示接口，
这里先把链本身的性质钉死——防篡改能力是答辩现场要现场演示的。
"""
from datetime import timedelta
from sqlalchemy import select

from modules.evidence.chain import GENESIS_PREV_HASH, LocalHashChain
from modules.evidence.model import ChainEvidence

chain = LocalHashChain()


def _write(db, n=1, category="data"):
    out = []
    for i in range(n):
        out.append(chain.write(db, category=category, ref_id=f"ref-{i}",
                               payload={"index": i, "value": f"数据-{i}"},
                               actor_did="did:vpp:user:0x01", trace_id="tr-20260818-aaaabbbb"))
        db.commit()
    return out


def test_创世块前哈希为全零(db):
    db.query(ChainEvidence).delete()
    db.commit()
    first = _write(db, 1)[0]
    assert first["blockHeight"] == 0
    assert first["prevHash"] == GENESIS_PREV_HASH
    assert first["evidenceId"] == "ev-000000"


def test_区块首尾相连(db):
    db.query(ChainEvidence).delete()
    db.commit()
    blocks = _write(db, 5)
    for prev, cur in zip(blocks, blocks[1:]):
        assert cur["prevHash"] == prev["blockHash"]
        assert cur["blockHeight"] == prev["blockHeight"] + 1


def test_未篡改时链完整(db):
    db.query(ChainEvidence).delete()
    db.commit()
    _write(db, 5)
    status = chain.status(db)
    assert status["intact"] is True
    assert status["brokenAt"] is None
    assert status["height"] == 5
    assert status["chainType"] == "LocalHashChain"


def test_单条存证校验通过(db):
    db.query(ChainEvidence).delete()
    db.commit()
    ev = _write(db, 1)[0]
    result = chain.verify(db, ev["evidenceId"])
    assert result["intact"] is True
    assert result["localHash"] == result["chainHash"]


def test_篡改快照会被抓出来(db):
    """这就是答辩现场 /evidence/demo/tamper 背后的原理。"""
    db.query(ChainEvidence).delete()
    db.commit()
    blocks = _write(db, 5)
    target = blocks[2]["evidenceId"]

    record = db.execute(
        select(ChainEvidence).where(ChainEvidence.evidence_id == target)
    ).scalar_one()
    record.payload_snapshot = {"index": 2, "value": "被偷偷改过的数据", "pvOutput": 999.9}
    db.commit()

    result = chain.verify(db, target)
    assert result["intact"] is False
    assert result["localHash"] != result["chainHash"]
    assert "篡改" in result["message"]

    status = chain.status(db)
    assert status["intact"] is False
    assert status["brokenAt"] == target      # 断裂点精确定位到被改的那一块


def test_改payload_hash也逃不掉(db):
    """连摘要一起改，区块哈希校验这一关还在。"""
    db.query(ChainEvidence).delete()
    db.commit()
    blocks = _write(db, 3)
    target = blocks[1]["evidenceId"]

    record = db.execute(
        select(ChainEvidence).where(ChainEvidence.evidence_id == target)
    ).scalar_one()
    record.payload_snapshot = {"index": 1, "value": "改了"}
    from core.gm_crypto import payload_hash

    record.payload_hash = payload_hash(record.payload_snapshot)   # 同步改摘要
    db.commit()

    assert chain.verify(db, target)["intact"] is False
    assert chain.status(db)["brokenAt"] == target


def test_按traceid取出完整链路(db):
    db.query(ChainEvidence).delete()
    db.commit()
    _write(db, 3)
    steps = chain.trace(db, "tr-20260818-aaaabbbb")
    assert len(steps) == 3
    assert [s["blockHeight"] for s in steps] == [0, 1, 2]


def test_分类统计(db):
    db.query(ChainEvidence).delete()
    db.commit()
    _write(db, 2, category="data")
    _write(db, 3, category="identity")
    status = chain.status(db)
    assert status["byCategory"] == {"data": 2, "identity": 3}
    assert status["totalRecords"] == 5


def test_fabric实现是可插拔占位(db):
    import pytest

    from modules.evidence.chain import FabricChain

    fabric = FabricChain()
    with pytest.raises(NotImplementedError):
        fabric.status(db)


# ---------------------------------------------------------------- 时间戳精度
# 下面两条挡的是一个只在真 MySQL 上现形的 bug：
# DATETIME 列没有小数秒位，MySQL 写入时对小数秒做四舍五入而不是截断，
# 于是「写入时算哈希用的时间戳」和「读回来重算用的时间戳」会差 1 秒，
# 链校验随机一半的概率失败。SQLite 原样保存微秒，所以单测本来完全看不见。

def test_时间戳一律秒级精度():
    """微秒必须在 now_cst 就抹掉，落库前的任何环节都不该再带小数秒。"""
    from core.response import now_cst

    for _ in range(50):
        assert now_cst().microsecond == 0, "now_cst 漏出了微秒，MySQL 落库会四舍五入进位"


def test_存证块的时间戳经得起DATETIME取整(db):
    """模拟 MySQL 的行为：把 created_at 按秒四舍五入，链必须依然完整。"""
    from modules.evidence.chain import LocalHashChain

    chain = LocalHashChain()
    for i in range(3):
        chain.write(db, category="data", ref_id=f"ts-{i}", payload={"v": i})
    db.commit()

    # MySQL DATETIME(0) 的等效操作：有小数秒就四舍五入
    from modules.evidence.model import ChainEvidence
    for rec in db.query(ChainEvidence).all():
        t = rec.created_at
        if t.microsecond:
            rec.created_at = t.replace(microsecond=0) + timedelta(seconds=1 if t.microsecond >= 500_000 else 0)
    db.commit()

    结果 = chain.status(db)
    assert 结果["intact"], f"取整之后链断了，断在 {结果['brokenAt']}"
