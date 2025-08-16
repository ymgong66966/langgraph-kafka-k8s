import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',  // Standard Vite output directory
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: undefined  // Avoid chunk splitting for simpler deployment
      }
    }
  },
  server: {
    proxy: {
      '/chat': 'http://localhost:8003',
      '/health': 'http://localhost:8003'
    }
  }
})