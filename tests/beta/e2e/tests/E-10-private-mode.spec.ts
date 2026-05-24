// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test"

/**
 * E-10 — Private Mode L4 session-wipe contract.
 *
 * The Private Mode engine (shipped v0.84.0; UX polished v0.93.4) has
 * four levels; L4 wipes the session on close via
 * navigator.sendBeacon('/settings/private-mode/session-wipe').
 *
 * This test invokes the wipe endpoint directly (the FE wires the same
 * call into beforeunload) and confirms the server's idempotent
 * Redis-flag clear behaves correctly across two back-to-back calls.
 */
test("E-10 Private Mode session-wipe idempotent", async ({ request }) => {
  const conversationId = `e2e-private-${Date.now()}`

  const first = await request.post("/api/mcp/settings/private-mode/session-wipe", {
    headers: { "Content-Type": "application/json" },
    data: { conversation_id: conversationId },
  })
  expect(first.ok()).toBe(true)

  // Idempotent — second wipe must not error.
  const second = await request.post("/api/mcp/settings/private-mode/session-wipe", {
    headers: { "Content-Type": "application/json" },
    data: { conversation_id: conversationId },
  })
  expect(second.ok()).toBe(true)
})
