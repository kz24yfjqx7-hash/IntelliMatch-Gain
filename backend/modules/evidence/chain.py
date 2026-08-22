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
import logging
from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.gm_crypto import block_hash as calc_block_hash
from core.gm_crypto import payload_hash as calc_payload_hash
from core.response import iso, now_cst
from modules.evidence.model import ChainEvidence

logger = logging.getLogger(__name__)

GENESIS_PREV_HASH = "sm3:" + "0" * 64


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
        # 取链尾。MySQL 下加 FOR UPDATE 行锁，避免并发写入导致高度重复；
        # SQLite 会忽略 FOR UPDATE，单测无并发也不受影响。
        stmt = select(ChainEvidence).order_by(ChainEvidence.block_height.desc()).limit(1)
        if db.bind and db.bind.dialect.name == "mysql":
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
        """顺序遍历整条链。分批读取，避免树莓派上一次性把全表拉进内存。"""
        total = db.execute(select(func.count()).select_from(ChainEvidence)).scalar_one()
        by_category = dict(
            db.execute(
                select(ChainEvidence.category, func.count()).group_by(ChainEvidence.category)
            ).all()
        )

        broken_at = None
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
                if broken_at is None:
                    if rec.prev_hash != prev_hash:
                        broken_at = rec.evidence_id
                    elif calc_payload_hash(rec.payload_snapshot) != rec.payload_hash:
                        broken_at = rec.evidence_id
                    elif calc_block_hash(rec.prev_hash, rec.payload_hash,
                                         iso(rec.created_at)) != rec.block_hash:
                        broken_at = rec.evidence_id
                prev_hash = rec.block_hash
                last_hash = rec.block_hash
                height = rec.block_height
            offset += BATCH

        return {
            "height": height + 1,
            "lastHash": last_hash,
            "intact": broken_at is None,
            "brokenAt": broken_at,
            "totalRecords": total,
            "byCategory": by_category,
            "chainType": "LocalHashChain",
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
