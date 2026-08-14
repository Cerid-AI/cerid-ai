// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

export default defineConfig({
  // Relative, not the "/" default. The desktop app loads this same bundle from
  // disk via `loadFile`, and under file:// a root-absolute `/assets/index.js`
  // resolves to the FILESYSTEM root — so every script and stylesheet 404s and
  // the window renders blank with no error in the main process, because the
  // failure is a renderer subresource. That is exactly how the packaged app
  // shipped: `loadFile` itself succeeded, so nothing logged.
  //
  // Safe for the container build too: the SPA lives at a single path and only
  // ever writes query params (navigation-context.tsx, analytics-panel.tsx,
  // meetings-capture-panel.tsx all mutate searchParams and leave pathname
  // alone), so relative asset URLs always resolve against the same directory.
  base: "./",
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
    // Vitest defaults to 5000ms, which this suite cannot hold on a busy box.
    // It blocked `make push` three times across 2026-08-09/10 with 16, 20 and
    // 22 timeouts — in DIFFERENT files each run, none related to the change,
    // and the same suite passed 226/226 standalone every time. 226 files times
    // a jsdom environment plus React Testing Library setup is enough work that
    // a background Spotlight reindex pushes individual tests past 5s.
    //
    // That is a gate reporting machine load, not correctness, and the cost is
    // worse than the delay: the obvious escape is `git push --no-verify`,
    // which also skips the supply-chain guard (see scripts/safe-push.sh).
    // A real hang still fails, just at 20s instead of 5s.
    testTimeout: 20_000,
    hookTimeout: 20_000,
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