import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API keys never reach the browser: the dev server only proxies /api to the local FastAPI backend.
// API_TARGET lets several dev servers talk to different backend ports (default 8000).
declare const process: { env: Record<string, string | undefined> }; // Node global; avoids a @types/node dependency
const target = process.env.API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": { target, changeOrigin: false } },
  },
});
