# STATUS-test-deploy（部署与安装包脚本沙箱实测）

日期：2026-08-21　执行者：test-deploy　范围：`packaging/**`、`deploy/**`、两个 compose、`frontend/Dockerfile` + `nginx.conf`、`algo-service/Dockerfile`、systemd 单元、部署文档。

## 0. 测试方法（本机无 docker.sock 权限、无 sudo）

一条命令可重复跑全部场景：

```bash
bash qa/deploy-sandbox/run.sh          # 39 个场景，末尾 PASS/FAIL 表；任一 FAIL 退出码 1
bash qa/deploy-sandbox/run.sh -v       # 失败时附带该场景完整输出
```

- `qa/deploy-sandbox/fakebin/`：假 `docker / systemctl / free / df / uname / ss / curl / wget / id / hostname / sleep / groupadd / getent`，记录每次调用到 `calls.log`，行为由 `FAKE_*` 环境变量控制（架构、内存、磁盘、端口占用、docker 是否装/守护进程是否起、compose 是否装、健康检查第几次通过、load/up/build 失败、imagetools 有无 arm64 …）。`tar / gzip / sha256sum / openssl / sed / install` 用真的。`docker compose config` 不需要守护进程，假 docker 遇到 `compose config` 直接转发给真 `/usr/bin/docker`。
- 为了让脚本在非 root 下可测，给 `install.sh / uninstall.sh / build.sh` 加了 **仅用于测试的路径覆盖变量**（默认值不变，正常安装无感知）：`ENERGY_TDS_INSTALL_DIR`、`ENERGY_TDS_SYSTEMD_DIR`、`ENERGY_TDS_BIN_DIR`、`ENERGY_TDS_CLI_PLUGIN_DIRS`、`ENERGY_TDS_DPHYS_SWAPFILE`、`ENERGY_TDS_HEALTH_TIMEOUT`、`ENERGY_TDS_BUILD_DIR`、`ENERGY_TDS_DIST_DIR`。
- build.sh 在沙箱里的「源码树」上跑（只复制打包需要的文件，外加一个假 `backend/`），**没有在真仓库里创建 backend/**（甲方目录）。
- `qa/deploy-sandbox/check_compose.py`：用 PyYAML 对两个 compose 做结构化断言（服务名/容器名/镜像名/端口/healthcheck/depends_on condition/内存上限/卷/网络/环境变量 ⊆ 契约第四部分/.env.example 默认值 = 契约）。

### 场景与结果（39 / 39 PASS）

| 组 | 场景 | 结果 |
|---|---|---|
| A1 | `bash -n` + shellcheck（-S warning，0 告警；shellcheck 0.11 通过 pip `shellcheck-py` 临时装在 scratch 目录） | PASS |
| A2 | compose 严格校验（python yaml ⨯ 契约） | PASS |
| A3 | `docker compose config`：根 compose（无 .env、`--env-file .env.example`）、dev compose、dev `--profile mock` | PASS |
| A4 | `nginx -t`（临时主配置 include，listen 改 18080 避开权限）+ `/api/` 保留前缀、`/ws` Upgrade/Connection、SPA try_files、gzip 类型、每个 location 的安全头、deploy/nginx.conf 与 frontend 一致 | PASS |
| A5 | `systemd-analyze verify` 三个单元 + kiosk.sh 实测（第 3 次健康才过 → 只探 3 次；永不健康 → 退回 /healthz 仍拉起） | PASS |
| A6 | Dockerfile 层顺序、非 root chown、.dockerignore（tests/.venv/node_modules/dist 排除，models/data/cache 不排除）、VITE_USE_MOCK build-arg 链路、`npm ci --dry-run` 通过（lock 与 package.json 一致） | PASS |
| A7 | 文档（packaging/部署说明、deploy/部署说明、答辩剧本、README、STATUS-deploy）出现的 `--xxx` 参数全部在对应脚本中定义 | PASS |
| B1 | `build.sh --arch all` 完整十步（含假 backend）：buildx `--output type=docker,dest=`、frontend build-arg、pull→save→rmi 严格串行顺序（逐行比对）、两架构静态包下载、dist tar.gz + .sha256 可 `sha256sum -c`、tar 顶层目录名、包内结构与规范 §三一致（多余顶层条目 0）、VERSION 字段、images/SHA256SUMS 可校验、包内 compose 无 build 块且 `docker compose config` 通过、ARM/x86 cnf 分别为低配/标准版、二次构建命中下载缓存 | PASS |
| B2 | `--skip-backend --arch amd64`：不构建 backend、VERSION `SKIP_BACKEND=1`、sql/ 空 | PASS |
| B3 | 缺 backend/ 且无 `--skip-backend` → 第 1 步明确报错，不触发任何 build | PASS |
| B4 | 假 imagetools 对 mysql:8.0 返回无 arm64 → 两架构整体切 `mariadb:11`（pull 的是 mariadb、compose 镜像替换、healthcheck 换 `healthcheck.sh`、VERSION 记录、mysql.cnf 无 MariaDB 不识别的裸选项）；`--db-image mysql:8.0` 强制时不切换 | PASS |
| B5 | imagetools 不可用 → 退回 Docker Hub registry API（token + manifest list）判定 | PASS |
| B6 | builder 无 arm64 / 断网 / buildx build 失败 / 磁盘 < 25GB / `--arch mips` 五个失败分支 | PASS |
| B7 | `--dry-run --arch all --skip-backend`：十步全打印、零 docker 调用、零产物 | PASS |
| C1 | 非 root → 提示 `sudo ./install.sh` | PASS |
| C2 | `armv7l` → 第 2 步立即退出，含「Raspberry Pi OS (64-bit)」重刷提示，之后无任何 docker/free 调用 | PASS |
| C3 | aarch64 机器装 x86_64 包 → 提示对应包名 | PASS |
| C4 | 内存 2048MB → 打印实际值与要求 | PASS |
| C5 | 磁盘 5GB → 打印实际值与要求 | PASS |
| C6 | 目标机无 docker → 解 tgz 到 bin、写 docker.service、groupadd、`systemctl enable --now docker`、后续步骤继续并安装完成 | PASS |
| C7 | 有 docker 命令但守护进程未起 → systemctl 拉起，不走离线安装 | PASS |
| C8 | 无 compose 插件 → 从包内装到 cli-plugins | PASS |
| C9 | 80 被 nginx 占用 → 列出 `"nginx",pid=1234` 并退出、不 load；8000/3306 占用 → 列进程仅警告 | PASS |
| C10 | `--port 8080` 绕过 80 占用；.env `FRONTEND_PORT=8080`、健康 URL 与横幅地址带 :8080 | PASS |
| C11 | 篡改 redis.tar.gz → SHA 校验失败、不 load | PASS |
| C12 | `docker load` 失败 → 提示 `df -h /var/lib/docker` | PASS |
| C13 | x86 成功：九步全过；.env 600 权限、三个密钥为 32/32/64 位 hex、无模板默认值、无行尾注释、契约变量齐全；安装目录文件齐全；load 5 个镜像；轮询 `localhost:80/api/v1/health`；energy-tds.service 安装+enable、WorkingDirectory 指向安装目录；无 kiosk、无 swap 操作；横幅含访问地址与六个账号 | PASS |
| C14 | 第二次独立安装 → 三个密钥与 C13 全部不同 | PASS |
| C15 | 重装且旧实例运行中 → 先 `compose down`、保留旧 .env（逐字节相同） | PASS |
| C16 | 健康永不通过 → 恰好轮询 60 次（180s/3s）、打印 `compose ps` + `logs --tail=50`、退出 1、不装 systemd；第 5 次才健康 → 恰好 5 次 | PASS |
| C17 | `compose up` 失败 → 退出并给 logs 命令 | PASS |
| C18 | arm64 成功：dphys-swapfile 100→2048 + `systemctl restart dphys-swapfile`；kiosk 单元 User=pi、ExecStart 指向安装目录 kiosk.sh、enable；swap 已 2048 不改；无 dphys-swapfile 时只警告 | PASS |
| C19 | arm64 `--no-kiosk` / 无 chromium / 找不到桌面用户 → 跳过 kiosk 不失败 | PASS |
| C20 | `--no-start` 不 `compose up` 仍装 systemd | PASS |
| C21 | 演练包：健康 URL 退为 `/healthz`、警告 sql/ 为空 | PASS |
| C22 | `--help` / 未知参数 / 非包目录 | PASS |
| D1 | uninstall 默认：`down --remove-orphans`（无 -v）、删单元、删镜像与网络、保留 .env、其余清理、提示 `--purge` | PASS |
| D2 | `--purge -y`：`down -v`、删整个目录、删 kiosk 单元 | PASS |
| D3 | `--purge` 输入非 yes → 取消且 .env 仍在；非 root 拒绝 | PASS |

## 1. 发现的问题与修复

| # | 问题 | 复现 | 修复 | 验证 |
|---|---|---|---|---|
| 1 | **nginx 安全头在多数 location 里丢失**：`add_header` 不跨层级继承，`location / | /assets/ | /mockServiceWorker.js` 各自写了 `Cache-Control` 后，server 级的 `X-Frame-Options / X-Content-Type-Options` 对这些路径全部失效（即对整个 SPA 失效） | 静态审查 nginx 语义 | 在这三个 location 内重复安全头；server 级增加 `Referrer-Policy`；`/healthz` 改用 `default_type`（`return` 后的 `add_header Content-Type` 不生效）；`deploy/nginx.conf` 同步 | A4：逐 location 断言含两个安全头；`nginx -t` 通过 |
| 2 | **mysql.cnf 含 MySQL 专有变量 `log_error_verbosity`**：build.sh 自动切 `mariadb:11` 时，MariaDB 遇到未知变量会**拒绝启动**，「两架构整体换 mariadb」的保底路径实际上是死的 | B4 场景 | 三份 cnf（amd64/arm64/deploy）改为 `loose-log_error_verbosity`（未知变量降级为警告）；其余选项逐个核对均为 MariaDB 兼容 | B4 断言包内 cnf 无裸 `log_error_verbosity` |
| 3 | **mariadb 切换时 healthcheck 仍用 `mysqladmin ping`**：mariadb:11 中 `mysql*` 别名已弃用（后续版本移除），健康检查可能永远不过 → backend 永不启动 | B4 | `gen_pkg_compose` 在 `DB_IMAGE=mariadb:*` 时把 healthcheck 换成镜像自带的 `healthcheck.sh --connect --innodb_initialized`；同时在生成后断言包内 compose 不含 `build:`（防 awk 失配静默出错） | B4 断言 |
| 4 | **kiosk 单元把 shell 逻辑写在 `ExecStart=/bin/bash -c '… $(command -v …) …'`**：systemd 对 `$` 有自己的展开规则，`$(…)`、`$i` 在不同版本行为不一致，且难以测试；还引用了用户会话级 `PartOf=graphical-session.target`（系统单元里无此 target）；只等 `/healthz`（nginx 起来 ≠ 平台可用） | 静态审查 + systemd-analyze | 逻辑抽到 `packaging/assets/energy-tds-kiosk.sh`（等 `/api/v1/health` 最长 5 分钟 → 退回 `/healthz` → 仍拉起浏览器；URL/等待时间可由单元 `Environment=` 调整）；单元改为 `ExecStart=/bin/bash /opt/energy-tds/kiosk.sh`，去掉 `PartOf`；build.sh 把脚本放进 arm64 包 `systemd/`，install.sh 装到 `/opt/energy-tds/kiosk.sh` | A5 实测 kiosk.sh 两条路径；C18 断言单元内容；`systemd-analyze verify` 通过 |
| 5 | install.sh 第 3 步内存读 `/proc/meminfo`，错误信息写「≥ 3584MB」但判定阈值是 3500 | 静态审查 | 改为 `free -m`（无 free 时退回 /proc/meminfo），信息统一为「≥ 3500MB（约 3.5GB）」 | C4 |
| 6 | install.sh 第 3 步 `df /var/lib` 失败时 `DOCKER_DISK_GB` 可能为空串 → `[[ "" -lt 8 ]]` 误判磁盘不足 | 静态审查 | 空值回退为 `/opt` 的值，比较加 `:-0` | C5 |
| 7 | install.sh 的 ARM swap 分支 `cur=$(grep … \|\| echo 0)` 在 pipefail 下会得到「\n0」之类脏值；非 Raspberry Pi OS（无 dphys-swapfile）时静默跳过 | 静态审查 | 改 `|| true` + `${cur:-0}`；无 dphys-swapfile 时打印 WARN 建议手动保证 swap ≥ 2GB | C18 三个子场景 |
| 8 | install.sh 第 9 步 `systemctl enable` 失败被 `>/dev/null 2>&1` 吞掉，仍打印「已启用」 | 静态审查 | 失败时 WARN 并给出手动命令；energy-tds.service 的 `WorkingDirectory` 随安装目录替换 | C13 |
| 9 | install.sh 离线装 docker 后未 `hash -r`，同一 shell 内 `command -v docker` 可能仍指向旧缓存 | C6 | 加 `hash -r` | C6 |
| 10 | uninstall.sh 非 purge 清理用 `find -mindepth 1 ! -name .env -exec rm -rf` 会对已删目录继续下钻（报错被吞） | 静态审查 | 加 `-maxdepth 1` | D1 |
| 11 | 文档：packaging/部署说明.md 包内结构与 /opt 结构未列 `kiosk.sh` | A7/人工 | 补充，并说明 kiosk 等待逻辑与 `journalctl -u energy-tds-kiosk` | 人工 |

未改动但已确认正确的点：`/api/` 用无 URI 的 `proxy_pass $backend_upstream` 保留前缀；`/ws` Upgrade/Connection map；algo/backend healthcheck 用 `python -c urllib`（python:slim 无 curl）、frontend 用 busybox `wget`；mysql healthcheck 的 `$${MYSQL_ROOT_PASSWORD}` 在容器内展开；backend 无 `env_file`；compose 引用变量 ⊆ 契约（仅豁免带默认值的 `FL_ROUND_DELAY`）；`.env.example` 默认值与契约逐项一致；`.dockerignore` 未排除 models/data/cache；`npm ci --dry-run` 通过。

## 2. 对规范 §三 的两处「超出」

包内比规范多了 `images/SHA256SUMS`（install.sh 第 6 步校验用）和 arm64 包 `systemd/energy-tds-kiosk.sh`；顶层条目集合与规范完全一致。`docs/legacy/安装包规范.md` 为冻结文档未改，在此记录。

## 3. 仍需真 Docker / 真机才能验证的项

1. `docker compose build algo-service frontend` 与 `packaging/build.sh --arch amd64 --skip-backend` 真打（镜像能否构建、frontend `npm run build` 在 node:20-alpine 内零错误、algo 镜像内 `cache/` 对 uid 10001 可写）。
2. arm64 交叉构建（binfmt + buildx）耗时与 QEMU 下 pip wheel 是否全部命中预编译。
3. `mysqladmin ping -p<密码>` 在 mysql:8.0 初始化阶段的行为（临时服务器只开 socket，TCP 探测应失败 → healthy 时机正确），以及 mariadb 退路的 `healthcheck.sh`。
4. 静态 Docker 二进制在干净机器上的依赖：`iptables`/`nftables`、`containerd` 由 dockerd 自起、`docker.service` 的 `Type=notify`。
5. 树莓派真机：4GB 内存预算（Chromium + 五容器）、SD 卡首次建库耗时是否在 180s 内、`dphys-swapfile` 重启、Wayland（Bookworm 默认 labwc）下 `DISPLAY=:0` + `XAUTHORITY` 能否拉起 Chromium（如用 Wayland 需改 `WAYLAND_DISPLAY` / `XDG_RUNTIME_DIR`）。
6. 规范 §八 验收：干净机器断网安装、reboot 自启、`uninstall.sh` 后 `docker ps -a` 无残留。
7. 甲方 `backend/` 合并后的端到端：`GET /api/v1/health` 真实返回、`sql/` 初始化、`caching_sha2_password` 驱动兼容。
