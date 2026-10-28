import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  use: {
    baseURL: 'http://127.0.0.1:7100',
    browserName: 'chromium',
    headless: true,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev:vite -- --host 127.0.0.1 --port 7100',
    url: 'http://127.0.0.1:7100',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
})
