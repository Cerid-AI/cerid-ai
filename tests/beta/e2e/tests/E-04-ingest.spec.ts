// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test"

/**
 * E-04 — Ingest text + verify it's queryable.
 *
 * Drives the ingest path via the public /sdk/v1/ingest endpoint
 * (the same endpoint the Sources pane uses) and confirms the artifact
 * is searchable within a short window.
 *
 * Covers: ingest pipeline (Neo4j commit + Chroma flip), search round
 * trip.
 */
test("E-04 ingest + search round trip", async ({ request }) => {
  const marker = `e2e-marker-${Date.now()}`
  const ingestResponse = await request.post("/api/mcp/sdk/v1/ingest", {
    headers: { "X-Client-ID": "e2e-test", "Content-Type": "application/json" },
    data: {
      content: `${marker} — the unique phrase that lets E-04 verify the artifact actually landed in the KB.`,
      domain: "projects",
      tags: "e2e-marker",
    },
  })

  expect(ingestResponse.status()).toBeLessThan(300)
  const ingestBody = await ingestResponse.json()
  expect(ingestBody.status).toMatch(/success|duplicate/)
  expect(ingestBody.artifact_id).toBeTruthy()

  // Search for the marker. Allow up to 5s for Chroma to commit the embedding.
  let foundResults = 0
  for (let attempt = 0; attempt < 5; attempt++) {
    const searchResponse = await request.post("/api/mcp/sdk/v1/search", {
      headers: { "X-Client-ID": "e2e-test", "Content-Type": "application/json" },
      data: { query: marker, domain: "projects", top_k: 5 },
    })
    if (searchResponse.ok()) {
      const searchBody = await searchResponse.json()
      foundResults = searchBody.total_results ?? 0
      if (foundResults > 0) break
    }
    await new Promise((r) => setTimeout(r, 1_000))
  }
  expect(foundResults).toBeGreaterThan(0)
})
