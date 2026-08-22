# MSG-test-func-to-integration-001：算法服务联邦学习隐私预算耗尽后不停止（P2）+ DQN violations 语义（P3）

来源：test-func 功能测试 TC-38-07 / TC-39-02（qa/func-tests/api_func_test.py），2026-08-22。

## 缺陷 1（P2）隐私预算耗尽后训练继续，ε 持续累计且损失发散
- 复现：`POST /algo/v1/fl/train` rounds=20，`dp: {enabled:true, epsilon:0.05, delta:1e-5}`，3 节点；或通过 backend `POST /fl/tasks` 同参数后 start。
- 期望（需求 3.8 差分隐私 + 任务异常停止）：第 1 轮 accountant 判定 `privacy_budget_exhausted` 后，任务应停止（status=failed 或 cancelled，anomaly 置位），不再继续消耗预算。
- 实际：algo job fl-000011 标记 `anomaly={"type":"privacy_budget_exhausted","round":1,"detail":"第 1 轮累计隐私预算 ε=0.1357 已超过目标 ε=0.05"}` 后继续跑满 20 轮并 `status=success`，epsilonSpent 累计到 0.54（目标 0.05 的 10 倍），loss 发散到 13240（还触发了甲方 DECIMAL(10,6) 溢出，见 BACKEND-ISSUES B-002）。
- 建议：`algorithms/fedavg.py` 在 `accountant.exhausted` 时终止训练并把 job 置 failed（保留 anomaly），或至少从该轮起停止加噪/更新；`FlTrainRequest` 的 epsilon 下限与轮数/噪声标定保持一致，避免第 1 轮即超限。

## 缺陷 2（P3）`constraintsChecked.violations` 记录的是“已修正/保留”的动作，不是违规
- 复现：`POST /algo/v1/dqn/dispatch` nodes soc=18 与 soc=97。
- 实际：返回 `violations:[{constraint:"socMin", attempted:"charge", applied:"charge", detail:"…动作 charge 可行，予以保留"}]`，applied 均合规，却列在 violations 中；契约 3.3 示例在无违规时 `violations: []`。前端「约束」面板据此会显示“违规”。
- 建议：仅在 attempted≠applied（被修正）时记录，或改名为 `constraintEvents` 并在前端按“已修正”展示。

## 缺陷 3（P3）backend `POST /fl/tasks` 未暴露 `simulatePoison`，页面无法演示投毒检测
- 投毒检测只能直接打算法服务验证（TC-38-08 通过）。属甲方接口范围，已在此备案，前端如需演示可在 PrivacyCompute 加说明。

## 回复（integration，2026-08-22）

1. **缺陷 1（P2）隐私预算耗尽不停止 —— 已修复**：`config.py` 新增 `FL_STOP_ON_BUDGET_EXHAUSTED`（默认 true），`main.py:229-235` 在某轮出现 `privacy_budget_exhausted` 时立即终止训练，任务置 `status=failed` 并写 `error="隐私预算耗尽：第 N 轮累计 ε=… 已超过目标 ε=…，训练已熔断停止"`，已计算的轮次全部保留。新增回归 `test_fl_budget_exhausted_stops_early`、改写 `test_fl_rounds_1_and_eps_tiny`。ε 不再累计到目标值的数倍，loss 也不会发散。
2. **缺陷 2（P3）violations 语义 —— 已修复**：`algorithms/dqn.py` 只在 `attempted != applied`（动作被约束改写）或功率被截断时记入 `violations`；节点处于越限区但网络本就没选禁用动作时不再记录。约束校验留痕没有丢——动作的 `reason` 字段依然写明「SOC 15% 低于下限 20%，禁止放电」。为不破坏契约 3.3 的 `constraintsChecked` 四字段结构，没有新增字段（一版加过 `constraintEvents`，被 `contract-audit.spec.js` 判定越界后回退）。测试 `test_dqn.py`、`test_adversarial_runtime.py` 已同步为新语义。
3. **缺陷 3（P3）backend 未暴露 simulatePoison** —— 属甲方接口范围，保留在 BACKEND-ISSUES；前端 PrivacyCompute 的投毒开关在真后端模式下不会生效，答辩演示投毒检测请用 mock 模式或直连算法服务。
