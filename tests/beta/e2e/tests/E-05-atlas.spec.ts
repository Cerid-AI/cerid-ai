// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect, suppressFirstRun } from "./fixtures"

/**
 * E-05 — Subjects pane tabs render (Atlas / Constellation / Timeline / Wiki).
 *
 * The Subjects pane is the K-program / Phase A+B+M consolidation. The
 * default-selected tab is Atlas; the empty state asks the user to
 * pick a focal entity via the "Open search palette" CTA. We verify
 * all 4 tab modes are reachable + the empty-state CTA is wired.
 *
 * Covers: vendor-atlas lazy chunk loads, Subjects 4-mode tabs, empty
 * state copy.
 */
test("E-05 Subjects pane — 4 visualization modes render", async ({ page }) => {
  await page.goto("/")
  await suppressFirstRun(page)
  await page.getByRole("button", { name: "Subjects", exact: true }).click()

  // 4-tab strip: Atlas, Constellation, Timeline, Wiki.
  for (const mode of ["Atlas", "Constellation", "Timeline", "Wiki"]) {
    await expect(page.getByRole("tab", { name: mode })).toBeVisible({ timeout: 15_000 })
  }

  // Atlas is the default-selected mode.
  await expect(page.getByRole("tab", { name: "Atlas", selected: true })).toBeVisible()

  // Empty-state CTA wires into the search palette.
  await expect(page.getByRole("button", { name: /open search palette/i })).toBeVisible()
})
