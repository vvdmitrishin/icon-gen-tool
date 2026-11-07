import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API keys never reach the browser: the dev server only proxies /api to the local FastAPI backend.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
});
