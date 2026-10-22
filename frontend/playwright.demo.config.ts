import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { defineConfig, devices } from '@playwright/test'

// The deployed demo's shape, locally: a built frontend (vite preview) that calls the
// backend directly on another origin, with the access code, CORS and rate limits on.
// Fake provider, jobs in memory, dummy code only.
export const DEMO_BACKEND_PORT = 8766
export const DEMO_FRONTEND_PORT = 5176
export const DEMO_ACCESS_TOKEN = 'e2e-dummy-access-code-0123456789'
export const DEMO_BACKEND_URL = `http://127.0.0.1:${DEMO_BACKEND_PORT}`
const FRONTEND_ORIGIN = `http://localhost:${DEMO_FRONTEND_PORT}`
const VENV_PYTHON = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']
const python = process.env.E2E_PYTHON ?? `"${resolve(import.meta.dirname, '..', '.venv', ...VENV_PYTHON)}"`

export default defineConfig({
  testDir: 'e2e',
  testMatch: 'demo.spec.ts',
  timeout: 60_000,
  workers: 1,
  reporter: 'list',
  use: { baseURL: FRONTEND_ORIGIN, locale: 'he-IL', trace: 'retain-on-failure' },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], channel: process.env.PLAYWRIGHT_CHANNEL || undefined } },
  ],
  webServer: [
    {
      command: `${python} -m uvicorn app.main:app --port ${DEMO_BACKEND_PORT}`,
      cwd: '../backend',
      url: `${DEMO_BACKEND_URL}/health`,
      env: {
        CE_PROVIDER: 'fake',
        CE_STORAGE: 'memory',
        CE_TMP_DIR: join(tmpdir(), 'ce-e2e-demo'),
        CE_DEMO_ACCESS_TOKEN: DEMO_ACCESS_TOKEN,
        CE_REQUIRE_ACCESS_TOKEN: '1',
        CE_ALLOWED_ORIGINS: FRONTEND_ORIGIN,
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npx vite build --outDir dist-e2e-demo && npx vite preview --outDir dist-e2e-demo --port ${DEMO_FRONTEND_PORT} --strictPort`,
      url: FRONTEND_ORIGIN,
      env: { VITE_API_URL: DEMO_BACKEND_URL },
      reuseExistingServer: false,
      timeout: 180_000,
    },
  ],
})
