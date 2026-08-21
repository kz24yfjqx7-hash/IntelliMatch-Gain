/**
 * 科学计算薄客户端（替代原 Pyodide 版本）。
 *
 * 为什么删除 Pyodide：
 * - 原实现从外网 CDN 动态加载 Pyodide + numpy/scipy/scikit-learn（约 30MB WebAssembly），
 *   违反平台「完全离线运行、源码不得出现外网 URL」的验收标准，断网即失效；
 * - 树莓派 4B 上跑 WebAssembly Python 极其吃力，首屏等待数十秒；
 * - 真正的算法（k-means 分级、FedAvg/DP/Top-k 训练、DQN 调度）已经在算法服务中实现，
 *   前端只需要调用后端 API；页面上的"单次加噪 / 稀疏化"演示用几十行纯 JS 即可完成。
 *
 * 对外保持导出名 `scientificCompute` 与原方法签名（全部返回 Promise），调用方改动最小：
 * - load()                         恒 resolve(true)
 * - kMeansClustering(features, k)  → POST /assets/classify（后端 k-means + 规则）
 * - calculateStatistics / normalizeZScore / applyDifferentialPrivacy / applyGaussianPrivacy
 *   / topkGradientSparsity / calculateGradientNorm / calculatePrivacyBudget  纯 JS 本地实现（演示用）
 */
import { classifyAssets } from '@/api/asset'

/** 标准正态随机数（Box-Muller） */
function gaussian() {
  const u = Math.max(Math.random(), 1e-12)
  const v = Math.random()
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
}

/** 拉普拉斯随机数：位置 0、尺度 scale */
function laplace(scale) {
  const u = Math.random() - 0.5
  return -scale * Math.sign(u) * Math.log(1 - 2 * Math.abs(u))
}

function toNumbers(arr) {
  return (Array.isArray(arr) ? arr : [arr]).map(Number).filter(n => !Number.isNaN(n))
}

function percentile(sorted, p) {
  if (!sorted.length) return 0
  const idx = (sorted.length - 1) * p
  const lo = Math.floor(idx)
  const hi = Math.ceil(idx)
  if (lo === hi) return sorted[lo]
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo)
}

/**
 * 把 [sensitivity, granularity, volume] 数值特征转换为 /assets/classify 需要的 records。
 * 也兼容直接传入 records 对象数组（{dataType, fields, freq, volume}）。
 */
function featuresToRecords(features) {
  const GRAN_KEYS = ['day', 'hour', '15min', 'minute', 'second']
  return features.map((f, i) => {
    if (f && !Array.isArray(f) && typeof f === 'object') return f
    const [sens = 0.3, gran = 0.5, vol = 0.3] = Array.isArray(f) ? f : [f]
    const fields = ['ts', 'value']
    if (sens >= 0.5) fields.push('owner')
    if (sens >= 0.8) fields.push('gps')
    const gi = Math.min(GRAN_KEYS.length - 1, Math.max(0, Math.round(gran * (GRAN_KEYS.length - 1))))
    return { index: i, dataType: sens >= 0.8 ? 'dispatch' : sens >= 0.5 ? 'load' : 'pv', fields, freq: GRAN_KEYS[gi], volume: Math.round(Math.pow(10, vol * 5)) }
  })
}

class ScientificComputeService {
  constructor() {
    this.loaded = true
    this.loading = false
  }

  /** 兼容旧签名：无需加载任何引擎，恒为就绪 */
  async load() {
    this.loaded = true
    return true
  }

  /**
   * k-means 分级：调后端 POST /assets/classify。
   * @param {Array<number[]|object>} features 每条 [sensitivity, granularity, volume] 或 record 对象
   * @param {number} nClusters 仅用于返回结构兼容（后端固定 k=3）
   * @returns {Promise<{labels:number[], centers:number[][], results:object[], algorithm:string}>}
   */
  async kMeansClustering(features, nClusters = 3) {
    const records = featuresToRecords(features || [])
    const data = await classifyAssets({ records })
    const results = data?.results || []
    return {
      labels: results.map(r => r.cluster ?? 0),
      centers: (data?.clusterCenters || []).slice(0, nClusters),
      results,
      algorithm: data?.algorithm || 'kmeans(k=3)+rule'
    }
  }

  /** 描述统计（均值/标准差/方差/偏度/峰度/极值/分位数） */
  async calculateStatistics(values) {
    const v = toNumbers(values)
    const n = v.length
    if (!n) return { mean: 0, std: 0, variance: 0, skewness: 0, kurtosis: 0, min: 0, max: 0, median: 0, q25: 0, q75: 0 }
    const mean = v.reduce((s, x) => s + x, 0) / n
    const variance = v.reduce((s, x) => s + (x - mean) ** 2, 0) / n
    const std = Math.sqrt(variance)
    const m3 = v.reduce((s, x) => s + (x - mean) ** 3, 0) / n
    const m4 = v.reduce((s, x) => s + (x - mean) ** 4, 0) / n
    const sorted = v.slice().sort((a, b) => a - b)
    return {
      mean,
      std,
      variance,
      skewness: std > 0 ? m3 / std ** 3 : 0,
      kurtosis: std > 0 ? m4 / std ** 4 - 3 : 0,
      min: sorted[0],
      max: sorted[n - 1],
      median: percentile(sorted, 0.5),
      q25: percentile(sorted, 0.25),
      q75: percentile(sorted, 0.75)
    }
  }

  /** z-score 归一化 */
  async normalizeZScore(data) {
    const v = toNumbers(data)
    const n = v.length || 1
    const mean = v.reduce((s, x) => s + x, 0) / n
    const std = Math.sqrt(v.reduce((s, x) => s + (x - mean) ** 2, 0) / n)
    return { normalized: v.map(x => (std > 0 ? (x - mean) / std : 0)), mean, std }
  }

  /** 拉普拉斯机制差分隐私：scale = sensitivity / epsilon */
  async applyDifferentialPrivacy(data, epsilon = 0.5, sensitivity = 1.0) {
    const v = toNumbers(data)
    const scale = sensitivity / Math.max(epsilon, 1e-6)
    const noise = v.map(() => laplace(scale))
    return { noisyData: v.map((x, i) => x + noise[i]), noise, scale }
  }

  /** 高斯机制差分隐私：σ = Δ·sqrt(2·ln(1.25/δ)) / ε */
  async applyGaussianPrivacy(data, epsilon = 0.5, delta = 1e-5, sensitivity = 1.0) {
    const v = toNumbers(data)
    const sigma = sensitivity * Math.sqrt(2 * Math.log(1.25 / delta)) / Math.max(epsilon, 1e-6)
    const noise = v.map(() => gaussian() * sigma)
    return { noisyData: v.map((x, i) => x + noise[i]), noise, sigma }
  }

  /** L2 裁剪 + 高斯加噪（与算法服务 dp.py 同思路，演示用） */
  async clipAndNoise(gradients, clipNorm = 1.0, sigma = 0.5) {
    const g = toNumbers(gradients)
    const l2 = Math.sqrt(g.reduce((s, x) => s + x * x, 0))
    const factor = l2 > clipNorm ? clipNorm / l2 : 1
    const clipped = g.map(x => x * factor)
    const noisy = clipped.map(x => x + gaussian() * clipNorm * sigma)
    return { clipped, noisy, l2Before: l2, l2After: Math.sqrt(clipped.reduce((s, x) => s + x * x, 0)), clipFactor: factor }
  }

  /** Top-k 稀疏化：按 |g| 保留前 k 个，其余置零，compressionRatio=(1-k/n)*100 */
  async topkGradientSparsity(gradients, k = 5) {
    const g = toNumbers(gradients)
    const n = g.length
    const kk = Math.max(0, Math.min(Math.round(k), n))
    const keptIndices = g
      .map((x, i) => ({ i, a: Math.abs(x) }))
      .sort((l, r) => r.a - l.a)
      .slice(0, kk)
      .map(o => o.i)
      .sort((a, b) => a - b)
    const keep = new Set(keptIndices)
    const sparseGradients = g.map((x, i) => (keep.has(i) ? x : 0))
    return { sparseGradients, keptIndices, compressionRatio: n ? (1 - kk / n) * 100 : 0 }
  }

  /** L1 / L2 / L∞ 范数 */
  async calculateGradientNorm(gradients) {
    const g = toNumbers(gradients)
    return {
      l1Norm: g.reduce((s, x) => s + Math.abs(x), 0),
      l2Norm: Math.sqrt(g.reduce((s, x) => s + x * x, 0)),
      infinityNorm: g.reduce((m, x) => Math.max(m, Math.abs(x)), 0)
    }
  }

  /** 隐私预算组合（基础组合 / 高级组合） */
  async calculatePrivacyBudget(epsilon, numQueries, mechanism = 'laplace') {
    const q = Math.max(0, Number(numQueries) || 0)
    const eps = Number(epsilon) || 0
    const total = mechanism === 'laplace' ? eps * Math.sqrt(2 * q) : eps * Math.sqrt(2 * q * Math.log(1 / 1e-5))
    return { totalEpsilon: total, privacyCost: q > 0 ? total / q : eps }
  }

  /** 生成一组服从 N(0, std²) 的演示梯度 */
  randomGradients(size = 36, std = 0.5) {
    return Array.from({ length: size }, () => gaussian() * std)
  }
}

export const scientificCompute = new ScientificComputeService()
export default scientificCompute
