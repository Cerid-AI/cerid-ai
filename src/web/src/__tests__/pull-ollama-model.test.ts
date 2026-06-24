// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

vi.stubEnv("VITE_MCP_URL", "http://test-mcp:8888")
vi.stubEnv("VITE_CERID_API_KEY", "test-key-123")

const { pullOllamaModel } = await import("@/lib/api")

/** Build a fetch mock whose Response exposes the clone().text() path used by pullOllamaModel. */
function mockFetch(body: string, status = 200) {
  const res = {
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(body),
    clone: () => ({ text: () => Promise.resolve(body) }),
  }
  return vi.fn().mockResolvedValue(res)
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("pullOllamaModel — not_implemented handling", () => {
  it("throws with the backend error message when the body is a not_implemented payload (HTTP 200)", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(
        '{"error":"Quenchforge does not support model pull from this UI.","status":"not_implemented"}',
        200,
      ),
    )
    await expect(pullOllamaModel("llama3.2:3b")).rejects.toThrow(
      "Quenchforge does not support model pull from this UI.",
    )
  })

  it("resolves with the Response on a normal pull body", async () => {
    vi.stubGlobal("fetch", mockFetch('{"status":"success"}', 200))
    const res = await pullOllamaModel("llama3.2:3b")
    expect(res.ok).toBe(true)
  })
})
