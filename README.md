# 能源可信数据空间平台（energy-tds）

面向虚拟电厂云边端场景的可信数据空间：DID 身份接入 → 数据资产登记与分级 → 权限控制 → 隐私计算（联邦学习 + 差分隐私）→ DQN 智能调度 → 本地哈希链存证与全链路审计。


## 目录

```
energy-tds/
├── contract/            API-CONTRACT.md、DB-SCHEMA.md
├── algo-service/        算法服务（FastAPI + NumPy）：FedAvg/DP/Top-k、DQN 调度、分类分级、风险评估、DeepSeek 适配
├── frontend/            前端（Vue3 + Vite + Element Plus + ECharts），内置 MSW 假后端作为离线兜底
├── deploy/              compose 部署说明、nginx 配置、单架构离线镜像包脚本、答辩演示剧本
├── packaging/           双架构（x86_64 / arm64）完全离线一键安装包：build.sh / install.sh / uninstall.sh
├── qa/                  集成与契约测试、验收报告
├── docs/                DEV-PLAN.md 总纲、agent-notes/ 协作记录、legacy/ 原始需求
├── docker-compose.yml   五服务编排（mysql / redis / backend / algo-service / frontend）
├── docker-compose.dev.yml 独立联调（不依赖 backend）
├── .env.example         环境变量模板（变量名见 contract/API-CONTRACT.md 第四部分）
└── Makefile             常用命令封装
```

服务拓扑（只有 frontend 对外暴露端口）：

```
浏览器 ──HTTP/WS──> frontend(nginx :80) ──/api/ /ws──> backend:8000 ──> algo-service:8100
                                                          ├──> mysql:3306
                                                          └──> redis:6379
```

## 三种运行方式

### 1. 本地开发

```bash
# 算法服务（.venv 已装 numpy/fastapi/uvicorn/httpx/pytest）
cd algo-service && ../.venv/bin/uvicorn main:app --reload --port 8100
curl localhost:8100/algo/v1/health

# 前端（MSW 假后端模式，不需要 backend）
cd frontend && VITE_USE_MOCK=true npm run dev        # http://localhost:3000
npm test                                             # vitest
```

### 2. docker compose（合并 backend/ 之后）

```bash
cp .env.example .env
docker compose up -d --build
# http://localhost  →  admin / admin123
```

没有 `backend/` 时用 `docker compose -f docker-compose.dev.yml up -d --build`（仅 algo-service；加 `--profile mock` 再起 MSW 前端）。详见 `deploy/部署说明.md`。

### 3. 离线安装包（目标机无 Docker、无网络）

```bash
# 开发机（联网、Docker 20.10+、磁盘 ≥ 25GB）一次性准备：
docker run --privileged --rm tonistiigi/binfmt --install all
docker buildx create --name energy-builder --use --bootstrap
# 构建（需 backend/ algo-service/ frontend/ 三目录齐全；演练可加 --skip-backend）
sudo ./packaging/build.sh --arch all          # 产物 dist/energy-tds-v1.0-linux-{x86_64,arm64}.tar.gz
# 目标机：
tar -xzf energy-tds-v1.0-linux-x86_64.tar.gz && cd energy-tds-v1.0-linux-x86_64 && sudo ./install.sh
```

详见 `packaging/部署说明.md` 与 `docs/legacy/安装包规范.md`。

## 演示账号（种子数据）

| 用户名 | 密码 | 角色 |
|---|---|---|
| `admin` | `admin123` | 系统管理员 |
| `grid` | `grid123` | 电网调度方 |
| `vpp` | `vpp123` | 虚拟电厂运营商 |
| `subject` | `subject123` | 能源主体 |
| `regulator` | `reg123` | 监管方 |
| `edge` | `edge123` | 边缘节点 |


## 常用命令（Makefile）

```bash
make env            # 生成 .env
make up / down      # compose 起停
make dev-up         # 独立联调
make build-images   # 本机构建 algo-service + frontend 镜像
make check          # bash -n + shellcheck + compose config
make package        # 双架构离线安装包（sudo）
```
