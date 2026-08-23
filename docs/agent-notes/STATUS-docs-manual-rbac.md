# STATUS · docs-manual-rbac

- **Agent**：docs-manual-rbac
- **日期**：2026-08-23
- **任务**：围绕"不同身份登录后看到/能做的事不同"，在操作手册补一段分角色导览。
- **状态**：✅ 完成

## 改动清单（仅限授权的两个文件）

1. `docs/系统操作手册.md`
   - **新增 §3.4「分角色登录导览（答辩按角色切换用）」**（置于 §3.3 之后、§4 之前）。为 6 个演示账号各写一段"落地页 + 侧栏能看到/明显看不到什么 + 标志性操作 + 一句演示建议"，末尾附一行"快速记忆"，并声明"以 §3.3 三张矩阵为准"，不重复整表。
   - **§5 剧本 3 处点睛式补充**（未重写剧本）：
     - 第 5 步越权拦截：注明"建议用 vpp（有 dispatch:read 能进页却无 dispatch:issue）"；
     - 第 9 步审计追踪：注明"审计只 admin/regulator 有菜单，可改用 regulator 突出独立监管视角"；
     - 第 7 步下发：注明"回执半段可另开 B 浏览器用 edge 演示边端接收执行"。
   - TOC（§0 目录）只列一级章节 0–8、无子节，故未改；§3.3、§4 既有内容未改。
2. `docs/agent-notes/STATUS-docs-manual-rbac.md`（本文件，新建）。

## 6 账号导览一句话要点

- **admin**：全能 + 管用户 + 唯一同时有审计与用户管理；当"平台管理员/主持人"号串起整条剧本。
- **grid**：能签名下发调度、能导出，但不能登记资产、无审计；当"合法下发方"与 vpp 越权形成对照。
- **vpp**：能登记资产、能进调度页却不能下发/跑算法/导出；招牌越权号，演示 1003 拦截 + R01 告警（+1004 冒名）。
- **subject**：菜单明显更少（无云端聚合/边端联邦与执行页），资产与存证均 own；演示"数据默认不共享 → 申请授权"。
- **regulator**：全程只读 + 专享审计中心；当"独立监管视角"做 traceId 全流程追溯、审计报告、导出 CSV。
- **edge**：登录直进边端视角、无「区块链存证」菜单；当"边端设备接收执行"做终端校验与回执。

## 文档 ↔ 代码一致性核对（只报告，未改任何代码或 §3.3）

逐条比对 `backend/sql/02_seed.sql` 的 `sys_role_permission` 种子（第 20–55 行）与手册 §3.3 三张矩阵，**完全一致，未发现不一致**：

- sys_admin：asset r/w/export、model r/w、dispatch r/issue、evidence r/w、algo execute、user manage —— 与"全能 + 管用户"一致。
- grid_dispatcher：asset read/export、model read、dispatch read/issue、evidence read、algo execute；**无 asset:write** —— 与"能下发不能登记"一致。
- vpp_operator：asset read/write、model read、dispatch read、evidence read；**无 dispatch:issue / algo:execute / asset:export** —— 与"能登记不能下发、越权演示"一致。
- energy_subject：asset read/write **own**、evidence read **own**，其余无 —— 与"只碰自己 + 申请授权、看不到调度/联邦页"一致。
- regulator：asset read/export、model read、dispatch read、evidence read（+ 审计为角色级门控）—— 与"只读 + 专享审计"一致。
- edge_node：asset read/write **own**、model read、dispatch read；**无 evidence:read** —— 与"边端设备、无存证、无审计"一致。

审计中心角色级门控（admin/regulator）、视角切换对所有角色开放、edge 默认边端视角等描述与 §3.3 说明及界面分区（§3.2）均一致。
