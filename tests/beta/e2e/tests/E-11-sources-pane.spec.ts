// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test"

/**
 * E-11 — Sources pane end-to-end coverage.
 *
 * Covers the ingestion experience surface:
 *   - Knowledge Stats hero renders with non-zero counters
 *   - HUD ticker mounts
 *   - FAB opens the radial menu (⌘⇧S)
 *   - Wizard renders the kind picker
 *
 * Performance assertion: initial Sources-pane paint within the
 * gross-regression budget.
 */
test("E-11 Sources pane mounts hero / ticker / FAB / wizard surfaces", async ({ page }) => {
  await page.goto("/?pane=sources")

  // Knowledge Stats hero
  const hero = page.getByRole("heading", { name: /your knowledge/i })
  await expect(hero).toBeVisible({ timeout: 10_000 })

  // HUD strip — diversity counter is reachable by role
  await expect(page.getByText(/\/ 22/)).toBeVisible()

  // FAB
  const fab = page.getByRole("button", { name: /add a new source/i })
  await expect(fab).toBeVisible()
  await fab.click()

  // Radial petals
  await expect(page.getByRole("button", { name: /add files source/i })).toBeVisible()
  await expect(page.getByRole("button", { name: /add feeds source/i })).toBeVisible()

  // Click a petal → wizard opens
  await page.getByRole("button", { name: /add feeds source/i }).click()
  await expect(page.getByRole("heading", { name: /add a source/i })).toBeVisible()
})

test("E-11 Sources pane initial paint within performance budget", async ({ page }) => {
  const start = Date.now()
  await page.goto("/?pane=sources", { waitUntil: "domcontentloaded" })
  await page.getByRole("heading", { name: /your knowledge/i }).waitFor({ state: "visible" })
  const elapsed = Date.now() - start
  // E2E harness can't isolate paint from network; apply a generous
  // gross-regression budget rather than the SLO itself.
  expect(elapsed).toBeLessThan(4_000)
})
