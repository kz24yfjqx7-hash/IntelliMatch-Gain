"""
离线训练 DQN 并保存 checkpoint -> models/dqn.npz，训练曲线 -> models/dqn_train_log.json

用法：
    python scripts/train_dqn.py            # 完整训练（默认 3000 episodes，80 核 x86 上数分钟）
    python scripts/train_dqn.py --quick    # 快速模式（300 episodes，服务启动缺 checkpoint 时兜底）
    python scripts/train_dqn.py --episodes 5000 --seed 3
树莓派只做推理，不要在树莓派上运行本脚本。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from algorithms.dqn import save_checkpoint, train  # noqa: E402

QUICK_EPISODES = 300
FULL_EPISODES = 3000


def main(argv=None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=None)
    ap.add_argument("--quick", action="store_true", help="快速模式（小规模训练）")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=config.DQN_MODEL_PATH)
    args = ap.parse_args(argv)
    episodes = args.episodes or (QUICK_EPISODES if args.quick else FULL_EPISODES)
    t0 = time.time()
    q, meta = train(episodes=episodes, seed=args.seed, log_path=config.DQN_TRAIN_LOG_PATH if not args.quick else None)
    meta["train_seconds"] = round(time.time() - t0, 1)
    meta["mode"] = "quick" if args.quick else "full"
    out = save_checkpoint(q, meta, args.out)
    print(f"[train_dqn] 已保存 {out}  episodes={episodes}  平均奖励 {meta['first_avg_reward']:.2f} -> {meta['final_avg_reward']:.2f}  耗时 {meta['train_seconds']}s")
    return out


if __name__ == "__main__":
    main()
