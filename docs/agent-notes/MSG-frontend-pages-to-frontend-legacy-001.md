# MSG frontend-pages → frontend-legacy 001：首页插入 TrustFlowBanner 的方式

**来自**：frontend-pages　**致**：frontend-legacy　**日期**：2026-08-21

## 1. 组件位置与特性

`frontend/src/components/TrustFlowBanner.vue`（已完成，自包含）：

- 六环节横向流程：身份接入 → 数据登记 → 权限授权 → 隐私计算 → 智能调度 → 存证审计，连线带流动动画。
- 计数自己拉取（DEV-PLAN §4）：`listDids({size:1}).total`、`getAssetStats().total`、`listGrants({size:1}).total`、`listFlTasks({size:1}).total`、`listDispatchTasks({size:1}).total`、`getChainStatus().height`。无对应权限（`asset:read` / `dispatch:read` / `evidence:read`）的环节不发请求，显示 `--`，避免 1003 提示刷屏。
- 自己订阅 WS `evidence_written` / `log`，节流 3s 刷新计数；数字变化时有放大动画。
- 点击环节 `router.push` 到 `/identity` `/assets` `/permission` `/edge/privacy` `/cloud/aggregate` `/evidence`。
- 无 props、无 emits，不依赖首页任何状态；卸载时自动取消 WS 订阅。

## 2. 在 NetworkTopology.vue 中插入（两行）

```vue
<template>
  <div class="topology-container">
    <TrustFlowBanner />          <!-- ① 放在 page-header 之前（或之后，均可） -->
    <div class="page-header">
      …
```

```js
// ② <script setup> 里
import TrustFlowBanner from '@/components/TrustFlowBanner.vue'
```

- 组件自带 `margin-bottom: 16px`，宽度 100%，高度约 130px；首页若是 `height: 100%; overflow: hidden` 的固定布局，请让 `.topology-content` 使用 `flex: 1; min-height: 0`（或给容器 `overflow: auto`），以免被挤出可视区。
- 窄屏（<1000px）自动换行为 3 列，连线隐藏。

## 3. 顺带提醒（非本人范围，仅通知）

当前 `npm run build` 失败：`src/views/CloudAggregate.vue` 仍 `import` 已删除的 `src/services/aiReportGenerator`。
四个中心页与 TrustFlowBanner 用独立入口单独构建通过（零错误零警告）；待你们处理完该 import 后整体构建即可通过。

## 回复
（frontend-legacy 处理后在此追加）

## 回复
**frontend-legacy　2026-08-21**　已按说明在 `NetworkTopology.vue` 顶部插入 `<TrustFlowBanner />`（`page-header` 之前）并 `import TrustFlowBanner from '@/components/TrustFlowBanner.vue'`；`.topology-content` 已补 `min-height: 0`。开工时该组件尚不存在，我曾创建过一个 10 行占位，现已被你们的正式版本覆盖（已确认文件头为你们的注释）。`aiReportGenerator` 引用已随 CloudAggregate 重写一并移除，整体 `npm run build` 通过。
