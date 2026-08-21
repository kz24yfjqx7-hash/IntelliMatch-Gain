/** 安全审计中心（契约 §2.7） */
import { http, HttpResponse } from 'msw'
import { db, chain } from '../db.js'
import { BASE, handle, ok, query, paginate, requireAuth, requirePerm, writeAudit, raiseAlert, MockError, now, inRange } from '../helpers.js'
import { toIso8 } from '../../utils/format.js'

const RULES = {
  R01_UNAUTHORIZED: '越权访问', R02_ABNORMAL_DID: '异常 DID 登录', R03_PERM_CHURN: '高频权限变更', R04_BULK_EXPORT: '批量数据导出', R05_SUSPICIOUS_GRAD: '可疑梯度上传'
}

function dayOf(ts) { return String(ts).slice(0, 10) }
function todayStr() { return toIso8(new Date()).slice(0, 10) }

function filterLogs(q) {
  let list = db.auditLogs.slice()
  if (q.traceId) list = list.filter(l => l.traceId === q.traceId)
  if (q.actorDid) list = list.filter(l => l.actorDid === q.actorDid)
  if (q.action) list = list.filter(l => l.action === q.action || l.action.startsWith(q.action))
  if (q.module) list = list.filter(l => l.module === q.module)
  if (q.result) list = list.filter(l => l.result === q.result)
  if (q.riskLevel) list = list.filter(l => l.riskLevel === q.riskLevel)
  if (q.from || q.to) list = list.filter(l => inRange(l.at, q.from, q.to))
  if (q.keyword) list = list.filter(l => (l.detail || '').includes(q.keyword) || (l.actorName || '').includes(q.keyword) || l.action.includes(q.keyword) || (l.resourceId || '').includes(q.keyword) || l.traceId.includes(q.keyword))
  return list.sort((a, b) => (a.at < b.at ? 1 : -1))
}

/** 审计报告的规则化叙述（mock 中 narrativeSource 固定 cache） */
function buildNarrative(period, date, stats) {
  const label = { day: '今日', week: '本周', month: '本月' }[period] || '本期'
  const risk = stats.riskEvents.length
    ? stats.riskEvents.map(r => `${RULES[r.ruleCode] || r.ruleCode} ${r.count} 次`).join('、')
    : '未触发风控规则'
  return `${label}（${date}）平台共记录 ${stats.totalLogs} 条审计日志，其中高风险及以上 ${stats.highRisk} 条，拒绝/失败操作 ${stats.denied} 条。` +
    `身份侧完成 DID 签发 ${stats.identityOps.register} 次、冻结 ${stats.identityOps.freeze} 次、注销 ${stats.identityOps.revoke} 次、密钥轮换 ${stats.identityOps.rotate} 次；` +
    `权限侧新增申请 ${stats.permissionOps.applied} 件、审批通过 ${stats.permissionOps.approved} 件、驳回 ${stats.permissionOps.rejected} 件、回收 ${stats.permissionOps.revoked} 件。` +
    `链上累计存证 ${stats.evidence.total} 条，本期新增 ${stats.evidence.periodNew} 条，链完整性校验${stats.chainIntact ? '通过' : `失败（断裂于高度 ${stats.brokenAt}）`}。` +
    `风险事件：${risk}。` +
    (stats.highRisk > 0 ? '建议对越权主体进行权限复核，并关注高风险操作的 traceId 全链路回溯。' : '整体运行平稳，未发现需人工介入的安全事件。')
}

function periodRange(period, date) {
  const base = new Date(`${date}T00:00:00+08:00`)
  if (period === 'week') { const s = new Date(base.getTime() - base.getDay() * 86400000); return [toIso8(s), toIso8(new Date(s.getTime() + 7 * 86400000 - 1))] }
  if (period === 'month') { const s = new Date(`${date.slice(0, 7)}-01T00:00:00+08:00`); const e = new Date(s.getFullYear(), s.getMonth() + 1, 0, 23, 59, 59); return [toIso8(s), toIso8(e)] }
  return [toIso8(base), toIso8(new Date(base.getTime() + 86400000 - 1))]
}

export const auditHandlers = [
  http.get(`${BASE}/audit/logs/export`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    // 导出属于 export 动作：按 DB-SCHEMA 矩阵只有 sys_admin / grid_dispatcher / regulator 具备
    requirePerm(user, 'asset:export', { request, traceId, resourceType: 'audit', detail: '无导出权限的批量导出尝试' })
    const q = query(request)
    const list = filterLogs(q)
    const header = ['id', 'traceId', 'at', 'actorName', 'actorDid', 'module', 'action', 'resourceType', 'resourceId', 'result', 'riskLevel', 'detail', 'ip', 'evidenceId', 'hash']
    const esc = v => `"${String(v ?? '').replace(/"/g, '""')}"`
    const csv = '﻿' + header.join(',') + '\n' + list.map(l => header.map(h => esc(l[h])).join(',')).join('\n')
    writeAudit({ traceId, user, module: 'audit', action: 'audit:export', resourceType: 'audit', riskLevel: list.length >= 1000 ? 'high' : 'medium', detail: `导出审计日志 ${list.length} 条` })
    // R04：单次导出 ≥1000 条或 10 分钟内导出 ≥3 次
    const recent = db.auditLogs.filter(l => l.actorDid === user.did && l.action === 'audit:export' && Date.now() - new Date(l.at).getTime() < 10 * 60 * 1000).length
    if (list.length >= 1000 || recent >= 3) raiseAlert({ ruleCode: 'R04_BULK_EXPORT', riskLevel: 'medium', message: `${user.realName} ${list.length >= 1000 ? `单次导出 ${list.length} 条` : `10 分钟内导出 ${recent} 次`}`, actorDid: user.did, traceId })
    return new HttpResponse(csv, { status: 200, headers: { 'Content-Type': 'text/csv; charset=utf-8', 'Content-Disposition': `attachment; filename="audit-logs-${todayStr()}.csv"`, 'X-Trace-Id': traceId } })
  })),

  http.get(`${BASE}/audit/logs`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    const q = query(request)
    return ok(paginate(filterLogs(q), q), traceId)
  })),

  http.get(`${BASE}/audit/trace/:traceId`, handle(async ({ request, params, traceId }) => {
    requireAuth(request)
    const tid = params.traceId
    const logs = db.auditLogs.filter(l => l.traceId === tid).sort((a, b) => (a.at < b.at ? -1 : 1))
    const evs = chain.blocks.filter(b => b.trace_id === tid)
    if (!logs.length && !evs.length) throw new MockError(1005, `traceId ${tid} 无记录`)
    const steps = logs.map((l, i) => ({ seq: i + 1, module: l.module, action: l.action, at: l.at, result: l.result === 'denied' ? 'denied' : l.result, riskLevel: l.riskLevel, actorDid: l.actorDid, actorName: l.actorName, resourceType: l.resourceType, resourceId: l.resourceId, detail: l.detail, evidenceId: l.evidenceId }))
    // 未被审计日志引用的存证也列为步骤
    const referenced = new Set(steps.map(s => s.evidenceId).filter(Boolean))
    for (const e of evs) if (!referenced.has(e.evidence_id)) steps.push({ seq: 0, module: 'evidence', action: 'evidence:write', at: e.created_at, result: 'success', riskLevel: 'low', actorDid: e.actor_did, detail: `${e.category} 存证上链（高度 ${e.block_height}）`, evidenceId: e.evidence_id })
    steps.sort((a, b) => (a.at < b.at ? -1 : 1)).forEach((s, i) => { s.seq = i + 1 })
    const startAt = steps[0].at, endAt = steps[steps.length - 1].at
    const worst = ['critical', 'high', 'medium', 'low'].find(lv => steps.some(s => s.riskLevel === lv)) || 'low'
    const result = steps.some(s => s.result === 'denied') ? 'denied' : steps.some(s => s.result === 'failed') ? 'failed' : 'success'
    return ok({
      traceId: tid,
      summary: { startAt, endAt, durationMs: Math.max(0, new Date(endAt) - new Date(startAt)), actorDid: steps.find(s => s.actorDid)?.actorDid || null, actorName: logs[0]?.actorName || null, result, riskLevel: worst, stepCount: steps.length, evidenceCount: evs.length },
      steps
    }, traceId)
  })),

  http.get(`${BASE}/audit/alerts`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    const q = query(request)
    let list = db.alerts.slice().sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
    if (q.status) list = list.filter(a => a.status === q.status)
    if (q.ruleCode) list = list.filter(a => a.ruleCode === q.ruleCode)
    if (q.riskLevel) list = list.filter(a => a.riskLevel === q.riskLevel)
    const page = paginate(list, q)
    return ok({ ...page, items: page.items.map(a => ({ ...a, ruleName: RULES[a.ruleCode] || a.ruleCode, at: a.createdAt })) }, traceId)
  })),

  http.post(`${BASE}/audit/alerts/:id/ack`, handle(async ({ request, params, traceId }) => {
    const user = requireAuth(request)
    const a = db.alerts.find(x => x.id === Number(params.id))
    if (!a) throw new MockError(1005, '告警不存在')
    if (a.status === 'acked') throw new MockError(1006, '告警已确认')
    a.status = 'acked'; a.ackedAt = now(); a.ackedBy = user.did
    writeAudit({ traceId, user, module: 'audit', action: 'alert:ack', resourceType: 'alert', resourceId: a.id, detail: `确认告警 ${a.ruleCode}` })
    return ok({ id: a.id, status: a.status, ackedAt: a.ackedAt, ackedBy: a.ackedBy }, traceId)
  })),

  http.get(`${BASE}/audit/report`, handle(async ({ request, traceId }) => {
    const user = requireAuth(request)
    const q = query(request)
    const period = ['day', 'week', 'month'].includes(q.period) ? q.period : 'day'
    const date = /^\d{4}-\d{2}-\d{2}$/.test(q.date || '') ? q.date : todayStr()
    const [from, to] = periodRange(period, date)
    const logs = db.auditLogs.filter(l => inRange(l.at, from, to))
    const cnt = (pred) => logs.filter(pred).length
    const alerts = db.alerts.filter(a => inRange(a.createdAt, from, to))
    const riskEvents = Object.keys(RULES).map(ruleCode => ({ ruleCode, name: RULES[ruleCode], count: alerts.filter(a => a.ruleCode === ruleCode).length, level: alerts.find(a => a.ruleCode === ruleCode)?.riskLevel || 'high' })).filter(r => r.count > 0)
    const chainStatus = chain.status()
    const stats = {
      totalLogs: logs.length,
      highRisk: cnt(l => l.riskLevel === 'high' || l.riskLevel === 'critical'),
      denied: cnt(l => l.result !== 'success'),
      identityOps: { register: cnt(l => l.action === 'did:register'), freeze: cnt(l => l.action === 'did:freeze'), revoke: cnt(l => l.action === 'did:revoke'), rotate: cnt(l => l.action === 'key:rotate') },
      permissionOps: { applied: cnt(l => l.action === 'permission:apply'), approved: cnt(l => l.action === 'permission:approve'), rejected: cnt(l => l.action === 'permission:reject'), revoked: cnt(l => l.action === 'permission:revoke') },
      evidence: { total: chainStatus.totalRecords, byCategory: chainStatus.byCategory, periodNew: chain.blocks.filter(b => inRange(b.created_at, from, to)).length },
      riskEvents, chainIntact: chainStatus.intact, brokenAt: chainStatus.brokenAt
    }
    const narrative = buildNarrative(period, date, stats)
    writeAudit({ traceId, user, module: 'audit', action: 'audit:report', resourceType: 'audit', detail: `生成${period}审计报告 ${date}` })
    return ok({
      period, date, range: { from, to }, identityOps: stats.identityOps, permissionOps: stats.permissionOps, evidence: stats.evidence, riskEvents,
      totals: { logs: stats.totalLogs, highRisk: stats.highRisk, denied: stats.denied, alerts: alerts.length },
      byModule: ['auth', 'did', 'key', 'asset', 'permission', 'evidence', 'algo', 'audit'].map(module => ({ module, count: cnt(l => l.module === module) })),
      narrative, narrativeSource: 'cache', generatedAt: now()
    }, traceId)
  })),

  http.get(`${BASE}/audit/stats`, handle(async ({ request, traceId }) => {
    requireAuth(request)
    const today = todayStr()
    const todayLogs = db.auditLogs.filter(l => dayOf(l.at) === today)
    const byModule = ['auth', 'did', 'key', 'asset', 'permission', 'evidence', 'algo', 'audit'].map(module => ({ module, count: db.auditLogs.filter(l => l.module === module).length }))
    const byRisk = ['low', 'medium', 'high', 'critical'].map(riskLevel => ({ riskLevel, count: db.auditLogs.filter(l => l.riskLevel === riskLevel).length }))
    const trend = []
    for (let d = 6; d >= 0; d--) {
      const date = toIso8(new Date(Date.now() - d * 86400000)).slice(0, 10)
      const ls = db.auditLogs.filter(l => dayOf(l.at) === date)
      trend.push({ date, total: ls.length, high: ls.filter(l => l.riskLevel === 'high' || l.riskLevel === 'critical').length })
    }
    return ok({
      todayLogs: todayLogs.length, highRiskLogs: db.auditLogs.filter(l => l.riskLevel === 'high' || l.riskLevel === 'critical').length,
      openAlerts: db.alerts.filter(a => a.status === 'open').length, onChainLogs: db.auditLogs.filter(l => l.hash).length, totalLogs: db.auditLogs.length,
      byModule, byRisk, trend
    }, traceId)
  }))
]
