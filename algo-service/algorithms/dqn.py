"""
DQN 储能调度（研究报告 2.2.4 节）：环境、Q 网络、训练器、推理与约束校验，全部 NumPy 手写。

MDP 建模  M = <S, A, p, R, γ>
  状态  s_t = [p_pv, p_load, SOC, price, hour]（推理接口输入即这 5 项，归一化后送入网络）
  动作  a_t ∈ {charge, idle, discharge}（储能充/闲/放，功率步长 P_STEP）
  奖励  r_t = α1·B_t − α2·c_imb − α3·c_pri − α4·c_safe
        B_t   市场收益 / 削峰收益：放电按电价卖出、充电按电价买入 → price·P·Δt
        c_imb 不平衡惩罚：|净负荷| = |load − pv − P_dis + P_ch|，按峰时放大
        c_pri 隐私成本：本服务的调度接口输入不含隐私风险等级，取 α3 = 0（注释保留公式位置）
        c_safe 设备约束惩罚：SOC 越出 [SOC_MIN, SOC_MAX] 或功率超 MAX_POWER_KW
  Q 值更新（时序差分）  Q(s,a) ← Q(s,a) + η·[ r + γ·max_a' Q_target(s',a') − Q(s,a) ]
  网络：5-64-64-3 两隐层 MLP（ReLU），Huber 损失，Adam 优化，经验回放 + 目标网络（软更新）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import config

ACTIONS = ("charge", "idle", "discharge")
STATE_DIM, N_ACTIONS = 5, 3

# ---- 物理参数 ----
CAPACITY_KWH = 100.0  # 单节点储能容量
P_STEP = 20.0  # 训练时固定充放功率（kW），推理时由 Q 值差与 SOC 余量决定具体功率
DT_H = 1.0  # 步长 1 小时
EFF = 0.95  # 充放电效率
# ---- 奖励权重 α1~α4 ----
ALPHA_BENEFIT, ALPHA_IMB, ALPHA_PRI, ALPHA_SAFE = 1.0, 0.08, 0.0, 5.0
# ---- 状态归一化尺度（与训练脚本一致，随 checkpoint 一并保存）----
STATE_SCALE = np.array([100.0, 200.0, 100.0, 1.0, 23.0])


def price_of_hour(h: int, rng: np.random.Generator | None = None) -> float:
    """分时电价（元/kWh）：谷 0.30 / 平 0.62 / 峰 1.05，叠加小扰动。"""
    if 0 <= h < 7 or h >= 23:
        p = 0.30
    elif 10 <= h < 15 or 18 <= h < 21:
        p = 1.05
    else:
        p = 0.62
    if rng is not None:
        p += rng.normal(0, 0.02)
    return float(max(p, 0.05))


def normalize_state(s: np.ndarray) -> np.ndarray:
    return np.asarray(s, dtype=np.float64) / STATE_SCALE


def reward_fn(pv: float, load: float, soc: float, price: float, hour: int, action: int, power_kw: float) -> tuple[float, float, dict]:
    """
    计算一步奖励与新 SOC。返回 (reward, new_soc, 分项明细)。
    action: 0 charge / 1 idle / 2 discharge；power_kw 为该动作的功率大小（≥0）。
    """
    p_ch = power_kw if action == 0 else 0.0
    p_dis = power_kw if action == 2 else 0.0
    # SOC 变化（百分比）
    d_soc = (p_ch * EFF - p_dis / EFF) * DT_H / CAPACITY_KWH * 100.0
    new_soc = soc + d_soc
    # B_t：放电卖电收益 − 充电购电成本（峰时放电、谷时充电为正向）
    benefit = price * (p_dis - p_ch) * DT_H
    # c_imb：净负荷不平衡（削峰目标），峰时段惩罚加倍
    net = load - pv - p_dis + p_ch
    peak_factor = 2.0 if price > 0.9 else 1.0
    c_imb = peak_factor * abs(net) / 100.0 * power_kw / P_STEP if action != 1 else peak_factor * abs(load - pv) / 100.0
    # 更直接的削峰收益：动作使 |净负荷| 相比不动作减少的量
    shave = (abs(load - pv) - abs(net)) / 100.0 * peak_factor
    # c_safe：SOC 越限 / 功率越限。处于越限区时，朝安全区恢复的动作（低于下限时充电、高于上限时放电）不罚，
    # 其余动作按越限深度惩罚，使网络学会"低 SOC 先充电、高 SOC 先放电"
    c_safe = 0.0
    if new_soc < config.SOC_MIN and action != 0:
        c_safe += (config.SOC_MIN - new_soc) / 10.0 + 1.0
    if new_soc > config.SOC_MAX and action != 2:
        c_safe += (new_soc - config.SOC_MAX) / 10.0 + 1.0
    if power_kw > config.MAX_POWER_KW:
        c_safe += 1.0
    c_pri = 0.0  # 隐私成本项（本接口无风险等级输入，α3=0）
    r = ALPHA_BENEFIT * (benefit + shave) - ALPHA_IMB * c_imb - ALPHA_PRI * c_pri - ALPHA_SAFE * c_safe
    new_soc = float(np.clip(new_soc, 0.0, 100.0))
    return float(r), new_soc, {"benefit": benefit, "shave": shave, "c_imb": c_imb, "c_safe": c_safe}


class StorageEnv:
    """单节点储能日内调度环境：一个 episode = 24 小时，每步 1 小时。"""

    def __init__(self, seed: int = 0):
        self.rng = np.random.default_rng(seed)
        self.reset()

    def reset(self) -> np.ndarray:
        r = self.rng
        self.hour = int(r.integers(0, 24))
        self.t = 0
        self.pv_cap = float(r.uniform(20, 90))
        self.load_base = float(r.uniform(60, 160))
        self.peak_hour = float(r.choice([10, 12, 14, 19]))
        self.soc = float(r.uniform(15, 100))  # 故意包含越限起点，让网络学会回到安全区
        return self._obs()

    def _pv(self, h: int) -> float:
        return self.pv_cap * max(0.0, np.sin((h - 6) / 12 * np.pi)) * float(self.rng.uniform(0.6, 1.0))

    def _load(self, h: int) -> float:
        return self.load_base + 70 * np.exp(-((h - self.peak_hour) ** 2) / (2 * 2.5**2)) + float(self.rng.normal(0, 5))

    def _obs(self) -> np.ndarray:
        self.pv = self._pv(self.hour)
        self.load = self._load(self.hour)
        self.price = price_of_hour(self.hour, self.rng)
        return np.array([self.pv, self.load, self.soc, self.price, self.hour], dtype=np.float64)

    def step(self, action: int) -> tuple[np.ndarray, float, bool]:
        r, self.soc, _ = reward_fn(self.pv, self.load, self.soc, self.price, self.hour, action, P_STEP if action != 1 else 0.0)
        self.hour = (self.hour + 1) % 24
        self.t += 1
        return self._obs(), r, self.t >= 24


# ---------------------------------------------------------------- Q 网络
class QNetwork:
    """5-64-64-3 MLP（ReLU），Adam 优化，手写反向传播。"""

    def __init__(self, rng: np.random.Generator | None = None, hidden: int = 64):
        rng = rng or np.random.default_rng(0)
        self.params = {
            "W1": rng.normal(0, np.sqrt(2 / STATE_DIM), (STATE_DIM, hidden)),
            "b1": np.zeros(hidden),
            "W2": rng.normal(0, np.sqrt(2 / hidden), (hidden, hidden)),
            "b2": np.zeros(hidden),
            "W3": rng.normal(0, np.sqrt(1 / hidden), (hidden, N_ACTIONS)),
            "b3": np.zeros(N_ACTIONS),
        }
        self.m = {k: np.zeros_like(v) for k, v in self.params.items()}
        self.v = {k: np.zeros_like(v) for k, v in self.params.items()}
        self.t = 0

    def forward(self, X: np.ndarray, cache: bool = False) -> np.ndarray:
        p = self.params
        Z1 = X @ p["W1"] + p["b1"]
        H1 = np.maximum(Z1, 0)
        Z2 = H1 @ p["W2"] + p["b2"]
        H2 = np.maximum(Z2, 0)
        out = H2 @ p["W3"] + p["b3"]
        if cache:
            self._c = (X, Z1, H1, Z2, H2)
        return out

    def train_step(self, X: np.ndarray, actions: np.ndarray, targets: np.ndarray, lr: float) -> float:
        """Huber 损失 L = mean(huber(Q(s,a) − y))，只对采样动作列回传梯度。"""
        q = self.forward(X, cache=True)
        X_, Z1, H1, Z2, H2 = self._c
        n = X.shape[0]
        qa = q[np.arange(n), actions]
        err = qa - targets
        # Huber 梯度：|err|<=1 时为 err，否则 sign(err)
        g = np.clip(err, -1.0, 1.0) / n
        dout = np.zeros_like(q)
        dout[np.arange(n), actions] = g
        grads = {}
        grads["W3"] = H2.T @ dout
        grads["b3"] = dout.sum(0)
        dH2 = dout @ self.params["W3"].T * (Z2 > 0)
        grads["W2"] = H1.T @ dH2
        grads["b2"] = dH2.sum(0)
        dH1 = dH2 @ self.params["W2"].T * (Z1 > 0)
        grads["W1"] = X_.T @ dH1
        grads["b1"] = dH1.sum(0)
        self._adam(grads, lr)
        loss = np.where(np.abs(err) <= 1, 0.5 * err**2, np.abs(err) - 0.5).mean()
        return float(loss)

    def _adam(self, grads: dict, lr: float, b1: float = 0.9, b2: float = 0.999, eps: float = 1e-8) -> None:
        self.t += 1
        for k, g in grads.items():
            self.m[k] = b1 * self.m[k] + (1 - b1) * g
            self.v[k] = b2 * self.v[k] + (1 - b2) * g * g
            mh = self.m[k] / (1 - b1**self.t)
            vh = self.v[k] / (1 - b2**self.t)
            self.params[k] -= lr * mh / (np.sqrt(vh) + eps)

    def copy_from(self, other: "QNetwork", tau: float = 1.0) -> None:
        """目标网络软更新 θ⁻ ← τθ + (1−τ)θ⁻。"""
        for k in self.params:
            self.params[k] = tau * other.params[k] + (1 - tau) * self.params[k]


class ReplayBuffer:
    def __init__(self, cap: int, rng: np.random.Generator):
        self.cap, self.rng = cap, rng
        self.S = np.zeros((cap, STATE_DIM))
        self.A = np.zeros(cap, dtype=np.int64)
        self.R = np.zeros(cap)
        self.S2 = np.zeros((cap, STATE_DIM))
        self.D = np.zeros(cap)
        self.n = self.i = 0

    def add(self, s, a, r, s2, d):
        self.S[self.i], self.A[self.i], self.R[self.i], self.S2[self.i], self.D[self.i] = s, a, r, s2, d
        self.i = (self.i + 1) % self.cap
        self.n = min(self.n + 1, self.cap)

    def sample(self, k: int):
        idx = self.rng.integers(0, self.n, size=k)
        return self.S[idx], self.A[idx], self.R[idx], self.S2[idx], self.D[idx]


# ---------------------------------------------------------------- 训练
def train(episodes: int = 3000, seed: int = 7, log_path: Path | None = None, verbose: bool = True) -> tuple[QNetwork, dict]:
    """
    DQN 训练主循环：ε-greedy 探索（1.0→0.05 线性衰减）、经验回放（batch 64）、
    目标网络软更新（τ=0.01）、折扣 γ=0.95、学习率 1e-3。
    返回 (训练好的 Q 网络, 元信息 dict)。
    """
    rng = np.random.default_rng(seed)
    env = StorageEnv(seed)
    q, q_tgt = QNetwork(rng), QNetwork(rng)
    q_tgt.copy_from(q)
    buf = ReplayBuffer(50_000, rng)
    gamma, lr, batch, tau = 0.95, 1e-3, 64, 0.01
    eps_start, eps_end, eps_decay_ep = 1.0, 0.05, int(episodes * 0.6)
    curve, losses = [], []
    steps = 0
    for ep in range(episodes):
        s = normalize_state(env.reset())
        epsilon = max(eps_end, eps_start - (eps_start - eps_end) * ep / max(1, eps_decay_ep))
        ep_r, done = 0.0, False
        while not done:
            if rng.random() < epsilon:
                a = int(rng.integers(0, N_ACTIONS))
            else:
                a = int(np.argmax(q.forward(s[None])[0]))
            s2_raw, r, done = env.step(a)
            s2 = normalize_state(s2_raw)
            buf.add(s, a, r, s2, float(done))
            s = s2
            ep_r += r
            steps += 1
            if buf.n >= 1000:
                S, A, R, S2, D = buf.sample(batch)
                # y = r + γ·(1−done)·max_a' Q_target(s',a')
                y = R + gamma * (1 - D) * q_tgt.forward(S2).max(1)
                losses.append(q.train_step(S, A, y, lr))
                q_tgt.copy_from(q, tau)
        curve.append(ep_r)
        if verbose and (ep + 1) % max(1, episodes // 20) == 0:
            print(f"[train_dqn] episode {ep + 1}/{episodes}  epsilon={epsilon:.3f}  avg_reward(last100)={np.mean(curve[-100:]):.2f}  loss={np.mean(losses[-200:]) if losses else 0:.4f}")
    meta = {
        "episodes": episodes,
        "steps": steps,
        "seed": seed,
        "gamma": gamma,
        "lr": lr,
        "final_avg_reward": float(np.mean(curve[-100:])),
        "first_avg_reward": float(np.mean(curve[:100])),
    }
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # 训练曲线按每 10 个 episode 取均值，便于前端/答辩展示
        w = max(1, episodes // 300)
        curve_ds = [float(np.mean(curve[i : i + w])) for i in range(0, len(curve), w)]
        with open(log_path, "w", encoding="utf-8") as fh:
            json.dump({"meta": meta, "rewardCurve": curve_ds, "window": w, "lossCurve": [float(x) for x in losses[:: max(1, len(losses) // 300)]]}, fh, ensure_ascii=False, indent=1)
    return q, meta


def save_checkpoint(q: QNetwork, meta: dict, path: Path | None = None) -> Path:
    path = Path(path or config.DQN_MODEL_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, state_scale=STATE_SCALE, meta=json.dumps(meta, ensure_ascii=False), actions=np.array(ACTIONS), **q.params)
    return path


def load_checkpoint(path: Path | None = None) -> tuple[QNetwork, dict]:
    path = Path(path or config.DQN_MODEL_PATH)
    d = np.load(path, allow_pickle=False)
    q = QNetwork()
    for k in q.params:
        q.params[k] = d[k]
    meta = json.loads(str(d["meta"]))
    return q, meta


# ---------------------------------------------------------------- 推理
_NODE_BOUNDS = {"pv": (0.0, -1e6, 1e6), "load": (0.0, -1e6, 1e6), "soc": (50.0, 0.0, 100.0), "price": (0.62, 0.0, 1e4), "storage": (0.0, -1e6, 1e6)}


def _finite(v, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if np.isfinite(f) else default


def _sanitize_node(nd: dict) -> dict:
    out = dict(nd)
    for k, (dflt, lo, hi) in _NODE_BOUNDS.items():
        out[k] = float(min(max(_finite(nd.get(k, dflt), dflt), lo), hi))
    h = nd.get("hour")
    if h is not None:
        out["hour"] = int(_finite(h, 12)) % 24
    return out


class DQNDispatcher:
    """加载 checkpoint，对多节点给出调度动作，并做硬约束校验。"""

    def __init__(self, path: Path | None = None):
        self.q, self.meta = load_checkpoint(path)

    @staticmethod
    def _reason(action: str, node: dict, rank_load: int, n_nodes: int, violated: str | None) -> str:
        soc, load, pv, price = node["soc"], node["load"], node["pv"], node["price"]
        parts = []
        if violated == "soc_low":
            parts.append(f"SOC {soc:.0f}% 低于下限 {config.SOC_MIN:.0f}%，禁止放电")
        elif violated == "soc_high":
            parts.append(f"SOC {soc:.0f}% 高于上限 {config.SOC_MAX:.0f}%，禁止充电")
        if action == "discharge":
            parts.append("负荷最高" if rank_load == 0 else f"负荷排第{rank_load + 1}位")
            parts.append("SOC充足" if soc >= 60 else "SOC尚可")
            if price >= 0.9:
                parts.append("处于峰时电价")
        elif action == "charge":
            if pv > load:
                parts.append("光伏过剩")
            if price <= 0.4:
                parts.append("谷时电价")
            parts.append("SOC偏低" if soc < 50 else "SOC有余量")
        else:
            parts.append("供需基本平衡" if abs(load - pv) < 30 else "充放收益不足以抵消约束成本")
            parts.append("保持待机")
        return "，".join(dict.fromkeys(parts)) if parts else "维持当前状态"

    def dispatch(self, nodes: list[dict]) -> dict:
        """
        nodes: [{"id","pv","load","soc","storage","price"}]
        返回契约 3.3 的 actions / totalReward / qTable / constraintsChecked（另加扩展字段 immediateReward）。
        totalReward = Σ_k Q(s_k, a_k)：执行动作的 Q 值之和，即策略的期望折扣累计回报（DQN 优化目标）；
        immediateReward = Σ_k r_k：按奖励函数算出的单步即时奖励之和，仅供对照。
        """
        hour = 12
        actions, q_table, violations = [], [], []
        total_reward = 0.0  # 策略期望累计回报 Σ_k Q(s_k, a_k)
        immediate = 0.0  # 各节点单步即时奖励之和 Σ_k r_k（可能为负：如为恢复 SOC 在峰时充电）
        # 输入消毒（防御性，HTTP 层已做 pydantic 校验）：非有限值按默认、数值裁剪到物理合理范围，
        # 避免 1e308 之类的输入让 Q 值溢出为 inf/nan 导致 JSON 序列化失败
        nodes = [_sanitize_node(nd) for nd in nodes]
        loads = sorted(range(len(nodes)), key=lambda i: -nodes[i]["load"])
        rank = {i: r for r, i in enumerate(loads)}
        for i, nd in enumerate(nodes):
            s = normalize_state([nd["pv"], nd["load"], nd["soc"], nd["price"], nd.get("hour", hour)])
            qv = self.q.forward(s[None])[0]
            q_table.append({"nodeId": nd["id"], "charge": round(float(qv[0]), 4), "idle": round(float(qv[1]), 4), "discharge": round(float(qv[2]), 4)})
            order = list(np.argsort(-qv))
            best = int(order[0])
            violated = None
            # ---- 硬约束校验 ----
            # SOC ≤ 下限：禁止放电；SOC ≥ 上限：禁止充电。
            # violations 只记「网络最优动作被约束改写」（attempted != applied）的真实拦截：
            # 契约 3.3 示例中无违规即 violations: []。节点虽在越限区但网络本就没选禁用动作时，
            # 不算违规——该情况已由动作的 reason 字段写明（"SOC x% 低于下限 y%，禁止放电"），校验留痕不丢。
            soc = nd["soc"]
            if soc <= config.SOC_MIN:
                violated, banned = "soc_low", 2
            elif soc >= config.SOC_MAX:
                violated, banned = "soc_high", 0
            else:
                banned = -1
            chosen = best
            for cand in order:
                cand = int(cand)
                if cand == banned:
                    continue
                chosen = cand
                break
            if violated and best != chosen:
                side = "下限" if violated == "soc_low" else "上限"
                violations.append(
                    {
                        "nodeId": nd["id"],
                        "constraint": "socMin" if violated == "soc_low" else "socMax",
                        "attempted": ACTIONS[best],
                        "applied": ACTIONS[chosen],
                        "detail": f"SOC={soc:.1f}% 触发{side}约束，禁止 {ACTIONS[banned]}，原动作 {ACTIONS[best]} 改为 {ACTIONS[chosen]}",
                    }
                )
            # ---- 功率：Q 值优势 × SOC 余量，封顶 MAX_POWER_KW ----
            power = 0.0
            if chosen != 1:
                second = float(sorted(qv)[-2])
                adv = max(0.0, float(qv[chosen]) - second)
                intensity = float(np.clip(0.5 + adv / (abs(float(qv[chosen])) + 1e-6), 0.5, 1.0))
                if chosen == 2:
                    headroom_kwh = (soc - config.SOC_MIN) / 100.0 * CAPACITY_KWH * EFF
                else:
                    headroom_kwh = (config.SOC_MAX - soc) / 100.0 * CAPACITY_KWH / EFF
                power = min(config.MAX_POWER_KW * intensity, headroom_kwh / DT_H, config.MAX_POWER_KW)
                power = float(round(max(power, 0.0), 1))
                if power < 1.0:
                    chosen, power = 1, 0.0
            if power > config.MAX_POWER_KW + 1e-9:
                violations.append({"nodeId": nd["id"], "constraint": "maxPowerKw", "attempted": ACTIONS[chosen], "applied": ACTIONS[chosen], "detail": f"功率 {power}kW 超过 {config.MAX_POWER_KW}kW，已截断"})
                power = config.MAX_POWER_KW
            r, _, _ = reward_fn(nd["pv"], nd["load"], soc, nd["price"], hour, chosen, power)
            immediate += r
            total_reward += float(qv[chosen])
            actions.append(
                {
                    "nodeId": nd["id"],
                    "action": ACTIONS[chosen],
                    "powerKw": power,
                    "qValue": round(float(qv[chosen]), 4),
                    "reason": self._reason(ACTIONS[chosen], nd, rank[i], len(nodes), violated),
                }
            )
        return {
            "actions": actions,
            "totalReward": round(total_reward, 4),
            "immediateReward": round(immediate, 4),
            "qTable": q_table,
            "constraintsChecked": {"socMin": config.SOC_MIN, "socMax": config.SOC_MAX, "maxPowerKw": config.MAX_POWER_KW, "violations": violations},
        }
