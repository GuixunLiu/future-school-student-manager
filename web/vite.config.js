import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端业务 API 默认本机回环地址；可用 VITE_API_TARGET 覆盖
const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      // 开发期同源代理：后端 LocalGuard 要求 Host 为回环地址且 Origin 与之匹配。
      // changeOrigin 改写 Host；headers 同步改写 Origin，否则模拟器来源会被 403
      '/v1': { target: apiTarget, changeOrigin: true, headers: { Origin: apiTarget } },
      '/health': { target: apiTarget, changeOrigin: true, headers: { Origin: apiTarget } }
    }
  },
  build: {
    // 平板旧 WebView 兼容：禁用可选链/空值合并等新语法
    target: 'es2015',
    outDir: 'dist'
  }
})
