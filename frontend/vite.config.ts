import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/*
 * 两个关键点，改之前先想清楚：
 *
 * 1. ``base: '/static/'`` —— Flask 用 ``static_url_path="/static"`` 托管
 *    静态目录，而 ``index.html`` 是由 ``/`` 路由返回的。所以 index.html
 *    里的资源引用必须是绝对路径 ``/static/assets/xxx.js``，否则浏览器会去
 *    ``/assets/xxx.js`` 找，404 白屏。
 * 2. ``build.outDir`` 指到后端的 static 目录，产物直接入库，
 *    最终用户双击 run.bat 即可，不需要装 Node。
 */
export default defineConfig({
  plugins: [react()],
  base: '/static/',
  build: {
    outDir: '../vial_light/webapp/static',
    emptyOutDir: true,
    assetsDir: 'assets',
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    // 开发时把 /api 转发给 Flask（python main.py --port 8765 --no-browser）
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
});