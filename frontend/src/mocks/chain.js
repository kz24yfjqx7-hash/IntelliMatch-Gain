/**
 * 本地哈希链（mock 版）。
 * 真实后端用 SM3；mock 用 SHA-256 代替，但 hash 字段统一格式化为 `sm3:<hex>` 以贴合契约展示。
 *   payload_hash = H(canonical(payload))
 *   block_hash   = H(prev_hash + payload_hash + created_at)
 * tamper() 真的改写 payload_snapshot；verify()/status() 重新计算并能定位断裂点。
 */
import { sha256Hex, sha256Json } from '../utils/sha256.js'

const GENESIS_HASH = 'sm3:' + '0'.repeat(64)

export function hashOf(str) {
  return 'sm3:' + sha256Hex(str)
}
export function hashJson(obj) {
  return 'sm3:' + sha256Json(obj)
}

export class LocalHashChain {
  constructor() {
    this.blocks = []
    this.byId = new Map()
    this.tampered = new Map() // evidenceId -> tamperedAt
  }

  get height() {
    return this.blocks.length
  }

  get last() {
    return this.blocks[this.blocks.length - 1] || null
  }

  /**
   * 追加一条存证
   * @returns 存证记录（字段照 DB-SCHEMA chain_evidence）
   */
  append({ category, refId, payload = {}, actorDid = null, traceId = null, createdAt }) {
    const height = this.blocks.length + 1
    const prevHash = this.last ? this.last.block_hash : GENESIS_HASH
    const payloadHash = hashJson(payload)
    const ts = createdAt
    const blockHash = hashOf(prevHash + payloadHash + ts)
    const rec = {
      id: height,
      evidence_id: `ev-${String(height).padStart(6, '0')}`,
      category,
      ref_id: String(refId),
      actor_did: actorDid,
      payload_hash: payloadHash,
      prev_hash: prevHash,
      block_hash: blockHash,
      block_height: height,
      tx_id: `blk-${String(height).padStart(6, '0')}-0`,
      trace_id: traceId,
      payload_snapshot: JSON.parse(JSON.stringify(payload)),
      created_at: ts
    }
    this.blocks.push(rec)
    this.byId.set(rec.evidence_id, rec)
    return rec
  }

  get(evidenceId) {
    return this.byId.get(evidenceId) || null
  }

  /** 演示：篡改 payload_snapshot（链上 payload_hash 保持不变） */
  tamper(evidenceId, newValue, at) {
    const rec = this.get(evidenceId)
    if (!rec) return null
    if (newValue && typeof newValue === 'object' && !Array.isArray(newValue)) {
      rec.payload_snapshot = { ...rec.payload_snapshot, ...newValue }
    } else {
      rec.payload_snapshot = { value: newValue }
    }
    this.tampered.set(evidenceId, at)
    return rec
  }

  /** 完整性校验：重算本地数据摘要并与链上摘要比对 */
  verify(evidenceId, payload) {
    const rec = this.get(evidenceId)
    if (!rec) return null
    const localHash = hashJson(payload !== undefined ? payload : rec.payload_snapshot)
    const intact = localHash === rec.payload_hash
    return {
      intact,
      localHash,
      chainHash: rec.payload_hash,
      blockHeight: rec.block_height,
      tamperedAt: intact ? null : (this.tampered.get(evidenceId) || null),
      message: intact ? '本地数据与链上摘要一致，数据完整' : '本地数据与链上摘要不一致，数据已被篡改'
    }
  }

  /** 遍历整条链：校验 payload 摘要、前驱链接与块哈希，返回首个断裂高度 */
  status() {
    let brokenAt = null
    let prev = GENESIS_HASH
    for (const b of this.blocks) {
      const payloadOk = hashJson(b.payload_snapshot) === b.payload_hash
      const linkOk = b.prev_hash === prev
      const blockOk = hashOf(b.prev_hash + b.payload_hash + b.created_at) === b.block_hash
      if (!(payloadOk && linkOk && blockOk)) {
        brokenAt = b.block_height
        break
      }
      prev = b.block_hash
    }
    const byCategory = { data: 0, identity: 0, permission: 0, audit: 0, algo: 0 }
    for (const b of this.blocks) byCategory[b.category] = (byCategory[b.category] || 0) + 1
    return {
      height: this.height,
      lastHash: this.last ? this.last.block_hash : GENESIS_HASH,
      intact: brokenAt === null,
      brokenAt,
      totalRecords: this.height,
      byCategory
    }
  }
}

/** 把内部记录转为契约返回格式 */
export function toEvidenceDto(rec) {
  if (!rec) return null
  return {
    evidenceId: rec.evidence_id,
    category: rec.category,
    refId: rec.ref_id,
    actorDid: rec.actor_did,
    hash: rec.payload_hash,
    payloadHash: rec.payload_hash,
    prevHash: rec.prev_hash,
    blockHash: rec.block_hash,
    blockHeight: rec.block_height,
    txId: rec.tx_id,
    traceId: rec.trace_id,
    timestamp: rec.created_at,
    createdAt: rec.created_at,
    payload: rec.payload_snapshot
  }
}
