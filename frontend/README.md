# 能源可信数据空间平台 · 前端

Vue 3 + Vite 5 + Pinia + Vue Router 4 + Element Plus + ECharts；MSW 2 作为离线桩/答辩兜底。

## 脚本

```bash
npm run dev        # 开发（.env.development 默认 VITE_USE_MOCK=true，走 MSW）
npm run build      # 生产构建 → dist/
npm test           # vitest（tests/ 归 qa）
node src/mocks/selfcheck.mjs   # Node 侧跑一遍契约第六部分 12 条验收链路（纯 mock）
```

联调真实后端：`VITE_USE_MOCK=false npm run dev`，vite 把 `/api` 与 `/ws` 代理到 `localhost:8000`。

## 目录

```
src/
├── api/          request.js（axios 实例）+ 13 个模块 + ws.js + index.js 汇总
├── stores/       user / logs / perspective / dispatch
├── router/       /login + AppLayout 子路由；全局守卫
├── layouts/      AppLayout.vue（header + sidebar + logbar）
├── components/   AppHeader / AppSidebar / AppLogBar
├── directives/   v-permission
├── utils/        traceId / format / sha256
├── views/        Login.vue、7 个旧页、4 个中心页
└── mocks/        MSW：db.js（种子）chain.js（本地哈希链）handlers/*.js wsMock.js algo/fedavgLite.js browser.js node.js
```

## 演示账号

admin/admin123、grid/grid123、vpp/vpp123、subject/subject123、regulator/reg123、edge/edge123（见 contract/DB-SCHEMA.md）。

详细使用说明见 `docs/agent-notes/MSG-frontend-infra-to-all-001.md`。
