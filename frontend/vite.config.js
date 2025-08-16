import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../static'
  },
  server: {
    proxy: {
      '/chat': 'http://localhost:8003',
      '/health': 'http://localhost:8003'
    }
  }
})