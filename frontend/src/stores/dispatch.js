/**
 * 调度任务 store —— 实现改为走后端 dispatch API：
 *   createDispatchTask → runDispatchTask → issueDispatchTask → ackDispatchTask
 * 向后兼容：保留 tasks / activeTask / latestTask / latestDispatchableTask / latestTaskTimeline 与
 *   initializeTask / generateTaskWithDeepSeek / dispatchTask / markTaskExecuting / completeTask /
 *   failTask / getTaskById / getLatestTaskForNode / getLatestReceivedTaskForNode / resetTasks 的签名与语义，
 *   旧页面（CloudAggregate / TerminalResponse / AuditLog）不改也能运行。
 * 新增：runTask() / issueTask(signature) / ackTask() / fetchTasks() / buildDemoSignature()。
 *
 * 本地 task 对象仍沿用 services/dispatchTask.js 的结构（status/stageHistory/aiResult/command/executionReceipt），
 * 额外附加后端字段：remoteId / strategy / qTable / explanation / explanationSource / evidenceId / traceId / commandId。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLogStore } from './logs.js'
import { usePerspectiveStore } from './perspective.js'
import { useUserStore } from './user.js'
import * as dispatchApi from '@/api/dispatch'
import { sha256Hex } from '@/utils/sha256'
import { DEEPSEEK_ADAPTER_STATE } from '../services/dispatchTask.js'
import {
  DISPATCH_TASK_STATUS,
  DISPATCH_TASK_STATUS_LABELS,
  appendTaskStage,
  canDispatchTask,
  createDispatchCommand,
  createDispatchTask as createLocalTask,
  createExecutionReceipt
} from '../services/dispatchTask.js'

const ACTION_LABELS = {
  discharge: { dispatchType: '放电调度', action: '提升储能输出支撑局部负荷', sign: 1 },
  charge: { dispatchType: '充电调度', action: '吸纳光伏余电并补充储能', sign: -1 },
  idle: { dispatchType: '待机保持', action: '维持当前运行状态', sign: 0 }
}

/** 把后端 run 结果（strategy/explanation）转为旧页面使用的 aiResult 结构 */
function strategyToAiResult(runData, task) {
  const actions = runData?.strategy?.actions || []
  const target = actions
    .filter(a => a.action !== 'idle')
    .sort((l, r) => Math.abs(r.powerKw) - Math.abs(l.powerKw))[0] || actions[0]
  if (!target) return null
  const meta = ACTION_LABELS[target.action] || ACTION_LABELS.idle
  const report = task.sourceReports.find(r => r.nodeId === target.nodeId)
  const reasoning = Array.isArray(runData.reasoning) && runData.reasoning.length
    ? runData.reasoning
    : actions.map(a => `${a.nodeId}：${a.action}${a.powerKw ? ` ${a.powerKw}kW` : ''}（Q=${a.qValue}）${a.reason ? '，' + a.reason : ''}`)
  return {
    provider: `backend-dqn/${runData.explanationSource || 'rule'}`,
    generatedAt: new Date().toISOString(),
    reasoningSummary: runData.explanation || target.reason || '',
    reasoning,
    recommendation: {
      targetNodeId: target.nodeId,
      targetNodeName: report?.nodeName || target.nodeId,
      dispatchType: meta.dispatchType,
      action: meta.action,
      targetPowerKw: Number((meta.sign * Math.abs(Number(target.powerKw) || 0)).toFixed(1)),
      schedule: task.aggregationInput.timeWindow
    }
  }
}

export const useDispatchStore = defineStore('dispatch', () => {
  const logStore = useLogStore()
  const tasks = ref([])
  const remoteTasks = ref([])
  const activeTaskId = ref(null)
  const adapterState = ref(DEEPSEEK_ADAPTER_STATE.IDLE)
  const lastError = ref(null)
  const receivedTaskStatuses = [
    DISPATCH_TASK_STATUS.DISPATCHED,
    DISPATCH_TASK_STATUS.EXECUTING,
    DISPATCH_TASK_STATUS.COMPLETED
  ]

  const activeTask = computed(() => tasks.value.find(task => task.id === activeTaskId.value) || null)
  const latestTask = computed(() => tasks.value[0] || null)
  const latestDispatchableTask = computed(() => tasks.value.find(task => canDispatchTask(task)) || null)
  const latestTaskTimeline = computed(() => activeTask.value?.stageHistory || [])

  function saveTask(task) {
    const index = tasks.value.findIndex(currentTask => currentTask.id === task.id)
    if (index >= 0) {
      tasks.value.splice(index, 1, task)
      return task
    }
    tasks.value.unshift(task)
    return task
  }

  function getTaskById(taskId) {
    return tasks.value.find(task => task.id === taskId || task.remoteId === taskId) || null
  }

  function writeTaskLog(task, content, level = 'INFO', source = 'CLOUD', extras = {}) {
    logStore.addLog(content, level, source, {
      taskId: task.id,
      taskStatus: task.status,
      taskStatusLabel: DISPATCH_TASK_STATUS_LABELS[task.status] || task.status,
      traceId: task.traceId,
      ...extras
    })
  }

  /** 创建本地任务（同步，兼容旧签名） */
  function initializeTask(reports, options = {}) {
    const task = createLocalTask({
      reports,
      metadata: {
        provider: options.provider || 'backend-dqn',
        objective: options.objective,
        timeWindow: options.timeWindow
      }
    })
    activeTaskId.value = task.id
    lastError.value = null
    saveTask(task)
    writeTaskLog(task, `创建统一调度任务 ${task.id}，已完成边缘数据汇总`, 'INFO', 'CLOUD', {
      result: '成功',
      payload: task.aggregationInput.summary
    })
    return task
  }

  function updateTaskStatus(taskId, status, detail, patch = {}) {
    const task = getTaskById(taskId)
    if (!task) return null
    const nextTask = appendTaskStage(task, status, detail, patch)
    saveTask(nextTask)
    return nextTask
  }

  function failTask(taskId, errorMessage, extras = {}) {
    lastError.value = errorMessage
    adapterState.value = DEEPSEEK_ADAPTER_STATE.FAILURE
    const task = updateTaskStatus(taskId, DISPATCH_TASK_STATUS.FAILED, errorMessage, {
      error: { message: errorMessage, failedAt: new Date().toISOString(), ...extras }
    })
    if (task) {
      writeTaskLog(task, `调度任务失败：${errorMessage}`, 'ERROR', 'CLOUD', { result: '失败', payload: task.error })
    }
    return { ok: false, task, errorMessage }
  }

  /** 时间窗字符串：2026-08-17T15:00~16:00+08:00 */
  function buildTimeWindow(task) {
    const tw = task.aggregationInput?.timeWindow || {}
    const d = new Date()
    const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    return `${date}T${tw.start || '15:00'}~${tw.end || '16:00'}+08:00`
  }

  /**
   * 生成调度策略（兼容旧名）：后端 create → run。
   * options.adapterOptions.mode==='failure' 时本地模拟失败（旧页面的"模拟失败"按钮），不调后端。
   */
  async function generateTaskWithDeepSeek(reports, options = {}) {
    const initialTask = initializeTask(reports, options)
    adapterState.value = DEEPSEEK_ADAPTER_STATE.GENERATING
    const generatingTask = updateTaskStatus(initialTask.id, DISPATCH_TASK_STATUS.GENERATING, '已提交调度任务，等待 DQN 策略生成与 AI 解释')
    writeTaskLog(generatingTask, `任务 ${generatingTask.id} 进入 AI 生成中状态`, 'INFO', 'CLOUD', { result: '进行中' })

    if (options.adapterOptions?.mode === 'failure') {
      await new Promise(r => setTimeout(r, options.adapterOptions.latencyMs ?? 600))
      return failTask(generatingTask.id, '模拟 AI 服务返回无效结果（演示失败分支）', { reason: 'simulated-failure' })
    }
    if (!reports?.length) {
      return failTask(generatingTask.id, '缺少边缘汇总数据，无法生成调度策略', { reason: 'empty-reports' })
    }

    try {
      const created = await dispatchApi.createDispatchTask({
        name: options.name || `统一调度-${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`,
        nodeIds: reports.map(r => r.nodeId),
        timeWindow: buildTimeWindow(generatingTask),
        objective: generatingTask.aggregationInput.objective
      })
      updateTaskStatus(generatingTask.id, DISPATCH_TASK_STATUS.GENERATING, `后端任务 ${created.id} 已创建`, {
        remoteId: created.id,
        traceId: created.traceId || null
      })
      const run = await dispatchApi.runDispatchTask(created.id)
      const taskNow = getTaskById(generatingTask.id)
      const aiResult = strategyToAiResult(run, taskNow)
      if (!aiResult) {
        return failTask(generatingTask.id, 'DQN 未返回有效动作', { reason: 'empty-strategy' })
      }
      adapterState.value = DEEPSEEK_ADAPTER_STATE.SUCCESS
      const taskWithAi = updateTaskStatus(generatingTask.id, DISPATCH_TASK_STATUS.GENERATED, '已生成调度策略与 AI 解释，等待签名下发', {
        aiResult,
        command: createDispatchCommand(taskNow, aiResult),
        targetNodeId: aiResult.recommendation.targetNodeId,
        strategy: run.strategy,
        qTable: run.qTable || null,
        explanation: run.explanation,
        explanationSource: run.explanationSource,
        evidenceId: run.evidenceId || null,
        traceId: run.traceId || taskNow.traceId || null,
        error: null
      })
      writeTaskLog(taskWithAi, `任务 ${taskWithAi.id} 已生成调度策略，目标节点 ${taskWithAi.targetNodeId}（解释来源 ${run.explanationSource || '-'}）`, 'INFO', 'CLOUD', {
        result: '成功',
        payload: taskWithAi.command
      })
      return { ok: true, task: taskWithAi }
    } catch (err) {
      return failTask(generatingTask.id, err?.message || '调度服务调用失败', { code: err?.code, traceId: err?.traceId })
    }
  }

  /** 新接口：基于当前全部节点上报生成策略 */
  function runTask(options = {}) {
    const perspectiveStore = usePerspectiveStore()
    const reports = options.reports || perspectiveStore.dispatchReports
    return generateTaskWithDeepSeek(reports, options)
  }

  /** 演示用签名：sig:sha256(signerDid|taskId) —— 真实环境由 SM2 私钥签名 */
  function buildDemoSignature(taskId = activeTaskId.value) {
    const userStore = useUserStore()
    const task = getTaskById(taskId)
    return `sig:${sha256Hex(`${userStore.did || 'anonymous'}|${task?.remoteId || taskId}|${Date.now()}`)}`
  }

  /** 新接口：调后端下发（校验 dispatch:issue 权限 + 签名）。失败抛出带 code 的 error。 */
  async function issueTask(signature, taskId = activeTaskId.value) {
    const task = getTaskById(taskId)
    if (!task?.remoteId) throw Object.assign(new Error('任务尚未在后端创建，无法下发'), { code: 1001 })
    try {
      const res = await dispatchApi.issueDispatchTask(task.remoteId, { signature: signature || buildDemoSignature(taskId) })
      const dispatchedAt = new Date().toISOString()
      const next = updateTaskStatus(task.id, DISPATCH_TASK_STATUS.DISPATCHED, `调度指令已签名下发至 ${task.command?.targetNodeName || res.targets?.join(',')}`, {
        command: task.command ? { ...task.command, issuedAt: dispatchedAt, commandId: res.commandId, signerDid: res.signerDid } : task.command,
        commandId: res.commandId,
        signerDid: res.signerDid,
        issueEvidenceId: res.evidenceId,
        dispatchedAt
      })
      writeTaskLog(next, `任务 ${next.id} 已下发（commandId ${res.commandId}，签名人 ${res.signerDid}）`, 'INFO', 'CLOUD', { result: '成功' })
      return res
    } catch (err) {
      const denied = err?.code === 1003 || err?.code === 1004
      const next = updateTaskStatus(task.id, DISPATCH_TASK_STATUS.FAILED, err?.message || '下发失败', {
        error: { message: err?.message, code: err?.code, traceId: err?.traceId, failedAt: new Date().toISOString() }
      })
      writeTaskLog(next, denied ? `下发被拒绝（code ${err.code}）：${err.message}` : `下发失败：${err?.message}`, 'ERROR', 'CLOUD', { result: '失败', traceId: err?.traceId })
      throw err
    }
  }

  /**
   * 兼容旧名：同步返回 {ok, task}，本地状态立即置为 DISPATCHED，后端 issue 在后台进行；
   * 若后端拒绝（1003/1004），任务会被 issueTask 标记为 FAILED。
   */
  function dispatchTask(taskId = activeTaskId.value, signature) {
    const currentTask = getTaskById(taskId)
    if (!canDispatchTask(currentTask)) {
      return failTask(taskId, '当前任务未生成有效指令，禁止下发无效调度命令', { reason: 'invalid-dispatch-command' })
    }
    const dispatchedAt = new Date().toISOString()
    const task = updateTaskStatus(taskId, DISPATCH_TASK_STATUS.DISPATCHED, `调度指令已下发至 ${currentTask.command.targetNodeName}`, {
      command: { ...currentTask.command, issuedAt: dispatchedAt },
      dispatchedAt
    })
    writeTaskLog(task, `任务 ${task.id} 已下发至终端 ${task.command.targetNodeName}`, 'INFO', 'CLOUD', { result: '成功' })
    if (currentTask.remoteId) {
      issueTask(signature, taskId).catch(() => { /* 已在 issueTask 内记录 */ })
    }
    return { ok: true, task }
  }

  function markTaskExecuting(taskId = activeTaskId.value, extras = {}) {
    const currentTask = getTaskById(taskId)
    if (!currentTask) return null
    const startedAt = extras.startedAt || currentTask.executionStartedAt || new Date().toISOString()
    const task = updateTaskStatus(taskId, DISPATCH_TASK_STATUS.EXECUTING, '目标终端已接收指令并开始执行', {
      ...extras,
      executionStartedAt: startedAt
    })
    if (task) writeTaskLog(task, `任务 ${task.id} 进入执行中状态`, 'INFO', 'EDGE', { result: '进行中' })
    return task
  }

  /** 新接口：边缘节点回执（POST /dispatch/tasks/:id/ack） */
  async function ackTask(taskId = activeTaskId.value, data = {}) {
    const task = getTaskById(taskId)
    if (!task?.remoteId) return null
    const res = await dispatchApi.ackDispatchTask(task.remoteId, {
      nodeId: task.command?.targetNodeId || task.targetNodeId,
      status: 'success',
      actualPowerKw: task.executionReceipt?.actualPowerKw,
      ...data
    })
    updateTaskStatus(task.id, task.status, '后端已记录执行回执', { ackEvidenceId: res?.evidenceId || null })
    return res
  }

  /** 兼容旧名：同步完成任务并在后台回传 ack */
  function completeTask(taskId = activeTaskId.value, receiptOverrides = {}) {
    const currentTask = getTaskById(taskId)
    if (!currentTask) return null
    const startedAt = receiptOverrides.startedAt || currentTask.executionStartedAt || currentTask.executionReceipt?.startedAt
    const executionReceipt = createExecutionReceipt(currentTask, { ...receiptOverrides, startedAt })
    const task = updateTaskStatus(taskId, DISPATCH_TASK_STATUS.COMPLETED, '终端执行完成并已回传执行回执', {
      executionReceipt,
      executionStartedAt: executionReceipt.startedAt,
      completedAt: executionReceipt.completedAt
    })
    writeTaskLog(task, `任务 ${task.id} 已完成，回执已同步云端`, 'INFO', 'EDGE', { result: '成功', payload: executionReceipt })
    if (currentTask.remoteId) {
      ackTask(taskId).catch(() => { /* request.js 已提示 */ })
    }
    return task
  }

  /** 拉取后端任务列表（放在 remoteTasks，不覆盖本地视图模型） */
  async function fetchTasks(params = {}) {
    const data = await dispatchApi.listDispatchTasks({ size: 50, ...params })
    remoteTasks.value = data?.items || []
    return remoteTasks.value
  }

  function getLatestTaskForNode(nodeId) {
    return tasks.value.find(task => task.targetNodeId === nodeId || task.command?.targetNodeId === nodeId) || null
  }

  function getLatestReceivedTaskForNode(nodeId) {
    return tasks.value.find(task => {
      const matchesNode = task.targetNodeId === nodeId || task.command?.targetNodeId === nodeId
      return matchesNode && receivedTaskStatuses.includes(task.status)
    }) || null
  }

  function resetTasks() {
    tasks.value = []
    activeTaskId.value = null
    adapterState.value = DEEPSEEK_ADAPTER_STATE.IDLE
    lastError.value = null
  }

  return {
    tasks,
    remoteTasks,
    activeTaskId,
    adapterState,
    lastError,
    activeTask,
    latestTask,
    latestDispatchableTask,
    latestTaskTimeline,
    initializeTask,
    generateTaskWithDeepSeek,
    runTask,
    dispatchTask,
    issueTask,
    buildDemoSignature,
    markTaskExecuting,
    completeTask,
    ackTask,
    fetchTasks,
    failTask,
    getTaskById,
    getLatestTaskForNode,
    getLatestReceivedTaskForNode,
    resetTasks
  }
})
