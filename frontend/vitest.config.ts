import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Unit and component tests (jsdom); the browser tests in e2e/ run with Playwright.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['src/test/setup.ts'],
  },
})
