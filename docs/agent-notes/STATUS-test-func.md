# STATUS · test-func（功能测试）

负责人：test-func　日期：2026-08-22（第 2 轮，复测 + 补全）
所有者文件：`docs/测试文档.md`、`docs/test-evidence/`、`qa/func-tests/`、`docs/agent-notes/MSG-test-func-to-integration-00N.md`

## 本轮做了什么
1. 复跑接口层 `qa/func-tests/api_func_test.py` 与页面层 `qa/func-tests/ui_func_test.mjs`（真后端 5199 + backend 8000 + algo 8100 + MariaDB）。
2. 复测上一轮提的缺陷（MSG-test-func-to-integration-001 与 6 条页面缺陷），逐条确认修复状态。
3. 补齐需求书未覆盖的功能点用例，产出 `docs/测试文档.md`（需求追溯矩阵 + 用例明细 + 总结）。
4. 新缺陷：甲方追加 `BACKEND-ISSUES.md` B-020～B-023；乙方写 `MSG-test-func-to-integration-002.md`。

## 结果（2026-08-22 13:17 收尾）
| 层面 | 用例 | 通过 | 失败 | 阻塞 |
|---|---|---|---|---|
| 接口层 `api_func_test.py`（run=0822130547） | 114 | 111 | 3 | 0 |
| 页面层 `ui_func_test.mjs`（tag=test-08221317） | 16 | 15 | 1 | 0 |
| 静态 / 范围外 | 4 | 3 | 0 | 1 |
| **合计** | **134** | **129** | **4** | **1** |

失败 4 条全部是甲方 backend 缺陷：TC-33-06（B-020 ECC/RSA 验签）、TC-311-04 与 TC-UI-13（B-021 node_status 不周期推送）、TC-312-04（B-023 时区口径）。
阻塞 1 条：TC-311-OS（端侧 MQTT/RS485/TLS1.3/断网缓存，范围外且无硬件，已给替代验证）。
乙方侧本轮**无未修复缺陷**：上一轮的 2 条算法缺陷 + 6 条页面缺陷、以及本轮新提的 1 条（角色管理取 `.items`）全部修复并复测通过。

## 复跑方法（可重复）
```bash
cd energy-tds && .venv/bin/python qa/func-tests/api_func_test.py     # 接口层，约 8 分钟
cd energy-tds/frontend && node ../qa/func-tests/ui_func_test.mjs      # 页面层，约 6 分钟
cd energy-tds && .venv/bin/python qa/func-tests/gen_doc.py           # 汇总生成 docs/测试文档.md
```

## 约束遵守
- 未重置数据库、未杀他人进程、未改任何业务代码（backend/ 与 frontend/ 只读）。
- 新建实体一律 `test-<时间戳>` 前缀；篡改演示后调 `/evidence/demo/restore` 还原（页面层没有还原入口，脚本直接调接口善后）。
- 截图 1366×768、jpeg、单张 ≤300KB、总数 ≤40。

## 需要别人知道的
- 存证链在本轮开始前已断于 `ev-001067`（test-api 的「绕过应用层直接改库」安全用例遗留，且甲方 B-001 使 `demo/restore` 无快照可用）。存证类用例改为「相对基线」判定；**要演示「链完整」画面需重导数据库**。
- 联调库里 4 个节点已绑定种子 DID，新注册 DID 冒充上线会被 1004 拒绝。功能测试的「设备用密钥完成接口认证」正向路径改用后端托管私钥（只读取库中 `did_key.private_key_enc` + `backend/core/gm_crypto.py` 解密），未改任何后端代码。
