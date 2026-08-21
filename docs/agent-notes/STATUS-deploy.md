# STATUS-deploy

## 已完成（2026-08-21）

| 文件 | 说明 |
|---|---|
| `docker-compose.yml` | 五服务；mysql(8.0, healthcheck, `./backend/sql`→initdb, `./deploy/mysql/mysql.cnf`, 命名卷)、redis(7-alpine)、backend(build ./backend, depends_on mysql/redis healthy, env 全部来自 .env)、algo-service(urllib 探 `/algo/v1/health`)、frontend(`${FRONTEND_PORT}:80`)；`image: energy-tds/<svc>:1.0` + `build:` 同写；内存上限 512M/96M/300M/400M/64M |
| `docker-compose.dev.yml` | 乙方独立联调：默认仅 algo-service；`--profile mock` 再起 `VITE_USE_MOCK=true` 前端 |
| `.env.example` | 严格照 contract 第四部分变量名与默认值 + 中文注释 |
| `deploy/nginx.conf` = `frontend/nginx.conf` | gzip、SPA `try_files`、`/api/`→`backend:8000`（保留前缀，透传 Authorization / X-Trace-Id）、`/ws` Upgrade + 3600s、`client_max_body_size 20m`、`/healthz`；upstream 用变量 + `resolver 127.0.0.11` 惰性解析，backend 不存在时 nginx 也能启动（MSW 模式需要） |
| `frontend/Dockerfile` + `.dockerignore` | `--platform=$BUILDPLATFORM node:20-alpine` builder（npm ci / install 回退）→ `nginx:alpine`；build-arg `VITE_API_BASE/VITE_WS_BASE/VITE_USE_MOCK`(默认 false) |
| `algo-service/.dockerignore` | 排除 tests / __pycache__ / .venv 等 |
| `deploy/mysql/mysql.cnf` | 标准版（= `packaging/assets/mysql-amd64.cnf`） |
| `deploy/build-offline.sh` / `load-offline.sh` | 单架构快速离线镜像包（`--skip-backend`、`--mock`） |
| `deploy/部署说明.md`、`deploy/答辩演示剧本.md` | compose 部署 + 常见问题；十步剧本（操作 / 预期 / 话术 / MSW 兜底 / 应急速查） |
| `packaging/build.sh` | 十步照规范 §2.3：`--arch amd64|arm64|all`、`--skip-backend`、`--skip-dqn-train`、`--dry-run`、`--version`、`--db-image`；第 2 步 arm64 验证（buildx imagetools，无 docker 时退回 Docker Hub registry API）失败自动整体切 `mariadb:11`；第 3 步 `.venv/bin/python scripts/train_dqn.py`（仅 dqn.npz 缺失时，否则容器内训练）；第 5 步 `buildx --output type=docker,dest=`；第 6/7 步串行 pull→save→rmi；第 8 步下载 docker 27.5.1 静态包 + compose 2.32.4（缓存于 build/downloads）；第 9 步组装（包内 compose 自动去掉 build 块、路径改 `./sql` `./config/mysql.cnf`、镜像名替换）；第 10 步 tar.gz + sha256 + VERSION（含各镜像 SHA256） |
| `packaging/install.sh` | 九步照规范 §四：root / `armv7l` 拒绝 + 架构匹配 / 内存 3.5GB 磁盘 8GB / 离线装 Docker + compose 插件 / 端口占用列进程（80 致命，其余内部端口仅警告）/ SHA256SUMS 校验 + docker load / 随机 `MYSQL_ROOT_PASSWORD` `MYSQL_PASSWORD` `JWT_SECRET`（重装时保留旧 .env 以兼容数据卷）/ ARM swap 2G / compose up + 180s 轮询 `/api/v1/health`（超时打印 ps + logs --tail=50）/ systemd enable + arm64 kiosk 单元 / 成功横幅 |
| `packaging/uninstall.sh` | 默认保留数据卷与 .env；`--purge [-y]` 全删 |
| `packaging/assets/` | `mysql-amd64.cnf`、`mysql-arm64.cnf`（performance_schema OFF、buffer_pool 128M 等）、`energy-tds.service`、`energy-tds-kiosk.service`（`__KIOSK_USER__` 由 install 替换、等健康后拉 chromium `--kiosk --disable-gpu-compositing`）、`docker.service` |
| `packaging/部署说明.md` | 安装包用户手册 |
| `README.md`、`Makefile` | 项目总览 / 三种运行方式 / 演示账号；`make check|up|dev-up|package|offline` |

## 更新（2026-08-21 第二轮）
- 修复 qa 缺陷单 MSG-qa-to-deploy-001（P2）：删除 backend 的 `env_file: .env` 硬依赖；`docker compose config -q` 在 无 .env / 有 .env / `--env-file` / 包内 compose 四种情况均通过。已在缺陷单末尾回复。
- 落实 algo 回复（MSG-deploy-to-algo-001 / MSG-algo-to-all-001）：
  - `FL_ROUND_DELAY`：两个 compose 的 algo-service 增加 `FL_ROUND_DELAY: ${FL_ROUND_DELAY:-1.0}`（algo 内部变量、有默认值，**不写入 .env.example**，保持契约第四部分变量集合不变；qa 跑测试可 `FL_ROUND_DELAY=0 docker compose up`）。
  - cache 目录对 uid 10001 可写：compose 不挂载任何卷到 algo-service，`cache/` 在镜像内且已 `chown algo`，无需额外处理；打包脚本同样不挂载。
  - 保留仓库内训练好的 `models/dqn.npz`：build.sh 第 3 步仅在文件缺失时训练，`.dockerignore` 未排除 `models/ data/ cache/`，完整 3000-episode 产物随 `COPY . .` 进镜像；镜像内 `--quick` 兜底只在缺文件时触发。

## 自测结果

- `docker compose config -q`：**通过**（backend/ 目录不存在，config 仍通过——build context 只在 build 时校验）；`docker-compose.dev.yml` 同样通过。已用临时目录验证 build.sh 第 9 步生成的「包内 compose」（去 build 块、mariadb 替换）也能 `config -q` 通过。测试用 `.env` 已删除。
- `bash -n`：deploy/*.sh、packaging/*.sh 全部通过。**shellcheck 本机未安装**，`make check` 在有 shellcheck 的机器会自动跑。
- `packaging/build.sh --arch all --dry-run --skip-backend`：十步全部打印，流程正确；不加 `--skip-backend` 时在第 1 步明确报「缺少 backend/ 目录（甲方代码）」退出。dry-run 下缺 docker 权限 / 缺文件仅警告。
- `install.sh --help` / `uninstall.sh --help` 正常。

## arm64 基础镜像验证结论

本机用户无 docker.sock 权限（不在 docker 组、sudo 需密码），改用 Docker Hub registry API（与 build.sh 第 2 步的退路一致）查询 manifest list，**六个镜像均含 `linux/arm64`**：

| 镜像 | arm64 |
|---|---|
| mysql:8.0 | ✅ |
| redis:7-alpine | ✅ |
| python:3.11-slim | ✅ |
| nginx:alpine | ✅ |
| node:20-alpine | ✅ |
| mariadb:11（备选） | ✅ |

结论：**维持 mysql:8.0**，无需切 mariadb。（build.sh 仍保留自动切换逻辑以防上游变动。）
为兼容可能的 mariadb 切换，compose 的 mysql `command` 不使用 `--default-authentication-plugin`（mariadb 不识别）；请甲方 backend 的 MySQL 驱动支持 `caching_sha2_password`（pymysql + cryptography 即可）。

## 阻塞 / 待办

1. **docker build 验证未做**：当前 shell 用户 `stu` 不在 `docker` 组且 `sudo` 需要密码，`docker info` 被拒。`algo-service/Dockerfile` 已就绪（已人工审阅，与约定一致）；`frontend` 待 `npm run build` 零错误（frontend-infra 进行中）。请总控 `sudo usermod -aG docker stu`（或提供 sudo）后再叫我，届时执行 `docker compose build algo-service frontend` 与 `packaging/build.sh --arch amd64 --skip-backend` 实打。
2. 正式打包需甲方 `backend/`（含 `Dockerfile`、`sql/01_schema.sql`、`02_seed.sql`、`GET /api/v1/health`）合并后执行。
3. 真机验收（规范 §八）：干净 x86 机器断网安装、树莓派 4B 真机 — 需要硬件。
4. 已发 MSG：`MSG-deploy-to-algo-001.md`（Dockerfile 约定，已确认符合）、`MSG-deploy-to-frontend-infra-001.md`（构建约定：dist 目录、VITE_* 构建期变量、lock 文件一致、mockServiceWorker.js 保留）。

## 对外约定

- 镜像名：`energy-tds/backend:1.0`、`energy-tds/algo-service:1.0`、`energy-tds/frontend:1.0`（MSW 兜底版 `energy-tds/frontend:1.0-mock`）。
- 容器名：`energy-tds-{mysql,redis,backend,algo,frontend}`；网络 `energy-tds-net`；卷 `energy-tds-mysql-data` / `energy-tds-redis-data`。
- 健康探针：backend `GET /api/v1/health`（无 curl 时用 python urllib）、algo `GET /algo/v1/health`、frontend `GET /healthz`（nginx 直接返回）。
- 安装目录 `/opt/energy-tds`；systemd 单元 `energy-tds.service`、`energy-tds-kiosk.service`。
