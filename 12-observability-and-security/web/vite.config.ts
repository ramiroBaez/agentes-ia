import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The SPA talks to the API through `/api`, which Vite forwards to uvicorn in dev
// and nginx forwards to the backend in production. Always the same origin: the
// session cookie and the CSRF token never travel to another host.
const API = process.env.AURORA_API ?? 'http://127.0.0.1:8000'

const proxy = {
  '/api': { target: API, changeOrigin: true, rewrite: (path: string) => path.slice(4) },
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // No base64 inside the CSS: the small font subsets were inlined as data URIs
  // and the browser rejected them ("Loading the font ... has failed"), leaving
  // the brand typography unloaded. As files they are also cached.
  build: { assetsInlineLimit: 0 },
  server: { proxy },
  // `npm run preview` serves the production build, so it forwards too: otherwise
  // testing the real bundle would require starting nginx somewhere else.
  preview: { proxy },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/tests/setup.ts'],
    css: false,
  },
})
