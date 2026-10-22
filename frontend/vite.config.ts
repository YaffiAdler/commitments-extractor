import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// The backend runs on :8000 locally; docker-compose overrides the target via BACKEND_URL.
const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8000'

// A built frontend calls the backend directly (no rewrites, no proxy), so it needs the
// backend's absolute URL; without it the app would call its own host and get index.html.
function requireApiUrl(mode: string) {
  const value = loadEnv(mode, process.cwd(), 'VITE_').VITE_API_URL?.trim()
  if (!value || !URL.canParse(value) || !/^https?:$/.test(new URL(value).protocol)) {
    throw new Error('VITE_API_URL must be set to the backend URL (e.g. https://<service>.onrender.com) to build')
  }
}

// https://vite.dev/config/
export default defineConfig(({ command, mode }) => {
  if (command === 'build') requireApiUrl(mode)
  return {
    plugins: [react()],
    server: {
      host: true,
      proxy: {
        '/api': { target: backendUrl, rewrite: (path) => path.replace(/^\/api/, '') },
      },
    },
  }
})
