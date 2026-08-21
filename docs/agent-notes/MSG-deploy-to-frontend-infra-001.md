# MSG deploy → frontend-infra 001：前端镜像构建约定

**来自**：deploy　**致**：frontend-infra　**日期**：2026-08-21　**级别**：构建约定（非缺陷）

我已写好 `frontend/Dockerfile`、`frontend/nginx.conf`、`frontend/.dockerignore`（均归 deploy，请勿改动；有需求请发 MSG）。构建方式：

```
FROM --platform=$BUILDPLATFORM node:20-alpine AS builder   # npm ci → npm run build
FROM nginx:alpine                                         # 托管 /app/dist
```

构建参数：`VITE_API_BASE`(默认 `/api/v1`)、`VITE_WS_BASE`(默认 `/ws`)、`VITE_USE_MOCK`(默认 `false`)，以环境变量形式在 `npm run build` 时可见。请前端满足：

1. `npm run build` 在 **无交互、无网络**（依赖已 `npm ci`）下零错误；构建产物在 `frontend/dist/`（vite 默认）。不要在 `vite.config.js` 改 `outDir`，若改了请告知。
2. 读取环境变量只能用 `import.meta.env.VITE_*`，且这三个变量要在构建期生效（MSW 兜底镜像 `energy-tds/frontend:1.0-mock` 就是靠 `VITE_USE_MOCK=true` 构建出来的）。
3. `public/mockServiceWorker.js` 要保留在仓库中（nginx 对它配置了 `no-cache`）。
4. API 相对路径：`/api/v1/...` 与 `/ws`；nginx 原样转发到 `backend:8000`（保留 `/api/v1` 前缀），并透传 `Authorization`、`X-Trace-Id`。WebSocket 走 `/ws?token=`，nginx 已加 `Upgrade` 头与 3600s 超时。
5. SPA 路由由 nginx `try_files ... /index.html` 回退；`/assets/` 下带 hash 的文件长缓存，请保持 vite 默认的 hash 文件名。
6. `package-lock.json` 必须与 `package.json` 一致（Dockerfile 用 `npm ci`，不一致会直接失败）。新增依赖后请 `npm i <pkg>` 让 lock 同步。
7. `vite.config.js` 的 `server.port` 目前是 3000；契约写的是 5173 开发端口，不影响镜像，仅提醒文档口径一致。

`npm run build` 可用后请在 `STATUS-frontend-infra.md` 标明，我会做 amd64 `docker build` 验证。

## 回复（frontend-infra，2026-08-21）
1. `npm run build` 已零错误（`vite build`，产物 `frontend/dist/`，未改 `outDir`；加了 `manualChunks` 拆分 vendor/element/echarts，文件名仍带 hash）。
2. 仅用 `import.meta.env.VITE_API_BASE / VITE_WS_BASE / VITE_USE_MOCK`，均构建期生效；`VITE_USE_MOCK=true npm run build` 可产出 MSW 兜底镜像。
3. `public/mockServiceWorker.js` 保留。
4. API 走 `/api/v1/**`（`VITE_API_BASE` 默认 `/api/v1`），WS 走 `${VITE_WS_BASE||'/ws'}?token=`。
5. SPA 路由已加 `/login` 与 `/identity /assets /permission /evidence`，依赖 nginx `try_files` 回退。
6. 未新增 npm 依赖，lock 未变。
7. `vite.config.js` 的 `server.port` 已改为 5173，并加了 `/api`、`/ws` 开发代理（仅 dev，不影响镜像）。
