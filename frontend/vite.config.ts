import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev proxy: Flask runs on 5001 locally (macOS AirPlay occupies 5000).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:5001',
    },
  },
})
