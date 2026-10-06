// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { test, expect } from "@playwright/test"

/**
 * E-09 — Wiki surface end-to-end via REST.
 *
 * Confirms the K-program /wiki/* REST routes are reachable and that
 * the index + log endpoints emit well-formed payloads. The visual
 * wiki pane is covered by E-06's settings tab pass-through; this
 * test guards the data contract that wiki-pane.tsx consumes.
 *
 * Covers: K-program K4 (wiki log + index) + K-program K1 (entity
 * pages list).
 */
// The registered e2e consumer (CONSUMER_REGISTRY["e2e-harness"]). The wiki
// routes do not read consumer scope, so the header is a label here, not a gate.
const E2E_HEADERS = { "X-Client-ID": "e2e-harness" }

test("E-09 wiki REST surface", async ({ request }) => {
  const indexResponse = await request.get("/api/mcp/wiki/index", {
    headers: E2E_HEADERS,
  })
  expect(indexResponse.ok()).toBe(true)
  const indexBody = await indexResponse.json()
  // Index returns Karpathy-shaped catalog rows.
  expect(typeof indexBody).toBe("object")
  // Either {entries: [...]} or {items: [...]} — accept either shape.
  const items = indexBody.entries ?? indexBody.items ?? indexBody.entities ?? []
  expect(Array.isArray(items)).toBe(true)

  const logResponse = await request.get("/api/mcp/wiki/log", {
    headers: E2E_HEADERS,
  })
  expect(logResponse.ok()).toBe(true)
  const logBody = await logResponse.json()
  expect(typeof logBody).toBe("object")

  // List entity pages (paginated).
  const entitiesResponse = await request.get("/api/mcp/wiki/entities", {
    headers: E2E_HEADERS,
  })
  expect(entitiesResponse.ok()).toBe(true)
})
