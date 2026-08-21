import { defineConfig } from '@playwright/test'

// E2E 针对 mock 模式的 vite dev server（默认 5197，可用 E2E_BASE_URL 覆盖）
export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: './e2e/.results',
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:5197',
    headless: true,
    viewport: { width: 1366, height: 768 },
    screenshot: 'off',
    video: 'off',
    trace: 'off'
  },
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: 'VITE_USE_MOCK=true npx vite --port 5197 --strictPort',
    url: 'http://localhost:5197',
    reuseExistingServer: true,
    timeout: 60_000
  }
})
