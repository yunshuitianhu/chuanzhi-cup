import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期把 /api 代理到 FastAPI，避免跨域配置分散在两处。
// 生产构建由后端/Nginx 直接托管 dist/，前端仍用相对路径 /api。
//
// 端口可用环境变量覆盖：本机 8000 端口上堆积了无法回收的残留监听套接字
// （netstat 显示有 PID，但该 PID 的进程已不存在，taskkill 也找不到），
// 表现为「改了后端代码却没生效」—— 请求被残留套接字背后的旧实例接走了。
// PowerShell 覆盖示例：$env:BACKEND_PORT='8003'; npm run dev
export default defineConfig(({mode}) => {
  const BACKEND_PORT = loadEnv(mode, process.cwd(), 'WQ_').WQ_BACKEND_PORT || process.env.BACKEND_PORT || '8002'
  return {
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${BACKEND_PORT}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    manifest: true,
    outDir: 'dist',
    chunkSizeWarningLimit: 1200,   // echarts 体积较大，属预期
  },
  }
})
