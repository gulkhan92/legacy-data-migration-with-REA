/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

const api = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": { target: api, changeOrigin: false },
  "/health": { target: api, changeOrigin: false },
  "/docs": { target: api, changeOrigin: false },
  "/openapi.json": { target: api, changeOrigin: false },
};

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  server: { host: "127.0.0.1", port: 5173, proxy },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy },
  build: {
    target: "es2022",
    sourcemap: false,
    cssCodeSplit: true,
    rollupOptions: {
      output: {
        // Long lived libraries get chunks of their own so that a change to a page does not invalidate them in the browser cache.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          if (/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler)\//.test(id)) return "react";
          if (id.includes("@tanstack")) return "query";
          if (id.includes("@radix-ui")) return "radix";
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
