/**
 * 时间/数字格式化工具。
 * 契约 §1.5：所有时间为 ISO 8601 带时区字符串，例如 2026-08-17T14:23:05+08:00。
 */

const TZ_OFFSET_MIN = 8 * 60 // 平台统一使用东八区

function pad(n, len = 2) {
  return String(n).padStart(len, '0')
}

/**
 * 把 Date 转成 `YYYY-MM-DDTHH:mm:ss+08:00`
 * @param {Date|number|string} input
 * @param {boolean} withMs 是否带毫秒
 */
export function toIso8(input = new Date(), withMs = false) {
  const d = input instanceof Date ? input : new Date(input)
  const t = new Date(d.getTime() + TZ_OFFSET_MIN * 60 * 1000)
  const base = `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}` +
    `T${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}:${pad(t.getUTCSeconds())}`
  return (withMs ? `${base}.${pad(t.getUTCMilliseconds(), 3)}` : base) + '+08:00'
}

/** 当前时间 ISO 字符串（东八区） */
export function nowIso() {
  return toIso8(new Date())
}

/** 显示用：`MM-DD HH:mm:ss`；无效输入返回 '--' */
export function fmtTime(value, pattern = 'MM-DD HH:mm:ss') {
  if (!value) return '--'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  const map = {
    YYYY: d.getFullYear(),
    MM: pad(d.getMonth() + 1),
    DD: pad(d.getDate()),
    HH: pad(d.getHours()),
    mm: pad(d.getMinutes()),
    ss: pad(d.getSeconds())
  }
  return pattern.replace(/YYYY|MM|DD|HH|mm|ss/g, k => map[k])
}

/** 完整日期时间 `YYYY-MM-DD HH:mm:ss` */
export function fmtDateTime(value) {
  return fmtTime(value, 'YYYY-MM-DD HH:mm:ss')
}

/** 仅日期 `YYYY-MM-DD` */
export function fmtDate(value) {
  return fmtTime(value, 'YYYY-MM-DD')
}

/** 数字保留小数位，非法返回 '--' */
export function fmtNumber(value, digits = 1) {
  const n = Number(value)
  if (value === null || value === undefined || Number.isNaN(n)) return '--'
  return n.toFixed(digits)
}

/** 百分比显示 */
export function fmtPercent(value, digits = 1) {
  const n = Number(value)
  if (Number.isNaN(n)) return '--'
  return `${n.toFixed(digits)}%`
}

/** 千分位 */
export function fmtThousands(value) {
  const n = Number(value)
  if (Number.isNaN(n)) return '--'
  return n.toLocaleString('zh-CN')
}

/** 哈希缩略：sm3:9a8b7c...d7e8 */
export function shortHash(hash, head = 10, tail = 6) {
  if (!hash || typeof hash !== 'string') return '--'
  if (hash.length <= head + tail + 3) return hash
  return `${hash.slice(0, head)}...${hash.slice(-tail)}`
}

/** DID 缩略 */
export function shortDid(did) {
  return shortHash(did, 22, 6)
}

/** 毫秒转可读时长 */
export function fmtDuration(ms) {
  const n = Number(ms)
  if (Number.isNaN(n)) return '--'
  if (n < 1000) return `${n}ms`
  if (n < 60000) return `${(n / 1000).toFixed(1)}s`
  return `${Math.floor(n / 60000)}m${Math.round((n % 60000) / 1000)}s`
}

/** 风险等级中文 */
export const RISK_LABELS = { low: '低', medium: '中', high: '高', critical: '严重' }
/** 敏感等级中文 */
export const LEVEL_LABELS = { L1: 'L1 公开', L2: 'L2 内部', L3: 'L3 敏感', L4: 'L4 核心' }
/** 角色中文 */
export const ROLE_LABELS = {
  sys_admin: '系统管理员',
  grid_dispatcher: '电网调度员',
  vpp_operator: '虚拟电厂运营商',
  energy_subject: '能源主体',
  regulator: '监管方',
  edge_node: '边缘节点'
}
/** 数据类型中文 */
export const DATA_TYPE_LABELS = { pv: '光伏', wind: '风电', storage: '储能', load: '负荷', dispatch: '调度' }
