import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Dev-only: proxy to the FastAPI read layer so the browser stays
      // same-origin. Keeps us from opening CORS on the API just for local dev;
      // in production web/ is served behind the same origin (or a gateway).
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
