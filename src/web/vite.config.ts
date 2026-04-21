// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/__tests__/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 800,
    // Vite 8 removed the object form of manualChunks; use the function form
    // (works under both the legacy Rollup path and the new Rolldown bundler).
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/recharts")) return "vendor-charts"
          if (id.includes("node_modules/react-markdown") || id.includes("node_modules/remark-gfm")) {
            return "vendor-markdown"
          }
          if (id.includes("node_modules/@tanstack/react-query")) return "vendor-query"
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api/bifrost": {
        target: "http://localhost:8080",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/bifrost/, ""),
      },
      "/api/mcp": {
        target: "http://localhost:8888",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/mcp/, ""),
      },
    },
  },
})