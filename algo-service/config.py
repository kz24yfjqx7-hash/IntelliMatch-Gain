"""
算法服务全局配置。

所有可变参数均从环境变量读取（由根目录 .env / docker-compose 注入），
此处仅给出默认值。**源码中不得出现外网 URL**，因此 DEEPSEEK_BASE_URL 默认为空字符串，
实际地址由 .env 提供（见 contract/API-CONTRACT.md 第四部分）。
"""
import os
from pathlib import Path

# ---- 路径 ----
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MODEL_DIR = BASE_DIR / "models"
CACHE_DIR = BASE_DIR / "cache"

NODE_DATASET_PATH = DATA_DIR / "node_datasets.npz"
DQN_MODEL_PATH = MODEL_DIR / "dqn.npz"
DQN_TRAIN_LOG_PATH = MODEL_DIR / "dqn_train_log.json"
DEEPSEEK_CACHE_PATH = CACHE_DIR / "deepseek_cache.json"

# ---- 服务 ----
ALGO_PORT = int(os.getenv("ALGO_PORT", "8100"))
SERVICE_VERSION = "1.0.0"

# ---- DeepSeek（三级降级：live -> cache -> rule）----
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
# 默认空：不在源码里写外网地址，由 .env 的 DEEPSEEK_BASE_URL 提供
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "").strip().rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_TIMEOUT = float(os.getenv("DEEPSEEK_TIMEOUT", "8"))
DEEPSEEK_OFFLINE_FALLBACK = os.getenv("DEEPSEEK_OFFLINE_FALLBACK", "true").lower() in ("1", "true", "yes")
# live 回答缓存条目上限（超出淘汰最旧的精确 key 条目，场景兜底 key 永不淘汰），防止缓存文件无限增长
DEEPSEEK_CACHE_MAX = int(os.getenv("DEEPSEEK_CACHE_MAX", "2000"))

# ---- 联邦学习 ----
# 每轮之间的停顿秒数，让前端看到曲线逐步增长；qa 测试时设为 0
FL_ROUND_DELAY = float(os.getenv("FL_ROUND_DELAY", "1.0"))
# 隐私预算耗尽时是否熔断停止训练（默认 true）。
# 关掉则保持旧行为：继续跑满轮次、继续加噪与累计 ε（只用于对比演示，不符合 DP 语义）。
FL_STOP_ON_BUDGET_EXHAUSTED = os.getenv("FL_STOP_ON_BUDGET_EXHAUSTED", "true").lower() not in ("0", "false", "no")
# 本地训练超参
FL_LOCAL_EPOCHS = int(os.getenv("FL_LOCAL_EPOCHS", "3"))
FL_LOCAL_LR = float(os.getenv("FL_LOCAL_LR", "0.05"))
FL_BATCH_SIZE = int(os.getenv("FL_BATCH_SIZE", "32"))
# DP 梯度裁剪阈值 C：自适应裁剪（各节点更新范数中位数）的**上限**。
# 不封顶会形成正反馈：噪声 ∝ C，噪声把权重撑大 → 下一轮更新范数变大 → C 变大 → 噪声更大，
# 单节点 + DP 实测 6 轮内 C 涨 90 倍、loss 从 0.2 发散到 9×10⁷。
DP_CLIP_NORM = float(os.getenv("DP_CLIP_NORM", "1.0"))
# 训练发散熔断：某轮测试 loss 非有限，或连续 FL_DIVERGE_PATIENCE 轮 > 历史最优 loss × FL_DIVERGE_FACTOR，
# 判定 anomaly=training_diverged 并停止（默认开）。典型诱因是节点太少/ε 太小导致噪声远大于更新。
FL_STOP_ON_DIVERGENCE = os.getenv("FL_STOP_ON_DIVERGENCE", "true").lower() not in ("0", "false", "no")
FL_DIVERGE_FACTOR = float(os.getenv("FL_DIVERGE_FACTOR", "20"))
FL_DIVERGE_PATIENCE = int(os.getenv("FL_DIVERGE_PATIENCE", "2"))
# 本地学习率衰减：第 t 轮 lr_t = FL_LOCAL_LR / (1 + FL_LR_DECAY·(t−1))，抑制 Non-IID 客户端漂移
FL_LR_DECAY = float(os.getenv("FL_LR_DECAY", "0.3"))
# 投毒检测：节点更新与其余节点更新的平均余弦相似度低于该阈值判为可疑。
# 正常 Non-IID 节点在收敛后期均值约在 -0.3~+0.9，翻转投毒约为 -0.97，取 -0.5 兼顾召回与误报
POISON_COS_THRESHOLD = float(os.getenv("POISON_COS_THRESHOLD", "-0.5"))
# 任务字典保护：最多保留的任务数（超出时淘汰最旧的终态任务）、终态任务 TTL 秒（0=不按时间清理）、最大并发训练数
FL_MAX_JOBS = int(os.getenv("FL_MAX_JOBS", "200"))
FL_JOB_TTL = float(os.getenv("FL_JOB_TTL", "3600"))
FL_MAX_RUNNING = int(os.getenv("FL_MAX_RUNNING", "16"))

# ---- DQN 调度约束（与契约 constraintsChecked 字段一致）----
SOC_MIN = 20.0
SOC_MAX = 95.0
MAX_POWER_KW = 30.0
