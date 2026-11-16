import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Static hosting under a sub-path (GitHub Pages serves at /<repository>/) needs relative asset URLs.
  base: process.env.VITE_STATIC_DATA === 'true' ? './' : '/',
  server: {
    // In development the browser talks to the Vite server, which forwards API calls to FastAPI.
    proxy: { '/api': process.env.API_PROXY_TARGET ?? 'http://localhost:8000' },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
