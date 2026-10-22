import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { defineConfig, devices } from '@playwright/test'

// Browser tests against the real backend with the fake provider (fixed dummy data,
// no API key, jobs in memory) behind the Vite dev proxy. Ports differ from the
// usual dev ports, so a running dev setup is left alone.
const BACKEND_PORT = 8765
const FRONTEND_PORT = 5175
// An absolute, native path: cmd.exe (the Windows shell) does not run "../x/python".
const VENV_PYTHON = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']
const python = process.env.E2E_PYTHON ?? `"${resolve(import.meta.dirname, '..', '.venv', ...VENV_PYTHON)}"`

export default defineConfig({
  testDir: 'e2e',
  // The demo set-up (token, CORS, built frontend) has its own config: playwright.demo.config.ts.
  testIgnore: 'demo.spec.ts',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    locale: 'he-IL',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      // PLAYWRIGHT_CHANNEL=chrome uses the installed Chrome instead of Playwright's own Chromium.
      use: { ...devices['Desktop Chrome'], channel: process.env.PLAYWRIGHT_CHANNEL || undefined },
    },
  ],
  webServer: [
    {
      command: `${python} -m uvicorn app.main:app --port ${BACKEND_PORT}`,
      cwd: '../backend',
      url: `http://127.0.0.1:${BACKEND_PORT}/health`,
      env: { CE_PROVIDER: 'fake', CE_STORAGE: 'memory', CE_TMP_DIR: join(tmpdir(), 'ce-e2e') },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npx vite --port ${FRONTEND_PORT} --strictPort`,
      url: `http://localhost:${FRONTEND_PORT}`,
      env: { BACKEND_URL: `http://127.0.0.1:${BACKEND_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
