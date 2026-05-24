// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { defineConfig, devices } from "@playwright/test"

/**
 * Cerid beta-test E2E config — tier 6 of tests/beta/run.sh.
 *
 * Designed for solo-dev local beta-testing: the stack runs on
 * localhost:3000 (cerid-web nginx proxy → MCP at :8888). No CI-mode
 * defaults baked in — the script's --browser flag drives this from
 * the host, not a hosted runner.
 */
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false, // single-user app; conversations + KB writes must not interleave
  workers: 1,
  retries: 1,
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  reporter: [
    ["list"],
    ["junit", { outputFile: "../reports/e2e.xml" }],
    ["html", { outputFolder: "../reports/e2e-html", open: "never" }],
  ],
  use: {
    baseURL: process.env.CERID_WEB_URL ?? "http://localhost:3000",
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // Cerid's first-paint sequence + LiquidGlass SVG filter benefit
    // from real Chrome rendering; jsdom equivalents wouldn't catch
    // the View Transitions API contract.
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
})
