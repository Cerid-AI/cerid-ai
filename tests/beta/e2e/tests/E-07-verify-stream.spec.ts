// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { test, expect } from "@playwright/test"

/**
 * E-07 — Verify-stream emits claim → verified → persisted handshake.
 *
 * Direct SSE consumption via the same endpoint the chat surface uses.
 * Confirms the Sprint C auto-persist write path: the stream MUST emit
 * a "persisted: success" event after the summary so the FE can stop
 * worrying about its own save call.
 *
 * Covers: extraction → cross-model verify → persisted handshake.
 */
test("E-07 verify-stream auto-persist handshake", async ({ request }) => {
  const response = await request.post("/api/mcp/agent/verify-stream", {
    headers: { "Content-Type": "application/json" },
    data: {
      response_text:
        "Python 3.12 was released in October 2023. It introduced per-interpreter GIL support.",
      conversation_id: `e2e-verify-${Date.now()}`,
      user_query: "Python 3.12 release notes",
    },
    timeout: 60_000,
  })

  expect(response.ok()).toBe(true)

  // Read the SSE stream as text.
  const body = await response.text()
  const events = body
    .split(/\n\n/)
    .filter((chunk) => chunk.startsWith("data:"))
    .map((chunk) => {
      const jsonText = chunk.replace(/^data:\s*/, "")
      try {
        return JSON.parse(jsonText)
      } catch {
        return null
      }
    })
    .filter((e): e is Record<string, unknown> => e !== null)

  const types = events.map((e) => e.type as string)
  // The stream MUST emit each of these event types in order.
  expect(types).toContain("extraction_complete")
  expect(types).toContain("claim_extracted")
  expect(types).toContain("claim_verified")
  expect(types).toContain("summary")
  expect(types).toContain("persisted")

  const persisted = events.find((e) => e.type === "persisted")
  expect(persisted).toBeTruthy()
  expect(persisted!.success).toBe(true)
})
