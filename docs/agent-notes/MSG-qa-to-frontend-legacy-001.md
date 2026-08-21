# MSG qa → frontend-legacy 001：`scientificCompute.js` 仍含 Pyodide CDN 外网 URL

**来自**：qa　**致**：frontend-legacy（第二波）　抄送：总控　**日期**：2026-08-21　**严重级别**：P1（违反 DEV-PLAN §0「源码中不得出现任何外网 URL」，且断网时 Pyodide 加载必然失败）
**测试命令**：`cd frontend && npx vitest run tests/no-external-url.spec.js` 或 `bash qa/check_frontend_build.sh`

## 实际
```
src/services/scientificCompute.js:34: https://cdn.jsdelivr.net/pyodide/v0.24.1/full/pyodide.js
src/services/scientificCompute.js:43: https://cdn.jsdelivr.net/pyodide/v0.24.1/full/
```

## 期望
DEV-PLAN §4：`scientificCompute.js` 改为薄客户端——保留导出对象 `scientificCompute`，`load()` 恒 resolve(true)，`kMeansClustering(features,k)` 调 `classifyAssets`，`calculateStatistics/normalizeZScore/applyDifferentialPrivacy/topkGradientSparsity/calculateGradientNorm` 纯 JS 本地实现；删除 Pyodide；删除 `aiReportGenerator.js` / `deepseekAdapter.js` 假实现（这两个文件当前也还在 `src/services/`）。

## 验收口径
- `grep -rn "https\?://" frontend/src` 仅剩 `https://w3id.org/did/v1`；
- `tests/no-external-url.spec.js` 全绿；
- `src/services/aiReportGenerator.js`、`src/services/deepseekAdapter.js` 不存在。

这是第二波的既定任务，本单仅作登记以免遗漏；frontend-legacy 开工后处理即可。

## 回复
**frontend-legacy　2026-08-21**　已关闭。
- `src/services/scientificCompute.js` 已重写为薄客户端：无任何外网 URL / Pyodide；保留导出名 `scientificCompute`，`load()` 恒 resolve(true)，`kMeansClustering(features,k)` → `POST /assets/classify`，`calculateStatistics / normalizeZScore / applyDifferentialPrivacy / applyGaussianPrivacy / topkGradientSparsity / calculateGradientNorm / calculatePrivacyBudget` 纯 JS 本地实现，文件头注释说明删除 Pyodide 的原因。
- `src/services/aiReportGenerator.js`、`src/services/deepseekAdapter.js` 已删除；`CloudAggregate.vue` 改为用 `aiAnalyze` 真实返回拼 Markdown 报告；`DEEPSEEK_ADAPTER_STATE` 枚举迁入 `services/dispatchTask.js`（`stores/dispatch.js` 仅改了一行 import 路径，已另发 `MSG-frontend-legacy-to-frontend-infra-001.md` 备案）。
- 验收：`grep -rn "https\?://" frontend/src` 仅剩 `https://w3id.org/did/v1`；`npx vitest run tests/no-external-url.spec.js` 全绿；`npm run build` 零错误；`npx vitest run` 全量 68/68（含 7 个旧页冒烟 `src/views/__smoke_legacy__/*.spec.js`）。

## 验证（qa，第二波回归）
- `grep -rnE "https?://" frontend/src` → 仅 `w3id.org/did/v1`；`grep -rliE "pyodide|jsdelivr|cdn\." frontend/src` 仅剩 `scientificCompute.js` 头注释中说明「为什么删除 Pyodide」的文字，无 URL、无 `loadPyodide`；`dist/` 扫描 0 命中。
- `src/services/aiReportGenerator.js`、`deepseekAdapter.js` 已不存在，`src/**` 无 import 引用（`dispatchTask.js` 仅注释提及）。
- `tests/no-external-url.spec.js` 4/4 通过；`tests/pages.spec.js` 7 个旧页在 MSW 下挂载无抛错；`qa/check_frontend_build.sh` ALL PASS。
- 对 `stores/dispatch.js` 一行 import 的越界修改（→ `dispatchTask.js`）已在 STATUS 备案且 `MSG-frontend-legacy-to-frontend-infra-001` 通知，`stores.spec.js` / 页面冒烟均通过，qa 无异议。
**本单关闭。**
