// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { test, expect, suppressFirstRun } from "./fixtures"

// Deterministic scene: reduced motion skips camera/ping animation so the
// hover probe doesn't race layout settle.
test.use({ reducedMotion: "reduce" })

/**
 * E-17 — Atlas v2 "Meridian" coverage.
 *
 * Covers the analyst ego-network surface:
 *   - Wiki identity capsule cross-links into Atlas (same-pane goTo)
 *   - Atlas mounts the focal neighborhood (family chrome present)
 *   - Hover over a node raises the entity tooltip (trusted input only —
 *     synthetic events have offsetX 0 and miss sigma's picking)
 *   - Analyst lens radiogroup + hop stepper + type chips operate
 */
test("E-17 Atlas mounts via Wiki cross-link, hovers a node, drives lenses/hops/chips", async ({ page }) => {
  await suppressFirstRun(page)

  // Data-agnostic: E-17 exercises the Wiki→Atlas cross-link + Atlas chrome for
  // ANY entity with a neighborhood, not a hardcoded one. Skip only when the KB
  // is genuinely empty (nothing to cross-link from).
  const entitiesResp = await page.request.get("/api/mcp/wiki/entities?limit=1")
  const entities = entitiesResp.ok() ? await entitiesResp.json() : []
  test.skip(!Array.isArray(entities) || entities.length === 0, "no wiki entities seeded in the KB")

  await page.goto("/")
  await page.getByRole("button", { name: "Subjects", exact: true }).click()
  await page.getByRole("tab", { name: "Wiki" }).click()

  // Open the A–Z index and pick the first entity (deterministic, name-agnostic).
  // Entity rows carry a data-completeness attribute — a stable row selector
  // that domain-filter/action buttons don't.
  await page.getByRole("button", { name: /A.Z Index/ }).click()
  const firstEntity = page.locator("button[data-completeness]").first()
  await expect(firstEntity).toBeVisible({ timeout: 15_000 })
  await firstEntity.click()

  // Jump via the identity capsule. Two "Open … in Atlas" affordances render
  // (the infobox capsule + the MiniGraph header); either takes us to Atlas.
  const capsuleLink = page.getByRole("button", { name: /Open .+ in Atlas/ }).first()
  await expect(capsuleLink).toBeVisible({ timeout: 15_000 })
  await capsuleLink.click()

  // Atlas mounts on the focal entity; FA2 settle can take a few seconds.
  const atlas = page.getByRole("application", { name: /Atlas view of .+ neighborhood/ })
  await expect(atlas).toBeVisible({ timeout: 20_000 })
  const canvas = atlas.locator("canvas").first()
  await expect(canvas).toBeVisible({ timeout: 20_000 })

  // Family chrome present.
  await expect(page.getByRole("radiogroup", { name: "Analysis lens" })).toBeVisible()
  await expect(page.getByRole("group", { name: "Hop depth" })).toBeVisible()
  await expect(page.getByRole("group", { name: "Entity type filter" })).toBeVisible({ timeout: 15_000 })

  // Wait for layout to settle (the "Computing layout" status disappears).
  await expect(page.getByText(/Computing layout/)).toHaveCount(0, { timeout: 30_000 })

  // Trusted-input hover sweep through the canvas center until the entity
  // tooltip ("N mentions · Click to pin") appears.
  const box = await canvas.boundingBox()
  if (!box) throw new Error("canvas has no bounding box")
  // Dense grid sweep: a 2-hop neighborhood is a diffuse cloud of small
  // nodes, so single-point probes miss; rake across the middle band.
  const tooltip = page.getByText("Click to pin")
  let hovered = false
  outer: for (const fy of [0.5, 0.4, 0.6, 0.3, 0.7]) {
    for (const fx of [0.3, 0.38, 0.46, 0.54, 0.62, 0.7]) {
      const x = box.x + box.width * fx
      const y = box.y + box.height * fy
      await page.mouse.move(x - 10, y, { steps: 2 })
      await page.mouse.move(x, y, { steps: 3 })
      try {
        await expect(tooltip).toBeVisible({ timeout: 600 })
        hovered = true
        break outer
      } catch {
        // miss — next probe
      }
    }
  }
  expect(hovered, "hovering the neighborhood should raise the entity tooltip").toBe(true)

  // Lens radiogroup: activate + deactivate one analyst lens. force: the
  // agent-console footer overlay trips Playwright's hit-target check.
  const lensGroup = page.getByRole("radiogroup", { name: "Analysis lens" })
  await lensGroup.getByRole("radio").first().click({ force: true })
  await expect(canvas).toBeVisible()
  await lensGroup.getByRole("radio").first().click({ force: true })

  // Hop stepper: drop to 1 hop (refetch) and back to 2.
  await page.getByRole("group", { name: "Hop depth" }).getByRole("button", { name: "1", exact: true }).click({ force: true })
  await expect(page.getByText(/Computing layout/)).toHaveCount(0, { timeout: 30_000 })
  await expect(canvas).toBeVisible({ timeout: 15_000 })
  await page.getByRole("group", { name: "Hop depth" }).getByRole("button", { name: "2", exact: true }).click({ force: true })
  await expect(page.getByText(/Computing layout/)).toHaveCount(0, { timeout: 30_000 })
  await expect(canvas).toBeVisible({ timeout: 15_000 })

  // Type chips: toggle the first chip on and off.
  const chip = page.getByRole("group", { name: "Entity type filter" }).getByRole("button").first()
  await chip.click({ force: true })
  await expect(chip).toHaveAttribute("aria-pressed", "true")
  await chip.click({ force: true })
  await expect(chip).toHaveAttribute("aria-pressed", "false")
})
