// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect, suppressFirstRun } from "./fixtures"

// Deterministic canvas: reduced motion disables LOD crossfades so the
// hover probe doesn't race transitions.
test.use({ reducedMotion: "reduce" })

/**
 * E-16 — Timeline (Stratigraph) coverage.
 *
 * Covers the time-dominant knowledge-graph surface:
 *   - Stratigraph mounts with mention totals (strata payload wired)
 *   - Hover over a stratum band raises the bucket tooltip with an exact
 *     count (trusted-input only — synthetic events have offsetX 0)
 *   - Lens radiogroup switches without killing the canvas
 *   - Entity-type chip filter applies (aria-pressed toggles)
 *   - Period tabs refetch (totals label updates)
 */
test("E-16 Stratigraph mounts, hovers a stratum, switches lens and filters", async ({ page }) => {
  await suppressFirstRun(page)
  await page.goto("/")
  await page.getByRole("button", { name: "Subjects", exact: true }).click()
  await page.getByRole("tab", { name: "Timeline" }).click()

  // The application region proves the strata payload arrived (totals > 0).
  const app = page.getByRole("application", { name: /Stratigraph of [\d,]+ mentions/ })
  await expect(app).toBeVisible({ timeout: 20_000 })

  const canvas = app.locator("canvas").first()
  await expect(canvas).toBeVisible({ timeout: 10_000 })
  const box = await canvas.boundingBox()
  if (!box) throw new Error("canvas has no bounding box")

  // Strata bands have a continuous 2px floor and bursts near the data's
  // dense columns; sweep along a horizontal line through the upper third
  // (the top stratum's band) until the bucket tooltip appears.
  const tooltip = page.getByText(/ · \d{4}-\d{2}-\d{2} · \d+ mention/)
  let hovered = false
  for (const fx of [0.72, 0.6, 0.5, 0.8, 0.4, 0.35]) {
    for (const fy of [0.12, 0.2, 0.3, 0.45, 0.6]) {
      const x = box.x + box.width * fx
      const y = box.y + box.height * fy
      await page.mouse.move(x - 20, y, { steps: 2 })
      await page.mouse.move(x, y, { steps: 4 })
      try {
        await expect(tooltip).toBeVisible({ timeout: 700 })
        hovered = true
        break
      } catch {
        // miss — next probe
      }
    }
    if (hovered) break
  }
  expect(hovered, "hovering a stratum should raise the bucket tooltip with a count").toBe(true)

  // Lens switching keeps the canvas alive. force: the agent-console footer
  // overlay trips Playwright's hit-target check on these radios.
  await page.getByRole("radio", { name: "Trust" }).click({ force: true })
  await expect(canvas).toBeVisible({ timeout: 5_000 })
  await page.getByRole("radio", { name: "Types" }).click({ force: true })
  await expect(canvas).toBeVisible({ timeout: 5_000 })
  await page.getByRole("radio", { name: "Clusters" }).click({ force: true })
  await expect(canvas).toBeVisible({ timeout: 5_000 })

  // Entity-type chip filter toggles pressed state and thins the strata
  // without unmounting the canvas.
  const chip = page.getByRole("group", { name: "Filter by entity type" }).getByRole("button").first()
  await chip.click({ force: true })
  await expect(chip).toHaveAttribute("aria-pressed", "true")
  await expect(canvas).toBeVisible()
  await chip.click({ force: true })
  await expect(chip).toHaveAttribute("aria-pressed", "false")

  // Period switch refetches; the totals region label re-resolves.
  await page.getByTestId("timeline-period-7d").click({ force: true })
  await expect(
    page.getByRole("application", { name: /Stratigraph of [\d,]+ mentions/ }),
  ).toBeVisible({ timeout: 20_000 })
  // Restore the default for whoever runs the suite next.
  await page.getByTestId("timeline-period-30d").click({ force: true })
  await expect(
    page.getByRole("application", { name: /Stratigraph of [\d,]+ mentions/ }),
  ).toBeVisible({ timeout: 20_000 })
})
