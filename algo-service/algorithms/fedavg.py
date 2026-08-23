"""
联邦平均 FedAvg（研究报告 2.2.2 节）+ 差分隐私 + Top-k 稀疏化 + 投毒检测。

全局优化目标：
    min_θ F(θ) = Σ_k (n_k / n) · F_k(θ)
其中 θ 为全局模型参数，F_k 为节点 k 的本地损失，n_k 为节点样本量，n = Σ n_k。

每轮流程：
  1. 服务端下发全局参数 θ_t；
  2. 各节点用本地数据训练 E 个 epoch 得到 θ_k，本地更新 Δ_k = θ_k − θ_t；
  3. （可选）Top-k 稀疏化 + 残差缓存（topk.py，节点上传前执行）；
  4. （可选）DP 裁剪：每个节点更新按自适应阈值 C 做 L2 裁剪（dp.py）；
  5. 投毒检测：cos θ_{i,j} = (Δ_i·Δ_j)/(‖Δ_i‖‖Δ_j‖)，节点得分 = 与其余节点余弦相似度均值，
     每轮剔除得分最低且低于阈值的一个节点 → 标记 gradient_poisoning（需 ≥3 节点）；
  6. 聚合：θ_{t+1} = θ_t + Σ_k (n_k/n)·Δ_k；
  7. （可选）DP 加噪：服务端对聚合结果加 N(0,(σ·C·w_max)^2)，并记一轮预算；
  8. 梯度哈希：H_t = SHA256(聚合后更新向量)（报告用 SM3，契约允许 SHA256）。

本地模型：NumPy MLP 8-16-1（tanh 隐层，线性输出），均方误差损失。
acc 定义：全局测试集上 |ŷ − y| / max(|y|, 1e-6) < 10% 的样本占比。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

import config
from algorithms.dp import PrivacyAccountant
from algorithms.topk import ResidualTopK

IN_DIM, HID_DIM, OUT_DIM = 8, 16, 1


# ---------------------------------------------------------------- MLP
class MLP:
    """8-16-1 两层感知机，参数以一维向量形式收发，方便做稀疏化/加噪/哈希。"""

    def __init__(self, rng: np.random.Generator):
        # Xavier 初始化
        self.W1 = rng.normal(0, np.sqrt(2.0 / (IN_DIM + HID_DIM)), (IN_DIM, HID_DIM))
        self.b1 = np.zeros(HID_DIM)
        self.W2 = rng.normal(0, np.sqrt(2.0 / (HID_DIM + OUT_DIM)), (HID_DIM, OUT_DIM))
        self.b2 = np.zeros(OUT_DIM)

    # --- 参数向量化 ---
    def get_flat(self) -> np.ndarray:
        return np.concatenate([self.W1.ravel(), self.b1, self.W2.ravel(), self.b2])

    def set_flat(self, v: np.ndarray) -> None:
        i = 0
        for name, shape in (("W1", (IN_DIM, HID_DIM)), ("b1", (HID_DIM,)), ("W2", (HID_DIM, OUT_DIM)), ("b2", (OUT_DIM,))):
            size = int(np.prod(shape))
            setattr(self, name, v[i : i + size].reshape(shape).copy())
            i += size

    @property
    def dim(self) -> int:
        return IN_DIM * HID_DIM + HID_DIM + HID_DIM * OUT_DIM + OUT_DIM

    # --- 前向 / 反向 ---
    def forward(self, X: np.ndarray) -> np.ndarray:
        self._X = X
        self._H = np.tanh(X @ self.W1 + self.b1)
        return self._H @ self.W2 + self.b2

    def backward_step(self, y_pred: np.ndarray, y: np.ndarray, lr: float) -> None:
        """MSE 损失 L = mean((ŷ−y)^2) 的手写反向传播 + SGD 更新。"""
        m = y.shape[0]
        dout = 2.0 * (y_pred - y.reshape(-1, 1)) / m
        dW2 = self._H.T @ dout
        db2 = dout.sum(0)
        dH = dout @ self.W2.T * (1 - self._H**2)  # tanh'
        dW1 = self._X.T @ dH
        db1 = dH.sum(0)
        self.W1 -= lr * dW1
        self.b1 -= lr * db1
        self.W2 -= lr * dW2
        self.b2 -= lr * db2

    def loss(self, X: np.ndarray, y: np.ndarray) -> float:
        p = self.forward(X).ravel()
        return float(np.mean((p - y) ** 2))

    def accuracy(self, X: np.ndarray, y: np.ndarray, tol: float = 0.10) -> float:
        """acc = 相对误差 < tol 的样本占比（负荷预测常用的 10% 容差）。"""
        p = self.forward(X).ravel()
        rel = np.abs(p - y) / np.maximum(np.abs(y), 1e-6)
        return float(np.mean(rel < tol))


def local_train(model: MLP, X: np.ndarray, y: np.ndarray, epochs: int, lr: float, batch: int, rng: np.random.Generator) -> float:
    """节点本地 mini-batch SGD，返回训练后本地损失。"""
    n = X.shape[0]
    for _ in range(epochs):
        perm = rng.permutation(n)
        for s in range(0, n, batch):
            idx = perm[s : s + batch]
            pred = model.forward(X[idx])
            model.backward_step(pred, y[idx], lr)
    return model.loss(X, y)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度 cos θ = (a·b) / (||a||·||b||)，报告 2.2.2 投毒检测公式。"""
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return float(a @ b / (na * nb))


# ---------------------------------------------------------------- 数据加载
def load_node_datasets(path=None) -> dict:
    """读取 data/node_datasets.npz；不存在则自动生成。"""
    path = path or config.NODE_DATASET_PATH
    if not path.exists():
        from scripts.gen_datasets import generate

        generate(path)
    d = np.load(path)
    out = {"test_X": d["test_X"], "test_y": d["test_y"], "nodes": {}}
    for nid in d["node_ids"]:
        out["nodes"][str(nid)] = (d[f"{nid}_X"], d[f"{nid}_y"])
    return out


# ---------------------------------------------------------------- 训练器
@dataclass
class RoundResult:
    round: int
    loss: float
    acc: float
    compressionRatio: float
    epsilonSpent: float
    gradientHash: str
    nodeContributions: list
    anomaly: dict | None = None

    def to_dict(self) -> dict:
        d = {
            "round": self.round,
            "loss": round(self.loss, 6),
            "acc": round(self.acc, 4),
            "compressionRatio": round(self.compressionRatio, 2),
            "epsilonSpent": round(self.epsilonSpent, 4),
            "gradientHash": self.gradientHash,
            "nodeContributions": self.nodeContributions,
        }
        return d


@dataclass
class FedAvgTrainer:
    """
    一个 FL 任务的训练器。调用 run_round() 逐轮推进，由任务管理器在后台线程驱动。

    参数
    ----
    nodes: [{"id": "Node-A", "samples": 480}, ...]  samples 用于加权；数据从数据集里取前 samples 条
    dp:    {"enabled": bool, "epsilon": float, "delta": float}
    topk:  {"enabled": bool, "ratio": float}
    simulate_poison: 可选，指定节点 id，让其更新翻转放大（演示投毒检测）
    """

    rounds: int
    nodes: list
    dp: dict
    topk: dict
    simulate_poison: str | None = None
    seed: int = 42
    datasets: dict = field(default_factory=load_node_datasets)

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)
        self.global_model = MLP(self.rng)
        self.dim = self.global_model.dim
        # 节点数据：优先用数据集里同名节点；陌生 id 轮询复用已有画像
        known = list(self.datasets["nodes"].keys())
        self.node_data: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.node_samples: dict[str, int] = {}
        for i, nd in enumerate(self.nodes):
            nid = nd["id"]
            X, y = self.datasets["nodes"].get(nid) or self.datasets["nodes"][known[i % len(known)]]
            n = int(nd.get("samples") or X.shape[0])
            n = max(16, min(n, X.shape[0]))
            self.node_data[nid] = (X[:n], y[:n])
            self.node_samples[nid] = n
        self.total_samples = sum(self.node_samples.values())
        # DP 会计
        self.dp_enabled = bool(self.dp.get("enabled"))
        self.accountant = (
            PrivacyAccountant(
                epsilon_target=float(self.dp.get("epsilon", 1.0)),
                delta=float(self.dp.get("delta", 1e-5)),
                total_rounds=self.rounds,
                clip_norm=config.DP_CLIP_NORM,
                q=min(1.0, config.FL_BATCH_SIZE / (self.total_samples / max(1, len(self.node_samples)))),
            )
            if self.dp_enabled
            else None
        )
        # Top-k 压缩器（每节点一份残差缓存）
        self.topk_enabled = bool(self.topk.get("enabled"))
        ratio = float(self.topk.get("ratio", 0.1))
        self.compressors = {nid: ResidualTopK(self.dim, ratio) for nid in self.node_data} if self.topk_enabled else {}
        self.current_round = 0
        self.history: list[RoundResult] = []
        self.anomaly: dict | None = None
        self.excluded: set[str] = set()  # 已判定投毒、后续轮次降权剔除的节点
        self.best_loss: float = float("inf")
        self.diverge_streak: int = 0

    # ------------------------------------------------------------ 单轮
    def run_round(self) -> RoundResult:
        self.current_round += 1
        t = self.current_round
        theta = self.global_model.get_flat()
        lr = config.FL_LOCAL_LR / (1.0 + config.FL_LR_DECAY * (t - 1))  # 学习率衰减，抑制客户端漂移
        updates: dict[str, np.ndarray] = {}
        local_losses: dict[str, float] = {}
        comp_ratios: list[float] = []

        # 1~3. 各节点本地训练 + Top-k 稀疏化（节点侧）
        for nid, (X, y) in self.node_data.items():
            local = MLP(self.rng)
            local.set_flat(theta)
            local_losses[nid] = local_train(local, X, y, config.FL_LOCAL_EPOCHS, lr, config.FL_BATCH_SIZE, self.rng)
            delta = local.get_flat() - theta
            if self.simulate_poison and nid == self.simulate_poison:
                delta = -5.0 * delta  # 投毒演示：方向翻转并放大 5 倍
            if self.topk_enabled:
                delta, cr = self.compressors[nid].compress(delta)
                comp_ratios.append(cr)
            updates[nid] = delta

        # 4. DP 裁剪（服务端收到后按自适应 C 裁剪）
        if self.accountant is not None:
            updates, _ = self.accountant.clip_updates(updates)

        # 5. 投毒检测：每个节点的更新方向与其余（未剔除）节点的平均余弦相似度
        #    每轮最多剔除一个得分最低且低于阈值的节点（避免恶意节点把正常节点"带偏"而误杀）；需 ≥3 个在线节点
        round_anomaly = None
        alive = [n for n in updates if n not in self.excluded]
        if len(alive) >= 3:
            scores = {}
            for nid in alive:
                sims = [cosine(updates[nid], updates[o]) for o in alive if o != nid]
                scores[nid] = float(np.mean(sims))
            worst = min(scores, key=scores.get)
            if scores[worst] < config.POISON_COS_THRESHOLD:
                self.excluded.add(worst)
                round_anomaly = {
                    "type": "gradient_poisoning",
                    "nodeId": worst,
                    "round": t,
                    "detail": f"节点 {worst} 第 {t} 轮梯度与其余节点的平均余弦相似度 {scores[worst]:.3f} 低于阈值 {config.POISON_COS_THRESHOLD}，已剔除其聚合权重",
                }

        # 6. FedAvg 加权聚合（剔除投毒节点）
        active = [n for n in updates if n not in self.excluded] or list(updates)
        denom = sum(self.node_samples[n] for n in active)
        agg = sum(self.node_samples[n] * updates[n] for n in active) / denom

        # 7. DP 加噪（服务端，敏感度 C·w_max）+ 预算会计
        eps_spent = 0.0
        if self.accountant is not None:
            w_max = max(self.node_samples[n] for n in active) / denom
            agg = self.accountant.noise_aggregate(agg, w_max, self.rng)
        theta_new = theta + agg
        self.global_model.set_flat(theta_new)

        # 8. 指标 + 哈希
        loss = self.global_model.loss(self.datasets["test_X"], self.datasets["test_y"])
        acc = self.global_model.accuracy(self.datasets["test_X"], self.datasets["test_y"])
        ghash = hashlib.sha256(np.ascontiguousarray(agg, dtype=np.float64).tobytes()).hexdigest()
        if self.accountant is not None:
            _, eps_spent = self.accountant.step()
            if self.accountant.exhausted and round_anomaly is None and (self.anomaly is None or self.anomaly.get("type") != "privacy_budget_exhausted"):
                round_anomaly = {
                    "type": "privacy_budget_exhausted",
                    "nodeId": None,
                    "round": t,
                    "detail": f"第 {t} 轮累计隐私预算 ε={eps_spent:.4f} 已超过目标 ε={self.accountant.epsilon_target}",
                }
        # 9. 发散熔断：loss 非有限，或连续 PATIENCE 轮比历史最优差 FACTOR 倍
        #    典型诱因：节点太少（w_max→1）或 ε 太小，噪声范数是更新的几十倍。
        #    投毒/预算异常优先级更高，同一轮只报一个。
        if not np.isfinite(loss) or (np.isfinite(self.best_loss) and loss > self.best_loss * config.FL_DIVERGE_FACTOR):
            self.diverge_streak += 1
        else:
            self.diverge_streak = 0
        if np.isfinite(loss):
            self.best_loss = min(self.best_loss, loss)
        if round_anomaly is None and (not np.isfinite(loss) or self.diverge_streak >= config.FL_DIVERGE_PATIENCE):
            n_nodes = len(active)
            hint = ("参与节点仅 1 个，差分隐私噪声权重 w_max=1、无法被聚合平均稀释" if n_nodes == 1
                    else f"参与节点 {n_nodes} 个")
            sig = f"，噪声乘子 σ={self.accountant.sigma:.2f}" if self.accountant is not None else ""
            round_anomaly = {
                "type": "training_diverged",
                "nodeId": None,
                "round": t,
                "detail": (f"第 {t} 轮测试 loss={loss:.4g} 已达历史最优 {self.best_loss:.4g} 的 "
                           f"{config.FL_DIVERGE_FACTOR:g} 倍以上（连续 {self.diverge_streak} 轮）；{hint}{sig}。"
                           f"建议增加参与节点或放宽 ε 后重试"),
            }
        if round_anomaly is not None:
            self.anomaly = round_anomaly

        contributions = [
            {
                "nodeId": nid,
                "weight": round(self.node_samples[nid] / denom, 4) if nid in active else 0.0,
                "localLoss": round(local_losses[nid], 6),
            }
            for nid in updates
        ]
        res = RoundResult(
            round=t,
            loss=loss,
            acc=acc,
            compressionRatio=float(np.mean(comp_ratios)) if comp_ratios else 0.0,
            epsilonSpent=eps_spent,
            gradientHash=ghash,
            nodeContributions=contributions,
            anomaly=round_anomaly,
        )
        self.history.append(res)
        return res
