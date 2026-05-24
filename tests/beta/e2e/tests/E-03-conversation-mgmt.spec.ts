// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect, suppressFirstRun } from "./fixtures"

/**
 * E-03 — Conversation management (archive + FLIP slide).
 *
 * Verifies the lib/flip.ts wire-in: clicking the archive button on a
 * conversation row removes it from the visible list. The FLIP play
 * itself isn't programmatically verified (DOM positions during
 * transition are hard to assert), but the post-mutation final state
 * is.
 *
 * Covers: Phase 7's FLIP wire-in + the conversation-list utility.
 */
test("E-03 conversation archive removes row", async ({ page }) => {
  await page.goto("/")
  await suppressFirstRun(page)

  // Need at least one conversation in the list. Skip if none.
  const firstRow = page.locator("[data-flip-item]").first()
  const rowCount = await page.locator("[data-flip-item]").count()
  test.skip(rowCount === 0, "No conversations in list; archive flow needs at least one")

  // Hover to reveal the per-row action buttons.
  await firstRow.hover()
  const archiveBtn = firstRow.getByRole("button", { name: /^archive conversation$/i })
  await expect(archiveBtn).toBeVisible({ timeout: 5_000 })

  const beforeCount = rowCount
  await archiveBtn.click()

  // The row count drops by one after the FLIP play completes.
  await expect(page.locator("[data-flip-item]")).toHaveCount(beforeCount - 1, {
    timeout: 5_000,
  })
})
