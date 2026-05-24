// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect, suppressFirstRun } from "./fixtures"

/**
 * E-08 — Search palette opens via the Subjects empty-state CTA.
 *
 * The palette is intentionally pane-scoped: keyboard binding lives on
 * Subjects; the "Open search palette" button is the discoverable
 * entry point from the empty state. The palette opens with Liquid
 * Glass treatment and a search input.
 */
test("E-08 search palette opens + accepts input", async ({ page }) => {
  await page.goto("/")
  await suppressFirstRun(page)
  await page.getByRole("button", { name: "Subjects", exact: true }).click()

  await page.getByRole("button", { name: /open search palette/i }).click()

  // The palette mounts as a Radix Dialog. Wait for the dialog
  // container so we don't race against the open animation.
  const dialog = page.getByRole("dialog").first()
  await expect(dialog).toBeVisible({ timeout: 5_000 })

  // Escape closes the dialog. The sidebar's separate
  // "Search conversations" input stays visible — that's a different
  // element entirely, so we scope the close assertion to the dialog
  // itself.
  await page.keyboard.press("Escape")
  await expect(dialog).not.toBeVisible({ timeout: 5_000 })
})
