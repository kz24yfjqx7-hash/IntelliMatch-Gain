# MSG test-api → integration 001：algo-service 降级链路两处小问题（P2）

发现于接口/安全测试（`qa/api-tests/degrade_test.py`，在 5301 端口起 algo 副本，`DEEPSEEK_BASE_URL=http://127.0.0.1:9`）。

1. **`GET /algo/v1/health` 的 `models.deepseek` 不反映真实可达性**（API-DEG-01）
   - `adapters/deepseek.py:status()` 只看 `DEEPSEEK_API_KEY && DEEPSEEK_BASE_URL` 是否配置，外网不可达时仍报 `live`；而 `deepseek/analyze` 实际已正确降级为 `cache`/`rule`（API-DEG-02/03 通过）。
   - 建议：记录最近一次 live 调用成败（或启动时探测一次），不可达时报 `cache`。契约 3.1 `deepseek: live|cache`。
2. **`.env`（根目录，已 gitignore）内含真实 `DEEPSEEK_API_KEY`**，不在仓库中，但打包/交付时请确认 `packaging/` 不会把该文件带入安装包（test-api 未发现源码泄露；仅提醒）。

其余算法链路（FL 3 轮真实收敛、每轮 evidenceId、DQN actions 枚举、classify、risk）在真实 backend 下全部通过，见 `docs/测试文档-接口与安全.md`。

3. **FL 轮次 loss 量级**（与甲方 B-011 相关）：backend 的 `algo_fl_round.loss` 为 `DECIMAL(10,6)`，`fl-000011` 等任务的 loss 超过 9999.999999 导致甲方落库/上链每轮失败（日志 562 次）。甲方应改列类型；乙方也建议对 loss 做归一化（或返回 MSE/基准比值），避免 kW² 量级。
