// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

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

  // The application region proves a NON-EMPTY strata payload arrived.
  // [1-9] leading digit: "Stratigraph of 0 mentions" satisfied [\d,]+ and
  // let the whole spec run against a dead-empty canvas (2026-07-10) — an
  // empty corpus must fail here loudly (or the pane shows its empty state
  // and this locator times out, equally loud).
  const app = page.getByRole("application", { name: /Stratigraph of [1-9][\d,]* mentions/ })
  await expect(app).toBeVisible({ timeout: 20_000 })

  const canvas = app.locator("canvas").first()
  await expect(canvas).toBeVisible({ timeout: 10_000 })
  const box = await canvas.boundingBox()
  if (!box) throw new Error("canvas has no bounding box")

  // Bucket hit-entries exist at every (bucket-column x, stratum-midline y)
  // pair regardless of count, with a 20px hit radius — but midlines are
  // sqrt-scaled and data-dependent, so fixed rows rot as the KB evolves
  // (drift found 2026-07-10). A VERTICAL descent at a bucket column is
  // structurally guaranteed to cross every midline; stop spacing stays
  // under the hit radius. Try a few x phases to land within a column.
  const tooltip = page.getByText(/ · \d{4}-\d{2}-\d{2} · \d+ mention/)
  let hovered = false
  const stepPx = 16
  for (const fx of [0.6, 0.62, 0.58, 0.64, 0.56]) {
    const x = box.x + box.width * fx
    await page.mouse.move(x, box.y + 2, { steps: 2 })
    for (let y = box.y + 8; y < box.y + box.height - 4; y += stepPx) {
      await page.mouse.move(x, y, { steps: 2 })
      await page.waitForTimeout(80)
      if (await tooltip.isVisible().catch(() => false)) {
        hovered = true
        break
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

  // Domain lens partitions lanes by taxonomy domain and renders the legend.
  await page.getByRole("radio", { name: "Domains" }).click({ force: true })
  await expect(canvas).toBeVisible({ timeout: 5_000 })
  await expect(
    page.getByText(/^(Coding|General|Research|Conversations|Projects)$/).first(),
  ).toBeVisible({ timeout: 10_000 })

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
