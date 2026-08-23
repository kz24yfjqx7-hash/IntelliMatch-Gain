/**
 * request.js 拦截器：解包 data / 1002 跳登录 / 1003 toast / 错误对象附带 code、traceId / X-Trace-Id 与 Authorization 注入。
 * 本文件自带独立 MSW server，不依赖项目 handlers。
 */
import { describe, it, expect, vi, beforeAll, afterAll, afterEach, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { setActivePinia, createPinia } from 'pinia'

const toastError = vi.fn()
vi.mock('element-plus', () => ({ ElMessage: { error: (...a) => toastError(...a), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }))

const routerReplace = vi.fn()
vi.mock('@/router', () => ({ default: { currentRoute: { value: { path: '/assets', fullPath: '/assets?x=1' } }, replace: (...a) => routerReplace(...a), push: vi.fn() } }))

const clearSession = vi.fn()
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ clearSession, token: 'tok' }) }))

const captured = { headers: null }
const wrap = (data, code = 0, message = 'ok', traceId = 'tr-20260821-abcdef01') => ({ code, message, data, traceId })

const local = setupServer(
  http.get('*/api/v1/ping', ({ request }) => {
    captured.headers = Object.fromEntries(request.headers.entries())
    return HttpResponse.json(wrap({ pong: true, traceEcho: request.headers.get('x-trace-id') }))
  }),
  http.get('*/api/v1/unauth', () => HttpResponse.json(wrap(null, 1002, 'Token 失效'), { status: 401 })),
  http.get('*/api/v1/forbid', () => HttpResponse.json(wrap(null, 1003, '角色 vpp_operator 无 dispatch:issue 权限'), { status: 403 })),
  http.get('*/api/v1/badsig', () => HttpResponse.json(wrap(null, 1004, '签名无效'), { status: 403 })),
  http.get('*/api/v1/conflict200', () => HttpResponse.json(wrap(null, 1006, '任务已在运行'), { status: 200 })),
  http.get('*/api/v1/boom', () => HttpResponse.json(wrap(null, 5000, '内部错误', 'tr-20260821-deadbeef'), { status: 500 })),
  http.get('*/api/v1/csv', () => new HttpResponse('a,b\n1,2\n', { headers: { 'Content-Type': 'text/csv' } })),
  http.get('*/api/v1/missing', () => HttpResponse.json(wrap(null, 1005, '接口或资源不存在'), { status: 404 })),
  http.get('*/api/v1/down', () => HttpResponse.error())
)

let request, download
beforeAll(async () => {
  local.listen({ onUnhandledRequest: 'bypass' })
  const mod = await import('@/api/request.js')
  request = mod.default
  download = mod.download
})
afterEach(() => { local.resetHandlers(); vi.clearAllMocks() })
afterAll(() => local.close())
beforeEach(() => { setActivePinia(createPinia()); localStorage.setItem('energy-tds-token', 'tok-abc') })

describe('request.js 拦截器', () => {
  it('code===0 时 resolve data 字段', async () => {
    const data = await request.get('/ping')
    expect(data).toMatchObject({ pong: true })
  })

  it('注入 Authorization 与 X-Trace-Id（格式 tr-YYYYMMDD-8hex）', async () => {
    const data = await request.get('/ping')
    expect(captured.headers.authorization).toBe('Bearer tok-abc')
    expect(captured.headers['x-trace-id']).toMatch(/^tr-\d{8}-[0-9a-f]{8}$/)
    expect(data.traceEcho).toBe(captured.headers['x-trace-id'])
  })

  it('1002 → 清登录态并跳 /login?redirect=', async () => {
    await expect(request.get('/unauth')).rejects.toMatchObject({ code: 1002 })
    await new Promise(r => setTimeout(r, 20))
    expect(clearSession).toHaveBeenCalled()
    expect(routerReplace).toHaveBeenCalled()
    const arg = routerReplace.mock.calls[0][0]
    expect(arg.path || arg).toBe('/login')
    expect(arg.query?.redirect).toBe('/assets?x=1')
  })

  it('1003 → ElMessage.error 越权提示并 reject，error 带 code/message/traceId', async () => {
    let err
    try { await request.get('/forbid') } catch (e) { err = e }
    expect(err).toBeTruthy()
    expect(err.code).toBe(1003)
    expect(err.message).toContain('dispatch:issue')
    expect(err.traceId).toBe('tr-20260821-abcdef01')
    expect(toastError).toHaveBeenCalled()
    expect(String(toastError.mock.calls[0][0])).toMatch(/越权|权限/)
  })

  it('1004 → 验签失败提示并 reject', async () => {
    await expect(request.get('/badsig')).rejects.toMatchObject({ code: 1004 })
    expect(toastError).toHaveBeenCalled()
    expect(String(toastError.mock.calls[0][0])).toMatch(/签名|验签|DID/)
  })

  it('HTTP 200 但 code 非 0 也要 reject + toast', async () => {
    await expect(request.get('/conflict200')).rejects.toMatchObject({ code: 1006 })
    expect(toastError).toHaveBeenCalled()
  })

  it('5000 → toast + reject，traceId 来自响应体', async () => {
    await expect(request.get('/boom')).rejects.toMatchObject({ code: 5000, traceId: 'tr-20260821-deadbeef' })
    expect(toastError).toHaveBeenCalled()
  })

  it('download() 返回 Blob（不解包）', async () => {
    const blob = await download('/csv', { period: 'day' })
    expect(blob).toBeInstanceOf(Blob)
    expect(blob.size).toBeGreaterThan(0)
    // jsdom 的 Blob 无 text()，用 FileReader 读
    const text = await new Promise(res => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsText(blob) })
    expect(text).toContain('a,b')
  })

  it('baseURL 为 /api/v1', () => {
    expect(request.defaults.baseURL).toBe('/api/v1')
  })

  /* silent：后台附属请求（铃铛拉取）失败不该弹全局 toast 打断用户。
     这条用例是为「登录后立刻弹接口或资源不存在」那次回归立的。 */
  describe('config.silent', () => {
    it('silent 请求业务失败不弹 toast，但仍 reject 且带 code', async () => {
      await expect(request.get('/missing', { silent: true })).rejects.toMatchObject({ code: 1005 })
      expect(toastError).not.toHaveBeenCalled()
    })

    it('silent 请求网络不可达也不弹 toast', async () => {
      await expect(request.get('/down', { silent: true })).rejects.toBeTruthy()
      expect(toastError).not.toHaveBeenCalled()
    })

    it('不带 silent 时同样的 404 照常弹 toast（不影响既有行为）', async () => {
      await expect(request.get('/missing')).rejects.toMatchObject({ code: 1005 })
      expect(toastError).toHaveBeenCalled()
    })

    it('silent 也不能吞掉 1002：登录态失效必须提示并跳登录', async () => {
      await expect(request.get('/unauth', { silent: true })).rejects.toMatchObject({ code: 1002 })
      await new Promise(r => setTimeout(r, 20))
      expect(toastError).toHaveBeenCalled()
      expect(clearSession).toHaveBeenCalled()
    })
  })
})
