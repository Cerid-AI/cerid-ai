// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

// The API key is injected at runtime (docker-entrypoint writes window.__ENV__).
;(globalThis as Record<string, unknown>).__ENV__ = {
  VITE_MCP_URL: "http://test-mcp:8888",
  VITE_CERID_API_KEY: "test-key", // pragma: allowlist secret
}

const { enablePrivateMode, wipePrivateSession } = await import("@/lib/api/settings")

function ok() {
  return vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") })
}

beforeEach(() => vi.stubGlobal("fetch", ok()))
afterEach(() => vi.restoreAllMocks())

describe("private-mode API", () => {
  it("registers the tab when entering a level", async () => {
    const f = ok()
    vi.stubGlobal("fetch", f)
    await enablePrivateMode(4, "tab-1")
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ level: 4, session_id: "tab-1" })
    await enablePrivateMode(1)
    expect(JSON.parse(f.mock.calls[1][1].body)).toEqual({ level: 1 })
  })

  it("the wipe is one authenticated keepalive POST, never a header-less beacon", () => {
    const f = ok()
    vi.stubGlobal("fetch", f)
    const beacon = vi.fn()
    vi.stubGlobal("navigator", { ...navigator, sendBeacon: beacon })
    wipePrivateSession({ sessionId: "tab-1", conversationIds: ["c1", "c2"] })
    expect(beacon).not.toHaveBeenCalled()
    expect(f).toHaveBeenCalledTimes(1)
    const [url, init] = f.mock.calls[0]
    expect(url).toBe("http://test-mcp:8888/settings/private-mode/session-wipe")
    expect(init.method).toBe("POST")
    expect(init.keepalive).toBe(true)
    expect(init.headers["X-API-Key"]).toBe("test-key")
    expect(JSON.parse(init.body)).toEqual({ session_id: "tab-1", conversation_ids: ["c1", "c2"] })
  })
})
