// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: false,
    include: ["test/**/*.test.ts"],
    // Provide custom element polyfill support
    setupFiles: ["./test/setup.ts"],
    // Resolve ?inline imports
    server: {
      deps: {
        inline: ["jest-axe"],
      },
    },
  },
  define: {
    __DEBUG__: "false",
  },
});
