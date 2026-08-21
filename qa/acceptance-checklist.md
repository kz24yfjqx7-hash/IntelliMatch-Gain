# 验收自检清单（qa 维护）

图例：✅ 通过　❌ 失败　⏳ 待运行 / 等依赖　🚫 本环境无法验证（需真机 / docker 权限 / 甲方 backend）

## A. 契约第六部分 12 条链路

| # | 链路 | 验证方式 | 负责模块 | 状态 |
|---|---|---|---|---|
| 1 | `docker compose up -d` 一条命令起全部服务，无需联网 | `qa/check_deploy.sh`（compose config / 挂载 / 内存 / healthcheck）；真实 up 需 docker 权限 + 甲方 backend 镜像 | deploy + 甲 | ✅ 静态检查全过（compose config / 五服务 / 挂载 / 内存 / healthcheck）；真实 `up` 🚫（需 docker 权限 + 甲方镜像） |
| 2 | `admin/admin123` 登录 → token | `frontend/tests/mocks.contract.spec.js`「2.1 认证」+ `stores.spec.js`（MSW 桩）；真实 backend 由甲 | frontend-infra（桩）/ 甲 | 桩 ✅ |
| 3 | 注册设备 DID → 列表可见 → 文档可查 → 验签通过 | `mocks.contract.spec.js`「2.2 DID」 | frontend-infra（桩）/ 甲 | 桩 ✅（MSW）；真实 backend 待甲 |
| 4 | 登记 pv 资产 → 自动分级 → SM3 摘要 → 上链 | `mocks.contract.spec.js`「2.4 资产」；分级算法 `algo-service/tests/test_classifier_risk.py` | frontend-infra（桩）/ algo / 甲 | algo ✅；桩 ✅ |
| 5 | subject 申请读权限 → admin 审批 → 权限生效 | `mocks.contract.spec.js`「2.5 权限」 | frontend-infra（桩）/ 甲 | 桩 ✅（MSW）；真实 backend 待甲 |
| 6 | vpp `issue` → 1003 → high 审计日志 → R01 告警 | `mocks.contract.spec.js`「2.10 调度 + 越权」+「WS mock 推送」；`api.request.spec.js` 1003 toast | frontend-infra / 甲 | request 拦截 ✅；桩 ✅（1003 + high 日志 + R01 告警 + WS audit_alert） |
| 7 | FL 任务启动 → 曲线随 WS 增长 → 每轮梯度哈希上链 | 算法：`test_fedavg.py`（真收敛 / DP / Top-k / 哈希 / 409 / cancel）；桩：`mocks.contract.spec.js`「2.9 联邦学习」 | algo / frontend-infra / 甲 | algo ✅（70/70）；桩 ⏳ |
| 8 | DQN 调度 → 策略 → DeepSeek 解释（断网 cache）→ admin 签名下发 | 算法：`test_dqn.py`、`test_deepseek.py`；桩：「2.10」 | algo / frontend-infra / 甲 | algo ✅；桩 ✅ |
| 9 | tamper → verify intact:false → 链状态断裂点 | `mocks.contract.spec.js`「2.6 存证与篡改」 | frontend-infra（桩）/ 甲 | 桩 ✅（MSW）；真实 backend 待甲 |
| 10 | 第 6 步 traceId 查 `/audit/trace/{traceId}` 完整步骤 | 「2.10」内用固定 traceId 回查 | frontend-infra（桩）/ 甲 | 桩 ✅（MSW）；真实 backend 待甲 |
| 11 | 生成日报 → DeepSeek 自然语言解读 | 算法：`test_deepseek.py::test_audit_narrative_is_chinese_and_uses_context`；桩：「2.7 审计」report.narrative | algo / frontend-infra / 甲 | algo ✅；桩 ✅ |
| 12 | 全程断网，仅 DeepSeek 降级 cache | `test_deepseek.py`（无 key → cache/rule；live 超时 → 降级）；`no-external-url.spec.js` + `check_frontend_build.sh` 源码/产物无外网 URL | algo / frontend-* | algo ✅；前端 ✅（Pyodide 已删，src/dist 均无外网 URL） |

## B. 安装包规范 §八 验收清单

| # | 项 | 验证方式 | 负责 | 状态 |
|---|---|---|---|---|
| 1 | 干净机器、拔网线 | 真机 | deploy / 总控 | 🚫 需硬件 |
| 2 | 解压 `sudo ./install.sh` | `check_deploy.sh` 静态核九步要点（root / 架构 / 内存 / docker 离线 / 端口 / load+SHA / 随机密钥 / 180s 轮询 / systemd + 六账号）；`bash -n` | deploy | ✅ 静态；真机 🚫 |
| 3 | 全程无联网请求 | 源码扫描（前端 ✅、algo ✅，DEEPSEEK_BASE_URL 仅 .env）；tcpdump 真机 🚫 | 全体 | ✅ 静态 |
| 4 | 2 分钟内安装完成并打印地址与账号 | install.sh 含 180s 轮询与横幅输出 ✅；计时真机 🚫 | deploy | ✅ 静态 |
| 5 | 浏览器 `admin/admin123` 登录 | 见 A2；`pages.spec.js` 以 admin/vpp 登录后挂载 11 页 | 甲 / frontend | 桩 ✅ |
| 6 | 12 条业务链路 | 见 A | 全体 | 桩 ✅；真实 backend 待甲 |
| 7 | reboot 自启（ARM 验证 kiosk 全屏） | `packaging/assets/energy-tds.service`、`energy-tds-kiosk.service` 存在 ✅；真机 🚫 | deploy | ✅ 静态 |
| 8 | `uninstall.sh` 卸载干净 | `uninstall.sh --purge` 存在 + `bash -n` ✅；真机 🚫 | deploy | ✅ 静态 |
| 9 | ARM 包真实树莓派跑一遍 | 真机 | deploy / 总控 | 🚫 |

## C. DEV-PLAN §7 DoD

| 模块 | 项 | 状态 |
|---|---|---|
| algo | `pytest algo-service/tests` 全绿 | ✅ 70/70 |
| algo | `uvicorn main:app` 起得来，health 返回 `{"status":"ok",...}` | ✅ `qa/check_algo_live.sh` 10/10 |
| algo | DQN checkpoint 存在，推理 <200ms | ✅ |
| algo | FL 10 轮 loss 趋势下降 | ✅（修复 MSG-qa-to-algo-001 后） |
| frontend | `npm run build` 零错误 | ✅（P0 已修） |
| frontend | 12 条链路在浏览器走通 | 自动化：11 页 + Banner 在 MSW 下挂载无抛错（`pages.spec.js` + 两份冒烟）；浏览器人工走查 ⏳ |
| frontend | `grep -rn "http://\|https://" src/` 仅剩允许项 | ✅ |
| frontend | `npm test` 全绿 | ✅ 83/83（14 个文件） |
| deploy | `docker compose config` 通过 | ✅（env_file 硬依赖已去除） |
| deploy | `docker build` 两镜像 | 🚫 本机无 docker 权限 |
| deploy | `bash -n` 全部脚本 | ✅ |
| deploy | 安装包目录结构与规范 §三一致 | ⏳ 需实际 build.sh 产物 |
