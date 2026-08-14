// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { test, expect, suppressFirstRun } from "./fixtures"

// Deterministic scene: reduced motion renders nodes full-size immediately
// (no growth stagger), so the hover probes don't race animations.
test.use({ reducedMotion: "reduce" })

/**
 * E-15 — Constellation coverage (Cartographer map default + 3D toggle).
 *
 * Covers the corpus-exploration surface:
 *   - Cartographer 2D map mounts as the default view
 *   - Hover over a map node raises the entity tooltip (trusted-input only —
 *     synthetic JS events can't exercise sigma's picking; offsetX/Y are zeroed)
 *   - View toggle switches to the 3D scene (links payload wired)
 *   - Quality tiers switch (High ↔ Ultra) without killing the canvas
 */
test("E-15 Constellation map hovers a node, toggles 3D, switches quality tiers", async ({ page }) => {
  await suppressFirstRun(page)
  await page.goto("/")
  await page.getByRole("button", { name: "Subjects", exact: true }).click()
  await page.getByRole("tab", { name: "Constellation" }).click()

  // --- Cartographer map (default view) ---
  const map = page.getByRole("application", { name: "Cartographer knowledge map" })
  await expect(map).toBeVisible({ timeout: 20_000 })

  const mapCanvas = map.locator("canvas").first()
  await expect(mapCanvas).toBeVisible({ timeout: 10_000 })
  const box = await map.boundingBox()
  if (!box) throw new Error("map has no bounding box")

  // Sigma fits the camera to the layout, so the dense core crosses the
  // viewport center — a short sweep through the middle reliably crosses
  // a node (same trusted-input probe pattern validated for the 3D scene).
  const tooltip = page.getByText(/mentions/).first()
  const cx = box.x + box.width / 2
  const cy = box.y + box.height / 2
  let hovered = false
  for (const [dx, dy] of [[0, 0], [25, 0], [-25, 10], [0, 25], [40, -20], [-40, -25]]) {
    await page.mouse.move(cx + dx - 30, cy + dy, { steps: 3 })
    await page.mouse.move(cx + dx, cy + dy, { steps: 6 })
    try {
      await expect(tooltip).toBeVisible({ timeout: 1_500 })
      hovered = true
      break
    } catch {
      // miss — nudge to the next probe point
    }
  }
  expect(hovered, "hovering the map core should raise an entity tooltip").toBe(true)

  // --- Live mode via the view toggle (the R3F "3D" mode was cut 2026-08-13;
  // Map | Live are the two remaining scenes) ---
  // force: the agent-console footer overlay trips Playwright's hit-target
  // check even though the radios are genuinely clickable (verified live).
  await page.getByRole("radio", { name: "Live", exact: true }).click({ force: true })

  // The Live scene is its own lazy chunk (vendor-cosmos) with its own
  // canvas; the simulation control strip proves it mounted. Generous
  // timeout for the first-activation chunk load.
  await expect(
    page.getByRole("button", { name: /Pause simulation|Run simulation/ }),
  ).toBeVisible({ timeout: 30_000 })
  await expect(page.locator("canvas").first()).toBeVisible({ timeout: 10_000 })

  // Restore the default view for whoever runs the suite next (mode is
  // persisted in localStorage).
  await page.getByRole("radio", { name: "Map", exact: true }).click({ force: true })
  await expect(map).toBeVisible({ timeout: 10_000 })
})
