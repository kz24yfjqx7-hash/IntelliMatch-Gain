# 算法服务镜像：python:3.11-slim，numpy/fastapi/uvicorn/httpx 均有 amd64 与 aarch64 wheel
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷源码（含 models/dqn.npz、data/node_datasets.npz、cache/deepseek_cache.json）
COPY . .

# 兜底：若构建上下文里缺少模型/数据/缓存，则在镜像内生成（树莓派应避免走到这一步，build.sh 会保证文件存在）
RUN test -f data/node_datasets.npz || python scripts/gen_datasets.py; \
    test -f models/dqn.npz || python scripts/train_dqn.py --quick; \
    test -f cache/deepseek_cache.json || python scripts/warm_cache.py

# 非 root 运行
RUN useradd -m -u 10001 algo && chown -R algo:algo /app
USER algo

EXPOSE 8100
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${ALGO_PORT:-8100}"]
