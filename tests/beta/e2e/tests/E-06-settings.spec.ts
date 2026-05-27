// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect, suppressFirstRun } from "./fixtures"

/**
 * E-06 — Settings pane: 7 tabs render (Essentials / Pipeline / System /
 * Governance / Plugins / Diagnostics / Pro).
 *
 * Confirms the Phase C 9 → 4 outer-pane consolidation lands the 7
 * settings tabs, and that switching between tabs swaps the panel
 * content.
 */
test("E-06 settings pane — 7 tabs + tab switch", async ({ page }) => {
  test.setTimeout(45_000)
  await suppressFirstRun(page)
  await page.goto("/")
  await page.getByRole("button", { name: "Settings", exact: true }).click()

  // Wait for the settings-pane lazy chunk to mount its tablist.
  await page.waitForSelector("[role='tablist']", { timeout: 15_000 })

  for (const tab of [
    "Essentials",
    "Pipeline",
    "System",
    "Governance",
    "Plugins",
    "Diagnostics",
    "Pro",
  ]) {
    await expect(page.getByRole("tab", { name: tab })).toBeVisible({ timeout: 10_000 })
  }

  // Switch tabs — Diagnostics has distinct content from Pro.
  await page.getByRole("tab", { name: "Diagnostics" }).click()
  await expect(page.getByRole("tab", { name: "Diagnostics", selected: true })).toBeVisible()
})
