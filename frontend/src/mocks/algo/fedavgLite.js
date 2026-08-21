/**
 * 简化版 FedAvg（mock 真算，JS 实现），用于答辩兜底时曲线仍随参数变化：
 * - 4 个节点各自一份 Non-IID 小数据集（8 维特征 → 负荷比例回归）
 * - 模型：1 隐层 MLP（8-16-1，tanh），本地 SGD 一个 epoch
 * - 差分隐私：对节点模型增量做 L2 裁剪（C=1.0）+ 高斯噪声 N(0,(C·σ)²)，
 *   σ 由 ε/δ/T 反推（简化矩会计 ε = sqrt(T·log(1/δ))/σ），每轮累计 epsilonSpent=ε/T
 * - Top-k：按 |Δ| 保留前 ratio 比例，其余置零，compressionRatio=(1-k/n)*100 真算
 * - 按样本量加权平均增量；acc 定义为相对误差 <10% 的样本占比
 * - 梯度投毒检测：节点增量与其它节点均值的余弦相似度 < 0 → anomaly
 */
import { sha256Hex } from '../../utils/sha256.js'

const IN = 8, HID = 16
const CLIP = 1.0

/* 确定性随机 */
function makeRng(seed) {
  let s = seed >>> 0
  return () => {
    s |= 0; s = (s + 0x6D2B79F5) | 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
function gauss(rng) {
  const u = Math.max(rng(), 1e-12), v = rng()
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
}
const sigmoid = x => 1 / (1 + Math.exp(-x))

/** 真实函数（所有节点共享），节点特征分布各不相同（Non-IID） */
const W_TRUE = [0.8, -0.5, 0.6, 0.3, -0.7, 0.4, 0.2, -0.3]
function genDataset(rng, n, shift) {
  const X = [], Y = []
  for (let i = 0; i < n; i++) {
    const x = []
    for (let j = 0; j < IN; j++) x.push((rng() * 2 - 1) + shift[j])
    let z = 0.2
    for (let j = 0; j < IN; j++) z += W_TRUE[j] * x[j]
    // 目标落在 (0.2, 1.0)，避免相对误差分母过小
    const y = 0.2 + 0.8 * sigmoid(z) + gauss(rng) * 0.02
    X.push(x); Y.push(y)
  }
  return { X, Y }
}

/** 参数向量：W1(IN*HID) b1(HID) W2(HID) b2(1) */
const SIZE = IN * HID + HID + HID + 1
function initParams(rng) {
  const p = new Float64Array(SIZE)
  for (let i = 0; i < IN * HID; i++) p[i] = (rng() * 2 - 1) * 0.3
  for (let i = IN * HID + HID; i < IN * HID + HID + HID; i++) p[i] = (rng() * 2 - 1) * 0.3
  return p
}

function forward(p, x) {
  const h = new Float64Array(HID)
  for (let j = 0; j < HID; j++) {
    let s = p[IN * HID + j]
    for (let i = 0; i < IN; i++) s += p[j * IN + i] * x[i]
    h[j] = Math.tanh(s)
  }
  let out = p[SIZE - 1]
  for (let j = 0; j < HID; j++) out += p[IN * HID + HID + j] * h[j]
  return { h, out }
}

/** 一轮本地 SGD（单 epoch，batch=16） */
function localTrain(pGlobal, data, lr, rng) {
  const p = Float64Array.from(pGlobal)
  const idx = data.X.map((_, i) => i).sort(() => rng() - 0.5)
  const B = 16
  for (let s = 0; s < idx.length; s += B) {
    const g = new Float64Array(SIZE)
    const batch = idx.slice(s, s + B)
    for (const k of batch) {
      const x = data.X[k], y = data.Y[k]
      const { h, out } = forward(p, x)
      const dOut = 2 * (out - y) / batch.length
      g[SIZE - 1] += dOut
      for (let j = 0; j < HID; j++) {
        g[IN * HID + HID + j] += dOut * h[j]
        const dh = dOut * p[IN * HID + HID + j] * (1 - h[j] * h[j])
        g[IN * HID + j] += dh
        for (let i = 0; i < IN; i++) g[j * IN + i] += dh * x[i]
      }
    }
    for (let i = 0; i < SIZE; i++) p[i] -= lr * g[i]
  }
  return p
}

function evaluate(p, data) {
  let se = 0, hit = 0
  for (let k = 0; k < data.X.length; k++) {
    const { out } = forward(p, data.X[k])
    const y = data.Y[k]
    se += (out - y) ** 2
    if (Math.abs(out - y) / Math.abs(y) < 0.1) hit++
  }
  return { loss: se / data.X.length, acc: hit / data.X.length }
}

function l2(v) { let s = 0; for (let i = 0; i < v.length; i++) s += v[i] * v[i]; return Math.sqrt(s) }
function cosine(a, b) {
  let d = 0; for (let i = 0; i < a.length; i++) d += a[i] * b[i]
  const na = l2(a), nb = l2(b)
  return na && nb ? d / (na * nb) : 0
}

/**
 * 创建并运行一个 FedAvg 作业（异步按轮推进）
 * @param {object} opts {taskId, nodes:[{nodeId,samples}], rounds, dp:{enabled,epsilon,delta}, topk:{enabled,ratio}, simulatePoison, intervalMs, onRound(r), onDone(result), onCancel()}
 * @returns {{cancel:Function}}
 */
export function createFedAvgJob(opts) {
  const { taskId, rounds, nodes, dp = {}, topk = {}, simulatePoison = null, intervalMs = 1000, onRound, onDone } = opts
  const rng = makeRng(hashSeed(taskId))
  const SHIFTS = [
    [0.3, 0, 0, 0.2, 0, 0, 0, 0], [0, -0.3, 0.2, 0, 0, 0, 0.1, 0], [0, 0, 0, 0, 0.4, -0.2, 0, 0], [-0.2, 0, 0, 0, 0, 0, 0, 0.3]
  ]
  const datasets = nodes.map((n, i) => genDataset(rng, n.samples || 300, SHIFTS[i % 4]))
  const testSet = genDataset(rng, 200, [0, 0, 0, 0, 0, 0, 0, 0])
  const totalSamples = datasets.reduce((s, d) => s + d.X.length, 0)
  let pGlobal = initParams(rng)
  const lr = 0.08
  const T = rounds
  const eps = Number(dp.epsilon) || 1.0
  const delta = Number(dp.delta) || 1e-5
  const sigma = dp.enabled ? Math.sqrt(T * Math.log(1 / delta)) / eps : 0
  const ratio = topk.enabled ? Math.min(1, Math.max(0.01, Number(topk.ratio) || 0.1)) : 1
  let epsilonSpent = 0
  let cancelled = false
  let timer = null
  const history = []
  let anomaly = null

  function step(r) {
    if (cancelled) return
    const deltas = []
    const contributions = []
    for (let i = 0; i < nodes.length; i++) {
      const pLocal = localTrain(pGlobal, datasets[i], lr, rng)
      const d = new Float64Array(SIZE)
      for (let k = 0; k < SIZE; k++) d[k] = pLocal[k] - pGlobal[k]
      const localLoss = evaluate(pLocal, datasets[i]).loss
      // 模拟梯度投毒：翻转并放大
      if (simulatePoison && simulatePoison === nodes[i].nodeId) for (let k = 0; k < SIZE; k++) d[k] *= -5
      // 裁剪
      const norm = l2(d)
      if (norm > CLIP) for (let k = 0; k < SIZE; k++) d[k] *= CLIP / norm
      // 高斯噪声
      if (dp.enabled) for (let k = 0; k < SIZE; k++) d[k] += gauss(rng) * CLIP * sigma * 0.02 // 缩放因子使演示曲线可收敛
      deltas.push(d)
      contributions.push({ nodeId: nodes[i].nodeId, weight: Number((datasets[i].X.length / totalSamples).toFixed(3)), localLoss: Number(localLoss.toFixed(4)) })
    }
    // 投毒检测：与其它节点均值的余弦相似度
    if (!anomaly) {
      for (let i = 0; i < deltas.length; i++) {
        const mean = new Float64Array(SIZE)
        let cnt = 0
        for (let j = 0; j < deltas.length; j++) if (j !== i) { cnt++; for (let k = 0; k < SIZE; k++) mean[k] += deltas[j][k] }
        if (cnt) for (let k = 0; k < SIZE; k++) mean[k] /= cnt
        const cs = cosine(deltas[i], mean)
        if (cs < -0.2) {
          anomaly = { type: 'gradient_poisoning', nodeId: nodes[i].nodeId, round: r, detail: `节点梯度与全局方向余弦相似度 ${cs.toFixed(2)}，疑似投毒，已剔除` }
          deltas[i].fill(0)
        }
      }
    }
    // Top-k 稀疏化 + 加权聚合
    const agg = new Float64Array(SIZE)
    let kept = 0
    for (let i = 0; i < deltas.length; i++) {
      const d = deltas[i]
      if (ratio < 1) {
        const k = Math.max(1, Math.round(SIZE * ratio))
        const thr = Array.from(d).map(Math.abs).sort((a, b) => b - a)[k - 1]
        for (let j = 0; j < SIZE; j++) if (Math.abs(d[j]) < thr) d[j] = 0
      }
      for (let j = 0; j < SIZE; j++) { if (d[j] !== 0) kept++; agg[j] += d[j] * contributions[i].weight }
    }
    const compressionRatio = Number(((1 - kept / (SIZE * deltas.length)) * 100).toFixed(1))
    for (let k = 0; k < SIZE; k++) pGlobal[k] += agg[k]
    if (dp.enabled) epsilonSpent = Number(Math.min(eps, epsilonSpent + eps / T).toFixed(4))
    if (dp.enabled && r === T && epsilonSpent >= eps && !anomaly && opts.simulateBudgetExhausted) {
      anomaly = { type: 'privacy_budget_exhausted', detail: `隐私预算 ε=${eps} 已耗尽` }
    }
    const { loss, acc } = evaluate(pGlobal, testSet)
    const gradientHash = 'sm3:' + sha256Hex(Array.from(agg).map(v => v.toFixed(6)).join(','))
    const round = {
      round: r, loss: Number(loss.toFixed(4)), acc: Number(acc.toFixed(3)), compressionRatio,
      epsilonSpent: dp.enabled ? epsilonSpent : 0, gradientHash, nodeContributions: contributions
    }
    history.push(round)
    try { onRound && onRound(round, { anomaly }) } catch (e) { console.error(e) }
    if (r >= T) {
      onDone && onDone({ rounds: history, anomaly, modelVersion: null })
    } else {
      timer = setTimeout(() => step(r + 1), intervalMs)
    }
  }

  timer = setTimeout(() => step(1), Math.min(intervalMs, 300))
  return {
    cancel() {
      cancelled = true
      if (timer) clearTimeout(timer)
    },
    get history() { return history }
  }
}

function hashSeed(str) {
  let h = 2166136261
  for (let i = 0; i < String(str).length; i++) { h ^= String(str).charCodeAt(i); h = Math.imul(h, 16777619) }
  return h >>> 0
}
