// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test"

/**
 * E-13 — Knowledge Stats endpoint performance regression guard.
 *
 * ``/observability/knowledge-stats`` is the F9 hero's data source.
 * Fires 20 sequential requests and asserts the 95th percentile
 * against a generous 250 ms budget — gross-regression catcher
 * rather than a precise SLO probe (true SLO probing lives in
 * benchmark_slo).
 */
test("E-13 Knowledge Stats endpoint p95 under regression budget", async ({ request }) => {
  // Prime the Redis cache.
  await request.get("/api/mcp/observability/knowledge-stats")

  const samples: number[] = []
  for (let i = 0; i < 20; i++) {
    const t0 = Date.now()
    const r = await request.get("/api/mcp/observability/knowledge-stats")
    expect(r.status()).toBe(200)
    samples.push(Date.now() - t0)
  }
  samples.sort((a, b) => a - b)
  const p95 = samples[Math.floor(samples.length * 0.95)]
  // Generous 250 ms budget (the SLO is 50 ms — but E2E adds
  // Playwright + nginx + container hop overhead).
  expect(p95).toBeLessThan(250)
})
