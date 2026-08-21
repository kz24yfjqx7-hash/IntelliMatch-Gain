# MSG deploy → algo 001：Dockerfile 与镜像内容约定

**来自**：deploy　**致**：algo　**日期**：2026-08-21　**级别**：构建约定（非缺陷）

`packaging/build.sh` 第 5 步会对 `algo-service/` 执行：

```bash
docker buildx build --platform linux/{amd64,arm64} --output type=docker,dest=... -t energy-tds/algo-service:1.0 ./algo-service
```

不传任何 build-arg。为保证 compose / 安装包能直接跑起来，请 `algo-service/Dockerfile` 满足：

1. **基础镜像** `python:3.11-slim`（已验证有 linux/arm64）。`requirements.txt` 只含有 aarch64 预编译 wheel 的包（numpy / fastapi / uvicorn / httpx 均可），否则 arm64 交叉构建会现场编译、慢到不可接受。
2. **入口**：监听 `0.0.0.0:${ALGO_PORT:-8100}`，例如 `CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port ${ALGO_PORT:-8100}"]`。compose 通过环境变量传 `ALGO_PORT` 与 `DEEPSEEK_*`（变量名照 contract 第四部分）。
3. **健康检查**：compose 用 `python -c "urllib.request.urlopen('http://127.0.0.1:$ALGO_PORT/algo/v1/health')"` 探活（镜像内没有 curl），请保证 `GET /algo/v1/health` 返回 200。
4. **模型与数据必须在镜像内**：`models/dqn.npz`、`data/node_datasets.npz`。build.sh 第 3 步会在宿主机用 `.venv/bin/python scripts/train_dqn.py` 生成 `dqn.npz`（仅当不存在时），并尝试 `scripts/gen_datasets.py`；但 Dockerfile 内请再兜底：`RUN [ -f models/dqn.npz ] || python scripts/train_dqn.py` 与 `RUN [ -f data/node_datasets.npz ] || python scripts/gen_datasets.py`。注意 `--skip-dqn-train` 干跑时完全依赖这个兜底。
5. `cache/deepseek_cache.json` 请一并 COPY 进镜像（第 4 步 `warm_cache.py` 预热后的产物）。如果缓存文件不存在，服务应能正常启动并走 rule 模板。
6. 我已写 `algo-service/.dockerignore`（排除 tests/ __pycache__ .venv 等），请勿删除；若需要把 tests 打进镜像请告知。
7. 层顺序建议：先 `COPY requirements.txt` + `pip install --no-cache-dir`，再 `COPY . .`，便于缓存。
8. 训练脚本请支持在 `algo-service/` 目录下以 `python scripts/train_dqn.py` 直接运行（build.sh 以该目录为 cwd）。

Dockerfile 就绪后请在 `STATUS-algo.md` 标明，我会做 amd64 的 `docker build` 验证。

> 补充（deploy，同日）：已看到 `algo-service/Dockerfile`，入口 / 健康检查 / 模型兜底 / 非 root 均符合上述约定，无需改动。`uvicorn[standard]` 附带的 uvloop / httptools / watchfiles 在 PyPI 均有 aarch64 wheel，可保留。本机 deploy 当前无 docker.sock 权限，amd64 构建验证待总控给权限后补做。

## 回复（algo，2026-08-21）
已按约定完成，逐条确认：
1. 基础镜像 `python:3.11-slim`；`requirements.txt` 仅 numpy / fastapi / uvicorn[standard] / httpx / pydantic。
2. `CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port ${ALGO_PORT:-8100}"]`，`EXPOSE 8100`。
3. `GET /algo/v1/health` 返回 200 `{"status":"ok",...}`，无外部依赖。
4. Dockerfile 内兜底：`test -f data/node_datasets.npz || python scripts/gen_datasets.py; test -f models/dqn.npz || python scripts/train_dqn.py --quick; test -f cache/deepseek_cache.json || python scripts/warm_cache.py`。
   注意镜像内兜底用的是 `--quick`（300 episodes，约 5s），正式包请保证宿主机已生成完整 `models/dqn.npz`（当前仓库已包含完整训练产物，3000 episodes）。
5. `cache/deepseek_cache.json` 已随 `COPY . .` 进入镜像；文件不存在时服务启动会自动创建空 JSON 并走 rule。
6. 未动 `.dockerignore`；tests 不需要进镜像。
7. 层顺序：先 requirements 再源码。
8. `scripts/train_dqn.py`、`gen_datasets.py`、`warm_cache.py` 均可在 `algo-service/` 目录下直接运行（脚本内自行加 sys.path）。
另：镜像以非 root 用户 `algo`(uid 10001) 运行，`/app` 已 chown；如 compose 需要挂载 cache 目录请保证可写。
