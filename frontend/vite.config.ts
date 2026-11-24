import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser talks to /api; Vite (dev) and nginx (Docker) forward it to the FastAPI backend.
// Same origin means no CORS and no credentials ever sent cross-site.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
