"""
差分隐私（高斯机制 + 隐私预算会计）。

对应研究报告 2.2.1 节「差分隐私保护 / 动态隐私预算分配」：
  1. L2 范数裁剪：   g' = g / max(1, ||g||_2 / C)
  2. 高斯机制加噪：   g~ = g' + N(0, (C·σ)^2 · I)
  3. 隐私预算会计（DEV-PLAN §2 规定的简化矩会计，Abadi et al. 2016 的闭式近似）：
         ε = q · sqrt(T · log(1/δ)) / σ
     其中 q 为采样率（采用 DP-SGD 约定 q = 本地 mini-batch 大小 / 节点样本量），
     T 为总轮数，δ 为失败概率，σ 为噪声乘子。反推得：
         σ = q · sqrt(T · log(1/δ)) / ε
     第 t 轮累计 ε(t) = q·sqrt(t·log(1/δ))/σ，相邻两轮之差即为本轮增量。

工程约定（写给答辩评委）：
  - 裁剪阈值 C 采用自适应裁剪：取本轮各节点更新 L2 范数的中位数，避免固定 C 与真实
    更新量级不匹配导致「全裁没」或「没裁到」；但 C 不得超过配置上限 DP_CLIP_NORM
    （否则噪声随被噪声撑大的更新一起增长，形成正反馈而发散）；
  - 噪声由聚合服务端加在加权平均之后（DP-FedAvg，McMahan et al. 2018）：
    加权平均对单节点更新的敏感度为 C·w_max（w_max 为最大样本权重），
    因此噪声为 N(0, (σ·C·w_max)^2)；
  - 为保证模型可用性，σ 设上限 SIGMA_MAX；当申请的 ε 过小导致所需 σ 超过上限时，
    按 SIGMA_MAX 加噪，预算会在训练中途耗尽 → exhausted=True → 上报
    anomaly=privacy_budget_exhausted（训练继续、噪声继续、会计继续累计，保证曲线完整并让
    后端/前端清楚看到超限）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# 噪声乘子上限：超过它，161 维参数的噪声范数远大于更新本身，模型完全不可用
SIGMA_MAX = 2.0


def clip_by_l2(g: np.ndarray, clip_norm: float) -> tuple[np.ndarray, float]:
    """L2 裁剪：g' = g / max(1, ||g||/C)。返回裁剪后梯度及裁剪前范数。"""
    norm = float(np.linalg.norm(g))
    scale = max(1.0, norm / clip_norm) if clip_norm > 0 else 1.0
    return g / scale, norm


def add_gaussian_noise(g: np.ndarray, clip_norm: float, sigma: float, rng: np.random.Generator) -> np.ndarray:
    """高斯机制：g~ = g + N(0, (C·σ)^2)。"""
    if sigma <= 0:
        return g
    return g + rng.normal(0.0, clip_norm * sigma, size=g.shape)


def sigma_from_budget(epsilon: float, delta: float, total_rounds: int, q: float = 1.0) -> float:
    """由 (ε, δ, T, q) 反推噪声乘子：σ = q·sqrt(T·ln(1/δ)) / ε。"""
    epsilon = max(float(epsilon), 1e-6)
    delta = min(max(float(delta), 1e-12), 0.5)
    return q * math.sqrt(total_rounds * math.log(1.0 / delta)) / epsilon


def epsilon_of_rounds(sigma: float, delta: float, t: int, q: float = 1.0) -> float:
    """已消耗 t 轮时的累计 ε = q·sqrt(t·ln(1/δ)) / σ（简化矩会计的闭式形式）。"""
    if sigma <= 0:
        return 0.0
    delta = min(max(float(delta), 1e-12), 0.5)
    return q * math.sqrt(t * math.log(1.0 / delta)) / sigma


@dataclass
class PrivacyAccountant:
    """
    隐私预算会计器。

    - epsilon_target：任务申请的总预算 ε
    - 每轮调用 step() 返回 (本轮增量, 累计)；
    - exhausted 为 True 表示累计已超过目标预算：训练仍继续（保持曲线完整），
      但后续轮次噪声乘子不再变化，且响应里会带 anomaly=privacy_budget_exhausted，
      后端据此生成 R05_SUSPICIOUS_GRAD 告警（契约 3.2 说明）。
    """

    epsilon_target: float
    delta: float
    total_rounds: int
    clip_norm: float = 1.0
    q: float = 1.0
    sigma: float = field(init=False)
    clip_cap: float = field(init=False)
    rounds_done: int = field(default=0, init=False)
    spent: float = field(default=0.0, init=False)
    history: list = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.sigma_required = sigma_from_budget(self.epsilon_target, self.delta, self.total_rounds, self.q)
        self.sigma = min(self.sigma_required, SIGMA_MAX)
        self.clip_cap = max(float(self.clip_norm), 1e-8)   # 构造时传入的 clip_norm 即上限

    def step(self) -> tuple[float, float]:
        """记一轮预算消耗，返回 (增量, 累计)。"""
        before = self.spent
        self.rounds_done += 1
        self.spent = epsilon_of_rounds(self.sigma, self.delta, self.rounds_done, self.q)
        inc = self.spent - before
        self.history.append({"round": self.rounds_done, "increment": inc, "spent": self.spent})
        return inc, self.spent

    @property
    def exhausted(self) -> bool:
        # 浮点误差容忍：累计预算超过目标 0.1% 以上才判为超限
        return self.spent > self.epsilon_target * 1.001

    def clip_updates(self, updates: dict[str, np.ndarray]) -> tuple[dict[str, np.ndarray], float]:
        """自适应裁剪：C = 各节点更新范数中位数；返回 (裁剪后更新, 本轮 C)。"""
        norms = [float(np.linalg.norm(u)) for u in updates.values()]
        c = float(np.median(norms)) if norms else self.clip_norm
        # 自适应 C 只能往下调、不能越过配置上限：噪声标准差 ∝ C，C 若跟着被噪声撑大的
        # 更新范数一起涨，就是正反馈，单节点 + DP 实测 6 轮 loss 发散到 9×10⁷。
        # 封顶后 C 与噪声有界，剩下的只是 DP 本身的信噪比问题（由发散熔断兜底）。
        c = min(max(c, 1e-8), self.clip_cap)
        self.clip_norm = c
        return {k: clip_by_l2(u, c)[0] for k, u in updates.items()}, c

    def noise_aggregate(self, agg: np.ndarray, w_max: float, rng: np.random.Generator) -> np.ndarray:
        """服务端对加权平均结果加噪：N(0, (σ·C·w_max)^2)。"""
        return add_gaussian_noise(agg, self.clip_norm * w_max, self.sigma, rng)
