import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend runs on :8000 locally; docker-compose overrides the target via BACKEND_URL.
const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/api': { target: backendUrl, rewrite: (path) => path.replace(/^\/api/, '') },
    },
  },
})
