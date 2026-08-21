"""
生成 4 个边缘节点的 Non-IID 本地负荷预测数据集 -> data/node_datasets.npz

任务：用 8 维特征回归「下一时刻负荷」（归一化到 [0,1]）。
特征（8 维）：
  0 hour_sin  1 hour_cos   2 是否工作日  3 温度(归一)  4 光伏出力(归一)
  5 上一时刻负荷(归一)  6 电价(归一)  7 储能SOC(归一)
Non-IID 设计：每个节点的负荷基线、峰值时段、温敏系数、光伏装机都不同
（Node-A 工业、Node-B 商业、Node-C 居民、Node-D 园区混合），
使得单节点模型无法泛化到全网，联邦聚合才有意义。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

# 节点画像：(基线负荷kW, 峰值时刻, 峰值幅度, 温敏系数, 光伏装机kW, 样本量)
NODE_PROFILES = {
    "Node-A": dict(base=120.0, peak_hour=10, amp=60.0, temp_k=0.8, pv_cap=50.0, n=480),
    "Node-B": dict(base=90.0, peak_hour=14, amp=70.0, temp_k=1.2, pv_cap=30.0, n=320),
    "Node-C": dict(base=60.0, peak_hour=19, amp=90.0, temp_k=0.5, pv_cap=20.0, n=400),
    "Node-D": dict(base=150.0, peak_hour=12, amp=40.0, temp_k=1.0, pv_cap=80.0, n=560),
}
LOAD_SCALE = 300.0  # 负荷归一化分母（kW）


def _gen_node(rng: np.random.Generator, p: dict) -> tuple[np.ndarray, np.ndarray]:
    n = p["n"]
    hour = rng.integers(0, 24, size=n)
    weekday = (rng.random(n) < 5 / 7).astype(float)
    temp = rng.normal(25, 6, size=n)
    # 光伏：白天钟形曲线 × 装机 × 随机云量
    pv = p["pv_cap"] * np.clip(np.sin((hour - 6) / 12 * np.pi), 0, None) * rng.uniform(0.5, 1.0, n)
    price = 0.4 + 0.4 * (np.abs(hour - 14) < 4) + 0.15 * (np.abs(hour - 19) < 2) + rng.normal(0, 0.02, n)
    soc = rng.uniform(20, 95, n)
    # 当前负荷：基线 + 峰值钟形 + 温度项 + 工作日项 + 噪声
    peak = p["amp"] * np.exp(-((hour - p["peak_hour"]) ** 2) / (2 * 2.5**2))
    load_now = p["base"] + peak + p["temp_k"] * np.maximum(temp - 26, 0) * 3 + 15 * weekday + rng.normal(0, 5, n)
    # 目标：下一时刻负荷（峰值时刻向后平移 1 小时 + 惯性）
    peak_next = p["amp"] * np.exp(-((hour + 1 - p["peak_hour"]) ** 2) / (2 * 2.5**2))
    load_next = 0.6 * load_now + 0.4 * (p["base"] + peak_next + 15 * weekday) + rng.normal(0, 4, n)

    X = np.stack(
        [
            np.sin(2 * np.pi * hour / 24),
            np.cos(2 * np.pi * hour / 24),
            weekday,
            (temp - 10) / 30,
            pv / 100.0,
            load_now / LOAD_SCALE,
            price,
            soc / 100.0,
        ],
        axis=1,
    ).astype(np.float64)
    y = (load_next / LOAD_SCALE).astype(np.float64)
    return X, y


def generate(path: Path | None = None, seed: int = 2026) -> Path:
    """生成并保存数据集，返回文件路径。"""
    path = Path(path or config.NODE_DATASET_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    arrays: dict[str, np.ndarray] = {}
    for node_id, prof in NODE_PROFILES.items():
        X, y = _gen_node(rng, prof)
        arrays[f"{node_id}_X"] = X
        arrays[f"{node_id}_y"] = y
    # 全局测试集：四种画像各抽 100 条，用于评估全局模型
    tx, ty = [], []
    for prof in NODE_PROFILES.values():
        X, y = _gen_node(rng, {**prof, "n": 100})
        tx.append(X)
        ty.append(y)
    arrays["test_X"] = np.concatenate(tx)
    arrays["test_y"] = np.concatenate(ty)
    arrays["node_ids"] = np.array(list(NODE_PROFILES.keys()))
    np.savez_compressed(path, **arrays)
    return path


if __name__ == "__main__":
    out = generate()
    d = np.load(out)
    print(f"已生成 {out}")
    for nid in d["node_ids"]:
        print(f"  {nid}: X{d[f'{nid}_X'].shape} y均值={d[f'{nid}_y'].mean():.3f}")
