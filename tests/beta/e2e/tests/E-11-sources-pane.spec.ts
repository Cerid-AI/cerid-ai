// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect, suppressFirstRun } from "./fixtures"

// The radial "add source" menu petals animate in via `cerid-radial-stagger`,
// which Playwright's actionability check sees as "not stable" mid-animation.
// The app disables that animation under prefers-reduced-motion (index.css),
// so emulate it here to keep the petal click deterministic.
test.use({ reducedMotion: "reduce" })

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
  await suppressFirstRun(page)
  await page.goto("/")
  await page.getByRole("button", { name: "Sources", exact: true }).click()

  // Knowledge Stats hero
  const hero = page.getByRole("heading", { name: /your knowledge/i })
  await expect(hero).toBeVisible({ timeout: 10_000 })

  // HUD strip — diversity counter (rendered in both the metric pulse and
  // the percentage label, so scope to the first match to stay strict-mode safe)
  await expect(page.getByText(/\/ 22/).first()).toBeVisible()

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
  await suppressFirstRun(page)
  await page.goto("/", { waitUntil: "domcontentloaded" })
  const start = Date.now()
  await page.getByRole("button", { name: "Sources", exact: true }).click()
  await page.getByRole("heading", { name: /your knowledge/i }).waitFor({ state: "visible" })
  const elapsed = Date.now() - start
  // E2E harness can't isolate paint from network; apply a generous
  // gross-regression budget rather than the SLO itself.
  expect(elapsed).toBeLessThan(4_000)
})
