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
      // @cosmos.gl/graph (B8 Live mode) imports a default from gl-bench, but
      // the resolver picks gl-bench's UMD build (a global-assign IIFE with no
      // ESM default export), which breaks the production build. Force its
      // proper ESM build (dist/gl-bench.module.js — has `export default`).
      "gl-bench": path.resolve(__dirname, "./node_modules/gl-bench/dist/gl-bench.module.js"),
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
          // Stratigraph d3 modules — timeline-only, ~30KB gz.
          // Bundled with vendor-charts so they share the same lazy subjects chunk.
          if (id.includes("node_modules/d3-")) return "vendor-charts"
          if (id.includes("node_modules/react-markdown") || id.includes("node_modules/remark-gfm")) {
            return "vendor-markdown"
          }
          if (id.includes("node_modules/@tanstack/react-query")) return "vendor-query"
          // Phase B — split the 3D stack so Constellation chunk stays
          // under 800KB. ORDER MATTERS: check @react-three FIRST so
          // its files (which contain "three" in path) route to r3f
          // chunk, then catch raw three.js in vendor-three.
          // Postprocessing rides the same lazy 3D chunk as fiber/drei. A
          // separate vendor-postfx chunk was tried and rolldown merged the
          // three.js core into it, which would make BASE constellation load
          // depend on the postfx chunk — worse than the ~46KB gzip it saves.
          if (
            id.includes("@react-three/fiber") ||
            id.includes("@react-three/drei") ||
            id.includes("@react-three/postprocessing") ||
            id.includes("node_modules/postprocessing")
          ) {
            return "vendor-r3f"
          }
          if (id.includes("node_modules/three") || id.includes("/three/three.")) return "vendor-three"
          // cosmos.gl "Live" mode (B8) + its luma.gl renderer — lazy chunk,
          // only loaded when the user switches to the self-organizing scene.
          // Its transitive d3-* deps are caught by the d3 rule above (also lazy).
          if (id.includes("node_modules/@cosmos.gl/") || id.includes("node_modules/@luma.gl/")) {
            return "vendor-cosmos"
          }
          // 2026-05-24 (rc1 beta finding F6) — peel Atlas dependencies out
          // of the main bundle. sigma.js + graphology + umap together
          // account for ~157KB minified. The Atlas pane is one of four;
          // users who never open it shouldn't pay for the load on first
          // paint.
          if (id.includes("node_modules/sigma") || id.includes("node_modules/graphology")) {
            return "vendor-atlas"
          }
          if (id.includes("node_modules/umap-js")) return "vendor-atlas"
          // Radix UI primitives — large ecosystem (~80-100KB across
          // dialog/dropdown/popover/select/tooltip etc.). Peeling them
          // into their own chunk drops main bundle below the 800KB CI
          // cap with comfortable headroom.
          if (id.includes("node_modules/@radix-ui/")) return "vendor-radix"
          // React + ReactDOM — stable foundation deps; cache headers can
          // long-pin this chunk across releases that change app code only.
          if (
            id.includes("node_modules/react/") ||
            id.includes("node_modules/react-dom/") ||
            id.includes("node_modules/scheduler/")
          ) {
            return "vendor-react"
          }
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