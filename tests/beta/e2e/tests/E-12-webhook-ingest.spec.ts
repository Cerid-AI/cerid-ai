// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { test, expect } from "@playwright/test"

/**
 * E-12 — Webhook receiver end-to-end with adapter-recipe routing.
 *
 * Covers:
 *   - Create a webhook source via POST /sources
 *   - Fetch the receiver URL via GET /sources/{id}/webhook-url
 *   - POST a Slack-shaped payload to the receiver
 *   - Confirm the adapter recipe normalized the payload (Slack →
 *     chat_capture normalized_count=1)
 *   - DELETE the source to leave the corpus clean
 */
test("E-12 webhook receiver normalizes Slack payload via chat_capture recipe", async ({ request }) => {
  // Create webhook source with provider=slack so the chat_capture
  // recipe fires on receipt.
  const createRes = await request.post("/api/mcp/sources", {
    headers: { "Content-Type": "application/json" },
    data: {
      kind: "webhook",
      display_name: "E-12 Slack",
      config: { provider: "slack" },
    },
  })
  expect(createRes.status()).toBe(201)
  const created = await createRes.json()
  const sourceId = created.id

  try {
    const urlRes = await request.get(`/api/mcp/sources/${sourceId}/webhook-url`)
    expect(urlRes.status()).toBe(200)
    const { url } = await urlRes.json()
    expect(url).toContain("/sdk/v1/ingest/webhook/")

    // The Playwright base URL maps the /api/mcp prefix to the MCP
    // server; rewrite the receiver URL accordingly so requests stay
    // inside the same origin during E2E.
    const proxiedUrl = url.replace(/^https?:\/\/[^/]+/, "/api/mcp")

    const payload = {
      event: {
        type: "message",
        user: "U_E12",
        channel: "e2e-channel",
        text: "Hello from E-12 — Slack normalized payload check",
        ts: "1700000000.0",
      },
    }
    const ingestRes = await request.post(proxiedUrl, {
      headers: { "Content-Type": "application/json" },
      data: payload,
    })
    expect(ingestRes.status()).toBe(202)
    const ingestBody = await ingestRes.json()
    expect(ingestBody.status).toBe("accepted")
    expect(ingestBody.normalized_count).toBe("1")
  } finally {
    await request.delete(`/api/mcp/sources/${sourceId}`)
  }
})
