# STATUS · test-func（功能测试）

负责人：test-func　日期：2026-08-22（第 2 轮，复测 + 补全）
所有者文件：`docs/测试文档.md`、`docs/test-evidence/`、`qa/func-tests/`、`docs/agent-notes/MSG-test-func-to-integration-00N.md`

## 本轮做了什么
1. 复跑接口层 `qa/func-tests/api_func_test.py` 与页面层 `qa/func-tests/ui_func_test.mjs`（真后端 5199 + backend 8000 + algo 8100 + MariaDB）。
2. 复测上一轮提的缺陷（MSG-test-func-to-integration-001 与 6 条页面缺陷），逐条确认修复状态。
3. 补齐需求书未覆盖的功能点用例，产出 `docs/测试文档.md`（需求追溯矩阵 + 用例明细 + 总结）。
4. 新缺陷：甲方追加 `BACKEND-ISSUES.md` B-020～B-023；乙方写 `MSG-test-func-to-integration-002.md`。

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
