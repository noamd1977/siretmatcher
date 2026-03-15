import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://localhost:8042',
      '/search': 'http://localhost:8042',
    },
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
})
