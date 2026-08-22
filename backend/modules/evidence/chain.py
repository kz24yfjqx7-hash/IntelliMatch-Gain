"""可信存证链。

需求文档要求用 Hyperledger Fabric。答辩演示场景下 Fabric 的部署成本与离线安装包体积
都不可接受，因此这里定义抽象接口 `EvidenceChain`，默认实现 `LocalHashChain`（本地哈希链），
并保留 `FabricChain` 的空实现证明架构可插拔——换链只需改一行工厂函数，业务代码零改动。

LocalHashChain 保留了区块链在本场景下真正被考核的性质：
- **不可篡改可检测**：block_hash = SM3(prev_hash + payload_hash + timestamp)，
  改任何一条历史记录都会让它自己以及其后所有区块的哈希对不上
- **可追溯**：每个区块带 traceId 与业务对象引用
- **可验证**：任何时刻可以从创世块顺序重算整条链

它不具备的是分布式共识——单机演示场景下本来也只有一个节点，这一点在答辩时如实说明。
"""
import hashlib
import logging
import random
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from core.gm_crypto import block_hash as calc_block_hash
from core.gm_crypto import canonical_json
from core.gm_crypto import payload_hash as calc_payload_hash
from core.response import iso, now_cst
from modules.evidence.model import ChainEvidence

logger = logging.getLogger(__name__)

GENESIS_PREV_HASH = "sm3:" + "0" * 64

# ---------------------------------------------------------------- 并发写链（B-010）
# 尾块 `SELECT … FOR UPDATE` 的行锁 + uk_height 唯一索引，在多线程并发上链时
# 会互相死锁（联调日志里 132 次 1213 Deadlock、2 次 1205 Lock wait timeout），
# 而失败只被 write_evidence 吞掉 ⇒ 存证被静默丢弃，高危审计日志没有 evidence_id。
#
# 两道防线：
# 1. 进程内串行化：同一进程里同一时刻只有一个**业务事务**处于「取尾块 → 插入新块 → 提交」之间，
#    把并发退化成排队，从源头消掉绝大部分锁竞争（uvicorn 单进程多线程正是这个场景）；
# 2. 死锁 / 锁超时重试：跨进程或跨连接仍可能撞上，捕获后回滚重试，带指数退避 + 抖动。
#
# 第三道防线（同样来自实测）：`_write_once` 只 flush 不 commit，链尾的 FOR UPDATE 行锁
# 要等调用方事务提交才释放。若此时**同一个请求**用另一条连接再来写链（审计日志上链原本
# 就是另开 SessionLocal），就是自己等自己 —— 实测一次「回收授权」被拖到 200 秒
# （innodb_lock_wait_timeout 默认 50 秒 × 重试 3 次）。两处一起治：
#   · modules/audit/service.py 写审计存证时复用当前请求的会话（core.database.current_db_session）；
#   · 这里把会话级 innodb_lock_wait_timeout 压到 3 秒，真撞上也是几秒内失败重试，而不是分钟级挂起。
_WRITE_LOCK = threading.Lock()
_INNODB_LOCK_WAIT_TIMEOUT = 3
_RETRY_SQLSTATES = ("40001", "HY000")
# MySQL 1213=Deadlock found、1205=Lock wait timeout；SQLite 5/6=database is locked/busy
_RETRY_KEYWORDS = ("deadlock", "lock wait timeout", "database is locked",
                   "database table is locked", "database is busy")
MAX_WRITE_RETRY = 3


def _is_retryable(exc: BaseException) -> bool:
    """判断一次落库失败是不是「重试就能过去」的锁冲突。"""
    if not isinstance(exc, (OperationalError, DBAPIError)):
        return False
    orig = getattr(exc, "orig", None)
    code = None
    args = getattr(orig, "args", ())
    if args and isinstance(args[0], int):
        code = args[0]
    if code in (1213, 1205):
        return True
    return any(k in str(exc).lower() for k in _RETRY_KEYWORDS)


# -------------------------------------------------- 链校验的增量化（API-PERF-04）
# 原来每次 GET /evidence/chain/status 都要把全表拉出来重算 2×N 次 SM3。
# SM3 是纯 Python 实现（见 core/gm_crypto.py 的取舍说明），600 条时 P95 已经 2.4s，
# 且随链长线性增长，1700+ 条时体验不可接受。
#
# 两级优化，**都不降低检出能力**：
#
# 1. 结果级：先用一条纯 SQL 聚合算出全表指纹（行数 + 最大高度 + 逐行 CRC32 的
#    普通和与按高度加权和）。指纹由数据库端计算，覆盖 payload_snapshot 与三个哈希列，
#    只要有任何一行被改动（篡改演示正是直接改库）指纹必变，缓存立即失效并回到全量校验。
#    加权和让「两行互换」也能被发现。指纹只做「要不要重算」的判断，
#    真正判定完整性的仍然是下面的 SM3 全量重算。
# 2. 逐块级：记住「高度 H 的这组 (payload 摘要, payload_hash, prev_hash, block_hash, ts)
#    已经通过 SM3 校验」。重算时只要这五元组完全一致就跳过两次 SM3。
#    payload 摘要用 SHA-256（C 实现，微秒级），只作为「这一行有没有变过」的记忆键，
#    内容一变摘要必变 ⇒ 篡改的那一行必然 memo miss，必然重新走完整 SM3 校验。
#
# 净效果：新增一个块 → 只对新块做 SM3；链未变 → 一条聚合 SQL 直接返回；
# 任何一行被改 → 指纹变 + 该行 memo miss → 照旧被 status 精确定位到断裂高度。
# 另外单条 /evidence/verify 永远不走任何缓存，答辩现场的「单条标红」是硬校验。
_VERIFIED_BLOCKS: dict[int, tuple] = {}
_VERIFIED_MAX = 500_000

_FINGERPRINT_SQL = text(
    "SELECT COUNT(*), COALESCE(MAX(block_height), -1),"
    " COALESCE(SUM(CRC32(CONCAT_WS('|', payload_hash, prev_hash, block_hash,"
    " created_at, payload_snapshot))), 0),"
    " COALESCE(SUM(CRC32(CONCAT_WS('|', payload_hash, prev_hash, block_hash,"
    " created_at, payload_snapshot)) * (block_height + 1)), 0)"
    " FROM chain_evidence"
)


def _table_fingerprint(db: Session) -> tuple | None:
    """全表指纹。只有 MySQL/MariaDB 支持，其它方言返回 None ⇒ 永远走全量校验。"""
    try:
        if db.bind is None or db.bind.dialect.name != "mysql":
            return None
        row = db.execute(_FINGERPRINT_SQL).first()
    except Exception as exc:  # noqa: BLE001  指纹只是加速手段，算不出来就老老实实全量重算
        logger.debug("计算链指纹失败，回退全量校验：%s", exc)
        return None
    return tuple(row) if row is not None else None


def _payload_digest(payload) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _block_intact(rec: ChainEvidence, expected_prev_hash: str) -> bool:
    """单块三重校验：链接指向 → payload 摘要 → 区块哈希。命中记忆时跳过 SM3。"""
    if rec.prev_hash != expected_prev_hash:
        return False
    ts = iso(rec.created_at)
    memo_key = (_payload_digest(rec.payload_snapshot), rec.payload_hash,
                rec.prev_hash, rec.block_hash, ts)
    if _VERIFIED_BLOCKS.get(rec.block_height) == memo_key:
        return True
    if calc_payload_hash(rec.payload_snapshot) != rec.payload_hash:
        return False
    if calc_block_hash(rec.prev_hash, rec.payload_hash, ts) != rec.block_hash:
        return False
    if len(_VERIFIED_BLOCKS) < _VERIFIED_MAX:
        _VERIFIED_BLOCKS[rec.block_height] = memo_key
    return True


class _StatusResultCache:
    """只缓存「指纹 → 上一次全量校验结论」这一对，不缓存任何未经校验的结论。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fingerprint: tuple | None = None
        self._result: dict | None = None

    def get(self, fingerprint: tuple) -> dict | None:
        with self._lock:
            if self._result is not None and fingerprint == self._fingerprint:
                return self._result
        return None

    def put(self, fingerprint: tuple, result: dict) -> None:
        with self._lock:
            self._fingerprint, self._result = fingerprint, dict(result)

    def invalidate(self) -> None:
        with self._lock:
            self._fingerprint, self._result = None, None


_STATUS_CACHE = _StatusResultCache()


def invalidate_status_cache() -> None:
    """篡改 / 还原演示后主动作废缓存。指纹本来就会变，这里是双保险。"""
    _STATUS_CACHE.invalidate()


class EvidenceChain(ABC):
    """存证链抽象接口。换成 Fabric / 长安链只需实现这四个方法。"""

    @abstractmethod
    def write(self, db: Session, *, category: str, ref_id: str, payload: dict,
              actor_did: str | None, trace_id: str | None) -> dict:
        """写入一条存证，返回 {evidenceId, hash, blockHeight, txId, prevHash, timestamp}。"""

    @abstractmethod
    def verify(self, db: Session, evidence_id: str, payload: dict | None = None) -> dict:
        """校验单条存证的完整性。"""

    @abstractmethod
    def status(self, db: Session) -> dict:
        """从创世块顺序校验整条链，返回链状态与第一个断裂点。"""

    @abstractmethod
    def trace(self, db: Session, trace_id: str) -> list[dict]:
        """按 traceId 取出一次业务操作产生的全部存证。"""


class LocalHashChain(EvidenceChain):
    """本地哈希链实现。"""

    def write(self, db: Session, *, category: str, ref_id: str, payload: dict,
              actor_did: str | None = None, trace_id: str | None = None) -> dict:
        """写一个新块。并发下的锁冲突会自动重试（B-010），重试用尽才抛出。"""
        last_exc: BaseException | None = None
        for attempt in range(1, MAX_WRITE_RETRY + 1):
            try:
                with _WRITE_LOCK:
                    return self._write_once(db, category=category, ref_id=ref_id,
                                            payload=payload, actor_did=actor_did,
                                            trace_id=trace_id)
            except Exception as exc:  # noqa: BLE001
                if not _is_retryable(exc) or attempt == MAX_WRITE_RETRY:
                    raise
                last_exc = exc
                # 冲突方必须先回滚——MySQL 死锁时被选中的事务已经整个回滚了，
                # 不 rollback 的话这条会话后面每一条 SQL 都会报 "transaction has been rolled back"
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                backoff = 0.05 * (2 ** (attempt - 1)) + random.uniform(0, 0.05)
                logger.warning("存证上链锁冲突，第 %s/%s 次重试（%.0fms 后）：%s",
                               attempt, MAX_WRITE_RETRY, backoff * 1000, exc)
                time.sleep(backoff)
        raise last_exc  # pragma: no cover  循环里一定会 return 或 raise

    def _write_once(self, db: Session, *, category: str, ref_id: str, payload: dict,
                    actor_did: str | None, trace_id: str | None) -> dict:
        # 取链尾。MySQL 下加 FOR UPDATE 行锁，避免并发写入导致高度重复；
        # SQLite 会忽略 FOR UPDATE，单测无并发也不受影响。
        stmt = select(ChainEvidence).order_by(ChainEvidence.block_height.desc()).limit(1)
        if db.bind and db.bind.dialect.name == "mysql":
            # 万一还是撞上行锁（跨进程 / 应用锁超时降级），5 秒失败去重试，别干等 50 秒默认值
            try:
                db.execute(text(f"SET SESSION innodb_lock_wait_timeout={_INNODB_LOCK_WAIT_TIMEOUT}"))
            except Exception as exc:  # noqa: BLE001  设置失败不影响正确性
                logger.debug("设置 innodb_lock_wait_timeout 失败：%s", exc)
            stmt = stmt.with_for_update()
        tail = db.execute(stmt).scalar_one_or_none()

        height = 0 if tail is None else tail.block_height + 1
        prev_hash = GENESIS_PREV_HASH if tail is None else tail.block_hash

        created_at = now_cst()
        ts = iso(created_at)
        ph = calc_payload_hash(payload)
        bh = calc_block_hash(prev_hash, ph, ts)
        evidence_id = f"ev-{height:06d}"

        record = ChainEvidence(
            evidence_id=evidence_id, category=category, ref_id=str(ref_id),
            actor_did=actor_did, payload_hash=ph, prev_hash=prev_hash, block_hash=bh,
            block_height=height, tx_id=f"blk-{height:06d}-0", trace_id=trace_id,
            payload_snapshot=payload, created_at=created_at.replace(tzinfo=None),
        )
        db.add(record)
        db.flush()

        logger.info("存证上链 %s height=%s category=%s ref=%s", evidence_id, height, category, ref_id)
        return {
            "evidenceId": evidence_id,
            "hash": ph,
            "blockHeight": height,
            "txId": record.tx_id,
            "prevHash": prev_hash,
            "blockHash": bh,
            "timestamp": ts,
        }

    def verify(self, db: Session, evidence_id: str, payload: dict | None = None) -> dict:
        record = db.execute(
            select(ChainEvidence).where(ChainEvidence.evidence_id == evidence_id)
        ).scalar_one_or_none()
        if record is None:
            from core.exceptions import NotFoundError

            raise NotFoundError(f"存证 {evidence_id} 不存在")

        # payload 省略时用库内快照重算——这正是篡改演示能被抓出来的原理
        source = payload if payload is not None else record.payload_snapshot
        local_hash = calc_payload_hash(source)
        intact = local_hash == record.payload_hash

        # 区块哈希本身也要校验，防止有人连 payload_hash 一起改
        recomputed_block = calc_block_hash(
            record.prev_hash, record.payload_hash, iso(record.created_at)
        )
        block_intact = recomputed_block == record.block_hash

        return {
            "evidenceId": evidence_id,
            "intact": intact and block_intact,
            "localHash": local_hash,
            "chainHash": record.payload_hash,
            "blockHeight": record.block_height,
            "tamperedAt": iso(record.created_at) if not intact else None,
            "message": (
                "数据完整，与链上摘要一致" if intact and block_intact
                else "本地数据与链上摘要不一致，数据已被篡改" if not intact
                else "区块哈希与链结构不一致，该区块已被篡改"
            ),
        }

    def status(self, db: Session) -> dict:
        """链状态。先看指纹能不能免掉全量重算，不能就走带记忆的全量校验。

        性能取舍见本文件顶部 _STATUS_CACHE 的说明——**任何一次篡改都仍然要被检出**。
        """
        fingerprint = _table_fingerprint(db)
        if fingerprint is not None:
            cached = _STATUS_CACHE.get(fingerprint)
            if cached is not None:
                return dict(cached)

        result = self._status_full(db)
        if fingerprint is not None:
            _STATUS_CACHE.put(fingerprint, result)
        return dict(result)

    def _status_full(self, db: Session) -> dict:
        """顺序遍历整条链。分批读取，避免树莓派上一次性把全表拉进内存。"""
        total = db.execute(select(func.count()).select_from(ChainEvidence)).scalar_one()
        by_category = dict(
            db.execute(
                select(ChainEvidence.category, func.count()).group_by(ChainEvidence.category)
            ).all()
        )

        broken_at = None
        broken_at_id = None
        prev_hash = GENESIS_PREV_HASH
        last_hash = None
        height = -1

        BATCH = 500
        offset = 0
        while True:
            batch = db.execute(
                select(ChainEvidence).order_by(ChainEvidence.block_height).offset(offset).limit(BATCH)
            ).scalars().all()
            if not batch:
                break
            for rec in batch:
                if broken_at is None and not _block_intact(rec, prev_hash):
                    broken_at = rec.block_height
                    broken_at_id = rec.evidence_id
                prev_hash = rec.block_hash
                last_hash = rec.block_hash
                height = rec.block_height
            offset += BATCH

        return {
            "height": height + 1,
            "lastHash": last_hash,
            "intact": broken_at is None,
            # 契约 2.6：brokenAt 与 height 同名词义，是**区块高度**或 null（B-017）。
            # evidenceId 另放 brokenAtEvidenceId，前端两个都能用。
            "brokenAt": broken_at,
            "brokenAtEvidenceId": broken_at_id,
            "totalRecords": total,
            "byCategory": by_category,
            "chainType": "LocalHashChain",
            "verifiedAt": iso(now_cst()),
        }

    def trace(self, db: Session, trace_id: str) -> list[dict]:
        records = db.execute(
            select(ChainEvidence)
            .where(ChainEvidence.trace_id == trace_id)
            .order_by(ChainEvidence.block_height)
        ).scalars().all()
        return [
            {
                "evidenceId": r.evidence_id, "category": r.category, "refId": r.ref_id,
                "actorDid": r.actor_did, "hash": r.payload_hash, "blockHeight": r.block_height,
                "txId": r.tx_id, "at": iso(r.created_at),
            }
            for r in records
        ]


class FabricChain(EvidenceChain):
    """Hyperledger Fabric 实现的占位类。

    保留它是为了证明存证层是可插拔的：真上 Fabric 时只要实现这四个方法，
    并把 get_chain() 的返回值换掉，上层业务代码一行都不用改。
    """

    def __init__(self, channel: str = "energych", chaincode: str = "evidence"):
        self.channel = channel
        self.chaincode = chaincode

    def write(self, db, **kwargs):
        raise NotImplementedError("FabricChain 尚未接入，当前使用 LocalHashChain")

    def verify(self, db, evidence_id, payload=None):
        raise NotImplementedError("FabricChain 尚未接入，当前使用 LocalHashChain")

    def status(self, db):
        raise NotImplementedError("FabricChain 尚未接入，当前使用 LocalHashChain")

    def trace(self, db, trace_id):
        raise NotImplementedError("FabricChain 尚未接入，当前使用 LocalHashChain")


_chain: EvidenceChain = LocalHashChain()


def get_chain() -> EvidenceChain:
    """存证链工厂。换链只改这里。"""
    return _chain
