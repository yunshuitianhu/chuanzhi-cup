import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期把 /api 代理到 FastAPI（8000），避免跨域配置分散在两处。
// 生产构建由后端/Nginx 直接托管 dist/，前端仍用相对路径 /api。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1200,   // echarts 体积较大，属预期
  },
})
