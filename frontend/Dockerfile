# ============================================================
# 前端镜像：多阶段构建
#   builder 阶段固定跑在构建机原生架构（$BUILDPLATFORM），npm run build 产物与架构无关；
#   仅 nginx 运行阶段使用目标架构（arm64 交叉构建时省掉 QEMU 下跑 node 的时间）。
# 构建参数：
#   VITE_API_BASE  默认 /api/v1
#   VITE_WS_BASE   默认 /ws
#   VITE_USE_MOCK  默认 false；true = 打出 MSW 假后端版本（答辩兜底镜像）
# ============================================================
FROM --platform=$BUILDPLATFORM node:20-alpine AS builder

ARG VITE_API_BASE=/api/v1
ARG VITE_WS_BASE=/ws
ARG VITE_USE_MOCK=false
ENV VITE_API_BASE=${VITE_API_BASE} \
    VITE_WS_BASE=${VITE_WS_BASE} \
    VITE_USE_MOCK=${VITE_USE_MOCK} \
    NODE_ENV=development

WORKDIR /app

# 先只拷依赖清单，利用层缓存；有 lock 文件用 npm ci，否则退回 npm install
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi

COPY . .
RUN npm run build

# ---------- 运行阶段 ----------
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=4 CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
CMD ["nginx", "-g", "daemon off;"]
