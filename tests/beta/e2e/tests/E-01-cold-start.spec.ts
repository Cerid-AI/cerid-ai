// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect, suppressFirstRun } from "./fixtures"

/**
 * E-01 — Cold-start render.
 *
 * The OpeningSequence is suppressed by the shared fixture (its 1300ms
 * gold-ring reveal would otherwise gate every test on a fixed
 * timeout). Asserts the app shell + status bar + 4-pane nav are
 * mounted and the runtime __ENV__ shim is wired.
 *
 * Covers: content-rise wiring, health status bar, 4-pane navigation
 * visibility, runtime __ENV__ injection.
 */
test("E-01 cold-start render", async ({ page }) => {
  await page.goto("/")
  await suppressFirstRun(page)

  await expect(page).toHaveTitle(/Cerid AI/i)

  // The 4-pane sidebar buttons must be present (Phase C consolidation).
  for (const pane of ["Chat", "Subjects", "Sources", "Settings"]) {
    await expect(page.getByRole("button", { name: pane, exact: true })).toBeVisible()
  }

  // Bottom status bar — operational rollup is the canonical signal.
  // The bar populates after the initial /health probe completes; give
  // it up to 15s on a cold container start.
  await expect(page.getByText("All systems operational")).toBeVisible({ timeout: 15_000 })

  // The three backing services each surface their name + connected
  // state. Use case-insensitive substring match — the StaticText
  // nodes for "chromadb"/":"/"connected" are tightly adjacent and
  // some renderers join them at the locator-tree level.
  for (const service of ["chromadb", "redis", "neo4j"]) {
    await expect(page.getByText(new RegExp(service, "i")).first()).toBeVisible()
  }

  // window.__ENV__ proves docker-entrypoint.sh wrote env-config.js
  // at container boot.
  const envOk = await page.evaluate(() => {
    const env = (window as unknown as { __ENV__?: Record<string, string> }).__ENV__
    return env !== undefined && typeof env.VITE_MCP_URL === "string"
  })
  expect(envOk).toBe(true)
})
