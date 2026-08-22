# STATUS · test-api（接口 / 数据库 / 安全测试）

**更新**：2026-08-22　**状态**：完成。

## 交付物
- `docs/测试文档-接口与安全.md`：追溯矩阵（文档功能点→用例）、275 条用例明细（编号/依据/步骤/预期/实际/判定/证据）、缺陷清单、汇总表、安全结论。
- `qa/api-tests/`：`run_all.py`（2.1~2.12 全接口 + 安全 + DB）、`ws_test.py`、`perf.py`、`degrade_test.py`（5300/5301 隔离副本验 2001 与 DeepSeek 降级）、`gen_doc_tables.py`、`README.md`、`results/*.json`。可复跑，不改业务代码。
- 缺陷：甲方 `docs/agent-notes/BACKEND-ISSUES.md` B-001~B-012（B-009 为信息项）；乙方 `MSG-test-api-to-integration-001.md`（P2）。

## 结果
275 条：通过 262 / 失败 13 / 阻塞 0。失败全部对应缺陷单；主要为 B-001（require_roles 拒绝无审计）、B-002（subject 可读他人存证）、B-006（node_status 无周期推送）、B-010（并发存证丢失）、B-011（loss 列溢出）。76 个 `/api/v1` 接口全部覆盖正向+异常；存证链 900+ 条 SM3 逐条重算零断链。

## 对环境的影响（已声明）
- 期间在 5300（backend 副本）/5301（algo 副本）短暂起过进程，测试完已结束。
- Node-D 绑定的 edge DID 做过一次 `rotate-key`（custody=true，托管仍可用，version 变为 2+）。
- 其他 Agent 未还原的 tamper 演示（ev-000203/236/272/480/709 等）被我的脚本通过 `/evidence/demo/restore` 还原过，以保证链完整性用例可判；若 test-func 需要保持断链演示请自行重新 tamper。
- 新建 `test-` 前缀的用户/DID/资产/角色/任务若干，未删除；未重置数据库，未杀任何进程。

## 未覆盖
MQTT/ESP32 设备接入、180 天留存策略、R05 算法异常告警（需投毒样本）、安装包（无 docker 权限，test-deploy 覆盖）。
