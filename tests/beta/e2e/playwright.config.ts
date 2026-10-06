// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { execFileSync } from "node:child_process"
import path from "node:path"
import { defineConfig, devices } from "@playwright/test"

/**
 * Cerid beta-test E2E config — tier 6 of tests/beta/run.sh.
 *
 * Designed for solo-dev local beta-testing: the specs drive the GUI of the
 * stack BETA_TARGET names (cerid-web nginx proxy → MCP). No CI-mode defaults
 * baked in — the script's --browser flag drives this from the host, not a
 * hosted runner.
 */

/**
 * GUI URL of the stack BETA_TARGET names. run.sh exports CERID_WEB_URL from
 * tests/beta/lib/target.sh; a standalone `npx playwright test` asks the same
 * file, so this config never keeps a port of its own.
 */
function targetGuiUrl(): string {
  const targetSh = path.resolve(__dirname, "..", "lib", "target.sh")
  return execFileSync("bash", ["-c", 'source "$0" && printf "%s" "$BETA_GUI_URL"', targetSh], {
    encoding: "utf8",
  }).trim()
}

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
    baseURL: process.env.CERID_WEB_URL ?? targetGuiUrl(),
    // The MCP enforces X-API-Key on /api routes. Forward the key (exported by
    // run.sh from .env) on every `request`-context call so REST-surface specs
    // (E-09 wiki, E-13 stats, …) authenticate the same way the keyed frontend
    // does. UI-driven specs rely on the frontend's own baked-in VITE key.
    extraHTTPHeaders: process.env.CERID_API_KEY
      ? { "X-API-Key": process.env.CERID_API_KEY }
      : {},
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
