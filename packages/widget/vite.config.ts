// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from "vite";
import dts from "vite-plugin-dts";
import { resolve } from "path";

// Vite is called twice:
//   --mode library → ESM/CJS npm package (dist/index.js + dist/index.cjs)
//   --mode cdn     → IIFE CDN bundle     (dist/cerid-widget.js, single-file minified)

export default defineConfig(({ mode }) => {
  if (mode === "cdn") {
    return {
      build: {
        outDir: "dist",
        emptyOutDir: false,
        lib: {
          entry: resolve(__dirname, "src/index.ts"),
          name: "CeridWidget",
          formats: ["iife"],
          fileName: () => "cerid-widget.js",
        },
        rollupOptions: {
          output: {
            // Inline everything — no external deps for CDN usage
            inlineDynamicImports: true,
          },
        },
        // Inline CSS into JS (no separate CSS file)
        cssCodeSplit: false,
        // Vite 8 unbundled esbuild: `minify: "esbuild"` now resolves esbuild
        // from node_modules and this package does not depend on it. `true` takes
        // whatever minifier Vite ships, which is the intent here.
        minify: true,
        target: "es2019",
        // Report bundle size
        reportCompressedSize: true,
      },
      define: {
        __DEBUG__: "false",
      },
    };
  }

  // Library mode (ESM + CJS)
  return {
    build: {
      outDir: "dist",
      emptyOutDir: true,
      lib: {
        entry: resolve(__dirname, "src/index.ts"),
        formats: ["es", "cjs"],
        fileName: (format) => (format === "es" ? "index.js" : "index.cjs"),
      },
      rollupOptions: {
        output: {
          // CSS inlined — no external assets
          inlineDynamicImports: true,
        },
      },
      cssCodeSplit: false,
      minify: false,
      target: "es2022",
    },
    plugins: [
      dts({
        include: ["src"],
        outDir: "dist",
        bundleTypes: true,
      }),
    ],
    define: {
      __DEBUG__: "false",
    },
  };
});
