/**
 * 身份中心内共享的「刚生成的私钥」状态：
 * 注册 DID / 密钥轮换 / 生成密钥后把私钥放在内存里（仅本次会话），供验签演示模拟签名使用。
 * 私钥不落本地存储。
 */
import { reactive } from 'vue'
import { sha256Hex } from '@/utils/sha256'

export const lastKey = reactive({ did: '', privateKey: '', publicKey: '', source: '' })

export function setLastKey({ did, privateKey, publicKey, source }) {
  lastKey.did = did || ''
  lastKey.privateKey = privateKey || ''
  lastKey.publicKey = publicKey || ''
  lastKey.source = source || ''
}

/** 演示用签名：sig:SHA256(privateKey + message)（真实环境为 SM2 签名，由设备端完成） */
export function demoSign(privateKey, message) {
  return 'sig:' + sha256Hex(`${privateKey}|${message || ''}`)
}
