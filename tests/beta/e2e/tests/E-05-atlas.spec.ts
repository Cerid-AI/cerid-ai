// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect, suppressFirstRun } from "./fixtures"

/**
 * E-05 — Subjects pane tabs render (Atlas / Constellation / Timeline / Wiki).
 *
 * The default-selected tab is Atlas, which since Cycle 4 (STRATA) opens on
 * the knowledge-decomposition icicle: domains tier first, drill-down to
 * communities and entities, with the ego Neighborhood view demoted to an
 * explicit leaf action. We verify all 4 tab modes are reachable + the
 * decomposition default renders its domain tier.
 */
test("E-05 Subjects pane — 4 visualization modes render", async ({ page }) => {
  await suppressFirstRun(page)
  await page.goto("/")
  await page.getByRole("button", { name: "Subjects", exact: true }).click()

  // 4-tab strip: Atlas, Constellation, Timeline, Wiki.
  for (const mode of ["Atlas", "Constellation", "Timeline", "Wiki"]) {
    await expect(page.getByRole("tab", { name: mode })).toBeVisible({ timeout: 15_000 })
  }

  // Atlas is the default-selected mode.
  await expect(page.getByRole("tab", { name: "Atlas", selected: true })).toBeVisible()

  // The decomposition icicle is the Atlas default: region + domains tier.
  await expect(page.getByRole("region", { name: "Knowledge decomposition" })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole("group", { name: "Domains" })).toBeVisible()
})
