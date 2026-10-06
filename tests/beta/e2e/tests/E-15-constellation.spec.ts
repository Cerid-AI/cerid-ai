// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { request as apiRequest } from "@playwright/test"
import { test, expect, suppressFirstRun } from "./fixtures"

// Deterministic scene: reduced motion renders nodes full-size immediately
// (no growth stagger), so the hover probes don't race animations.
test.use({ reducedMotion: "reduce" })

/**
 * E-15 — Constellation coverage (Cartographer map default + Live toggle).
 *
 * Covers the corpus-exploration surface:
 *   - Cartographer 2D map mounts as the default view
 *   - Hover over a map node raises the entity tooltip (trusted-input only —
 *     synthetic JS events can't exercise sigma's picking; offsetX/Y are zeroed)
 *   - View toggle switches to the Live simulation scene and back
 */

// The map is a computed artifact: GET /graph/map serves the coordinates the
// compute_umap_3d scheduler job writes. An empty map renders no canvas, has
// no node to hover, and Live mode shows "No graph to simulate yet" — every
// assertion below fails on a stack where the job has never run, and nothing
// else in the suite ran it. Seed it through the scheduler's manual trigger
// (POST /scheduler/jobs/{id}/run, the same call Settings → Diagnostics makes)
// and wait for the map to fill. The job finishes its layouts and a bounded
// LLM community-summary batch before it clears the serving cache, so the
// wait is long; a map that already has entities is left alone.
const MAP_SEED_TIMEOUT_MS = 10 * 60_000
const MAP_POLL_MS = 5_000

test.beforeAll(async () => {
  test.setTimeout(MAP_SEED_TIMEOUT_MS + 30_000)
  const { baseURL, extraHTTPHeaders } = test.info().project.use
  const api = await apiRequest.newContext({ baseURL, extraHTTPHeaders })
  try {
    const readMap = async (): Promise<{ count: number; isolated_count: number }> => {
      const response = await api.get("/api/mcp/graph/map")
      if (!response.ok()) {
        throw new Error(`GET /graph/map failed: HTTP ${response.status()} ${await response.text()}`)
      }
      const body = await response.json()
      return { count: body.count ?? 0, isolated_count: body.isolated_count ?? 0 }
    }

    if ((await readMap()).count > 0) return

    const trigger = await api.post("/api/mcp/scheduler/jobs/compute_umap_3d/run")
    // 200 {status: started | collapsed_into_pending} and 409 (already running)
    // both mean a run is in flight; 404 means the job is unknown or disabled.
    if (!trigger.ok() && trigger.status() !== 409) {
      throw new Error(`compute_umap_3d trigger failed: HTTP ${trigger.status()} ${await trigger.text()}`)
    }

    const deadline = Date.now() + MAP_SEED_TIMEOUT_MS
    let last = await readMap()
    while (last.count === 0 && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, MAP_POLL_MS))
      last = await readMap()
    }
    if (last.count === 0) {
      throw new Error(
        `/graph/map is still empty ${MAP_SEED_TIMEOUT_MS / 1000}s after compute_umap_3d ` +
          `(count=${last.count}, isolated_count=${last.isolated_count}). Entities without a ` +
          "community_id are peripheral and hidden; run the community_refresh job first.",
      )
    }
  } finally {
    await api.dispose()
  }
})

test("E-15 Constellation map hovers a node, switches to Live and back", async ({ page }) => {
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
