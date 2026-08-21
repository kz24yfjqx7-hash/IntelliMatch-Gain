"""
Top-k 梯度稀疏化 + 残差缓存（研究报告 2.2.3 节）。

  T_k(g) = { g_i | |g_i| >= τ_k }          τ_k 为 |g| 降序排列后第 k 大的值
  残差缓存补偿：
  c(t+1) = c(t) + g(t) − T_k(g(t))          未上传的分量累积到下一轮
  压缩率：compressionRatio = (1 − k/n) × 100 (%)
"""
from __future__ import annotations

import numpy as np


def topk_sparsify(g: np.ndarray, ratio: float) -> tuple[np.ndarray, np.ndarray, float]:
    """
    对一维梯度向量 g 执行 Top-k 稀疏化。

    返回 (稀疏后梯度, 保留掩码, 压缩率%)。ratio∈(0,1] 为保留比例 k/n。
    """
    n = g.size
    ratio = float(min(max(ratio, 0.0), 1.0))
    k = max(1, int(round(n * ratio)))
    if k >= n:
        return g.copy(), np.ones(n, dtype=bool), 0.0
    # 第 k 大阈值 τ_k：用 argpartition 取绝对值最大的 k 个下标（O(n)）
    idx = np.argpartition(np.abs(g), n - k)[n - k:]
    mask = np.zeros(n, dtype=bool)
    mask[idx] = True
    sparse = np.where(mask, g, 0.0)
    compression = (1.0 - k / n) * 100.0
    return sparse, mask, compression


class ResidualTopK:
    """带残差缓存的 Top-k 压缩器（每个节点一个实例）。"""

    def __init__(self, dim: int, ratio: float):
        self.ratio = ratio
        self.residual = np.zeros(dim)  # c(t)

    def compress(self, g: np.ndarray) -> tuple[np.ndarray, float]:
        """
        输入本轮梯度 g(t)，输出实际上传的稀疏梯度 T_k(g(t)+c(t)) 与压缩率。
        残差更新：c(t+1) = (g(t)+c(t)) − T_k(g(t)+c(t))
        """
        corrected = g + self.residual
        sparse, mask, ratio = topk_sparsify(corrected, self.ratio)
        self.residual = corrected - sparse
        return sparse, ratio
