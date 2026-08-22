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
