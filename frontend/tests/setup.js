/**
 * vitest setupFiles（qa 维护）。
 * - 启动 src/mocks/node.js 导出的 MSW server（若尚未就绪则跳过并在日志提示，不让整套测试崩溃）
 * - afterEach resetHandlers / afterAll close
 * - jsdom 已提供 localStorage / crypto；这里补 import.meta.env 默认值
 */
import { afterAll, afterEach, beforeAll } from 'vitest'

if (!import.meta.env.VITE_API_BASE) import.meta.env.VITE_API_BASE = '/api/v1'
if (!import.meta.env.VITE_WS_BASE) import.meta.env.VITE_WS_BASE = '/ws'
// 测试里不建真实 WebSocket：走 wsMock 事件总线
import.meta.env.VITE_USE_MOCK = 'true'

let server = null
// 用 import.meta.glob 而非字面量 import：文件缺失时不会让 vite 转换阶段报错
const candidates = import.meta.glob('../src/mocks/node.js')
const loader = candidates['../src/mocks/node.js']
if (loader) {
  try {
    const mod = await loader()
    server = mod.server || mod.default || null
    if (!server && typeof mod.setupServer === 'function') server = mod.setupServer()
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn('[qa/setup] 加载 src/mocks/node.js 失败：', e?.message)
  }
} else {
  // 由 frontend-infra 提供 src/mocks/node.js；缺失时依赖它的用例会自行报告
  // eslint-disable-next-line no-console
  console.warn('[qa/setup] src/mocks/node.js 尚未就绪')
}

globalThis.__mswServer = server

beforeAll(() => {
  if (server) server.listen({ onUnhandledRequest: 'bypass' })
})
afterEach(() => {
  if (server) server.resetHandlers()
})
afterAll(() => {
  if (server) server.close()
})
