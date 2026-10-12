import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 개발 모드(start.bat dev)에서는 Vite(5173)가 화면을, uvicorn(8000)이 API 를 맡는다.
// /api 를 프록시해 브라우저 입장에서는 같은 출처가 된다(쿠키·SSE 가 그대로 동작).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
  },
  build: { outDir: "dist", emptyOutDir: true },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
