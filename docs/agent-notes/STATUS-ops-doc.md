# STATUS-ops-doc（技术文档工程师）

更新：2026-08-22

## 已完成

- `docs/系统操作手册.md`：《能源可信数据空间平台 · 系统详细操作流程》，7 章 + 附录。章节：系统概述与架构（拓扑/端口/角色权限矩阵/6 账号）→ 三种启动方式（compose / 离线安装包 install.sh 九步与 x86_64-aarch64 差异与 kiosk / 开发模式与 MSW）→ 登录与角色（菜单可见性、1002/1003/1004/1007 提示）→ 11 个页面逐页按钮级操作（含 FL 的 ε/δ/Top-k/投毒/异常告警，调度创建→运行→签名下发→回执完整链路与私钥来源，存证写入→校验→篡改→链状态→证书→restore，权限申请→审批→生效→回收→审计留痕）→ 15 分钟演示剧本（按分钟表）→ 运维排错（健康、建库、算法超时/DeepSeek 三级降级、WS、树莓派内存、密钥、日志、备份重置）→ 附录（接口速查含权限、错误码、环境变量表与 `.env.example`/`backend/core/config.py`/`algo-service/config.py` 一致）。
- `docs/screenshots/`：20 张 1366×768 JPEG（每张 < 140KB），真后端模式 Playwright 实拍；为让截图显示中文/表情，在本机用户目录安装了文泉驿微米黑与 Noto Color Emoji 字体（不在仓库内）。
- 所有菜单/按钮/字段文案与 `frontend/src/views/*.vue`、`router/index.js`、`stores/perspective.js` 核对；接口与权限与 `contract/API-CONTRACT.md` 及后端 `openapi.json`、各 `router.py` 的 `require_permission/require_roles` 核对。
- `deploy/答辩演示剧本.md` 顶部加入手册引用（该文件工作区内还有 deploy 的未提交修改：`/health` 探活，已一并提交）。
- `docs/agent-notes/MSG-ops-doc-to-integration-001.md`：4 个真后端模式界面缺陷（签名下发伪签名 1004、终端页按非契约状态过滤、验签演示伪签名、非管理员角色轮询告警产生 1003 审计污染）。
- `docs/agent-notes/BACKEND-ISSUES.md`：B-001 重复 tamper 覆盖备份导致 restore 失效（实测并手工修复，联调库链状态已恢复 intact）。

## 联调期间对共享环境的操作

- 手册实测产生的数据：DID「手册演示逆变器-01」、资产 #1104、调度任务 dp-000003/dp-000010/dp-000011、FL 任务 fl-000012；已篡改并还原 ev-000272、ev-000203（后者原为 qa 脚本篡改残留），最终 `chain/status intact:true`。
- 未修改 backend/、contract/、前端与算法源码；临时截图脚本已删除。

## 未覆盖 / 待他人

- 真 SM2 签名下发与终端回执的界面截图：受 MSG-001 #1/#2 影响，手册用 curl 给出绕行；修复后请补截图 17/21 并删除手册中「当前已知差异」段落。
- 树莓派 aarch64 安装流程为按脚本与 STATUS-deploy 描述，未在真机实测。
