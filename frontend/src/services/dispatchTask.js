/**
 * 调度任务本地视图模型的纯函数集合（无副作用）。
 * 后端调度 API（create/run/issue/ack）由 stores/dispatch.js 调用，这里只负责：
 * - 本地任务状态机常量与中文标签
 * - 边缘上报（createEdgeReport）与聚合输入（buildAggregationInput）的构造
 * - 本地任务对象、阶段记录、指令与回执的生成
 * 原 deepseekAdapter.js（假 AI）已删除，其状态枚举迁移到此处以供 store 复用。
 */
/** AI/DQN 生成阶段的状态枚举（原 deepseekAdapter 导出，现由 store 复用） */
export const DEEPSEEK_ADAPTER_STATE = {
  IDLE: 'idle',
  GENERATING: 'generating',
  SUCCESS: 'success',
  FAILURE: 'failure'
}

const DEFAULT_DISPATCH_WINDOW = {
  start: '14:35',
  end: '15:35'
}

const DEFAULT_OBJECTIVE = '平衡边缘节点负荷并保障储能安全边界'

export const DISPATCH_TASK_STATUS = {
  COLLECTING: 'collecting',
  GENERATING: 'generating',
  GENERATED: 'generated',
  DISPATCHED: 'dispatched',
  EXECUTING: 'executing',
  COMPLETED: 'completed',
  FAILED: 'failed'
}

export const DISPATCH_TASK_STATUS_LABELS = {
  [DISPATCH_TASK_STATUS.COLLECTING]: '待汇总',
  [DISPATCH_TASK_STATUS.GENERATING]: 'AI生成中',
  [DISPATCH_TASK_STATUS.GENERATED]: '待下发',
  [DISPATCH_TASK_STATUS.DISPATCHED]: '已下发',
  [DISPATCH_TASK_STATUS.EXECUTING]: '执行中',
  [DISPATCH_TASK_STATUS.COMPLETED]: '已完成',
  [DISPATCH_TASK_STATUS.FAILED]: '失败'
}

function createTimestamp() {
  return new Date().toISOString()
}

function createStageEntry(status, detail, extra = {}) {
  return {
    status,
    label: DISPATCH_TASK_STATUS_LABELS[status] || status,
    detail,
    at: createTimestamp(),
    ...extra
  }
}

export function createDispatchTaskId(prefix = 'dispatch') {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export function createEdgeReport(node, overrides = {}) {
  if (!node?.id) {
    return null
  }

  const metrics = {
    loadKw: Number(node.data?.load ?? 0),
    storageKw: Number(node.data?.storageOutput ?? 0),
    pvKw: Number(node.data?.pvOutput ?? 0),
    socPct: Number(node.data?.soc ?? 0),
    model: node.data?.model || '未知型号'
  }

  const safeOutputKw = Math.max(0, Math.round((metrics.socPct - 25) * 0.45))

  return {
    nodeId: node.id,
    nodeName: node.name,
    nodeStatus: node.status,
    reportedAt: createTimestamp(),
    metrics,
    constraints: {
      dispatchWindow: { ...DEFAULT_DISPATCH_WINDOW },
      minSocPct: 25,
      maxSocPct: 90,
      safeOutputKw,
      objective: DEFAULT_OBJECTIVE
    },
    ...overrides
  }
}

export function buildAggregationInput(reports = [], options = {}) {
  const normalizedReports = reports.filter(Boolean)
  const totalLoadKw = normalizedReports.reduce((sum, report) => sum + report.metrics.loadKw, 0)
  const totalPvKw = normalizedReports.reduce((sum, report) => sum + report.metrics.pvKw, 0)
  const onlineNodes = normalizedReports.filter(report => report.nodeStatus === 'online').length
  const timeWindow = options.timeWindow || DEFAULT_DISPATCH_WINDOW

  return {
    generatedAt: createTimestamp(),
    objective: options.objective || DEFAULT_OBJECTIVE,
    timeWindow,
    reports: normalizedReports,
    summary: {
      reportCount: normalizedReports.length,
      onlineNodes,
      totalLoadKw: Number(totalLoadKw.toFixed(1)),
      totalPvKw: Number(totalPvKw.toFixed(1)),
      highestLoadNodeId: normalizedReports
        .slice()
        .sort((left, right) => right.metrics.loadKw - left.metrics.loadKw)[0]?.nodeId || null
    }
  }
}

export function createDispatchTask({ reports = [], aggregationInput, metadata = {} } = {}) {
  const taskId = metadata.taskId || createDispatchTaskId()
  const sourceReports = reports.filter(Boolean)
  const aggregatedInput = aggregationInput || buildAggregationInput(sourceReports, metadata)
  const createdAt = createTimestamp()

  return {
    id: taskId,
    provider: metadata.provider || 'deepseek-adapter',
    status: DISPATCH_TASK_STATUS.COLLECTING,
    sourceReports,
    aggregationInput: aggregatedInput,
    aiResult: null,
    command: null,
    executionReceipt: null,
    error: null,
    targetNodeId: null,
    createdAt,
    updatedAt: createdAt,
    dispatchedAt: null,
    completedAt: null,
    stageHistory: [
      createStageEntry(DISPATCH_TASK_STATUS.COLLECTING, '已创建调度任务并等待 AI 生成')
    ]
  }
}

export function appendTaskStage(task, status, detail, patch = {}) {
  const updatedAt = createTimestamp()

  return {
    ...task,
    ...patch,
    status,
    updatedAt,
    stageHistory: [
      ...(task.stageHistory || []),
      createStageEntry(status, detail, patch.stageMeta)
    ]
  }
}

export function createDispatchCommand(task, aiResult = task.aiResult) {
  if (!aiResult?.recommendation) {
    return null
  }

  return {
    taskId: task.id,
    targetNodeId: aiResult.recommendation.targetNodeId,
    targetNodeName: aiResult.recommendation.targetNodeName,
    dispatchType: aiResult.recommendation.dispatchType,
    action: aiResult.recommendation.action,
    targetPowerKw: aiResult.recommendation.targetPowerKw,
    schedule: aiResult.recommendation.schedule,
    reasoningSummary: aiResult.reasoningSummary,
    issuedAt: null
  }
}

export function canDispatchTask(task) {
  return task?.status === DISPATCH_TASK_STATUS.GENERATED && Boolean(task.command)
}

export function createExecutionReceipt(task, overrides = {}) {
  const startedAt = overrides.startedAt || createTimestamp()
  const completedAt = overrides.completedAt || createTimestamp()

  return {
    taskId: task.id,
    nodeId: task.command?.targetNodeId || task.targetNodeId,
    nodeName: task.command?.targetNodeName || task.targetNodeId,
    status: overrides.status || DISPATCH_TASK_STATUS.COMPLETED,
    startedAt,
    completedAt,
    actualPowerKw: overrides.actualPowerKw ?? task.command?.targetPowerKw ?? 0,
    responseDelaySec: overrides.responseDelaySec ?? 1.2,
    summary: overrides.summary || `节点 ${task.command?.targetNodeName || task.targetNodeId} 已完成调度执行`
  }
}
