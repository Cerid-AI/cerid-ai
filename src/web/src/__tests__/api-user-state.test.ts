// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

vi.stubEnv("VITE_MCP_URL", "http://test-mcp:8888")
vi.stubEnv("VITE_CERID_API_KEY", "")

const {
  fetchUserState, fetchSyncedConversations, syncConversation,
  syncConversationsBulk, deleteConversationSync, syncPreferences,
} = await import("@/lib/api")

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  })
}

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch({}))
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

// ---------------------------------------------------------------------------
// User State Sync API
// ---------------------------------------------------------------------------

describe("fetchUserState", () => {
  it("returns parsed user state", async () => {
    const state = { settings: { theme: "dark" }, preferences: { lang: "en" }, conversation_ids: ["c1"] }
    vi.stubGlobal("fetch", mockFetch(state))

    const result = await fetchUserState()
    expect(result.settings).toEqual({ theme: "dark" })
    expect(result.conversation_ids).toEqual(["c1"])
    expect(fetch).toHaveBeenCalledWith(
      "http://test-mcp:8888/user-state",
      expect.objectContaining({ headers: expect.any(Object) }),
    )
  })
})

describe("fetchSyncedConversations", () => {
  it("returns conversations array", async () => {
    const convs = [{ id: "c1", title: "Test", messages: [], createdAt: 1000, updatedAt: 2000, model: "gpt-4" }]
    vi.stubGlobal("fetch", mockFetch({ conversations: convs }))

    const result = await fetchSyncedConversations()
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe("c1")
  })

  it("returns empty array when conversations field is missing", async () => {
    vi.stubGlobal("fetch", mockFetch({}))

    const result = await fetchSyncedConversations()
    expect(result).toEqual([])
  })
})

describe("syncConversation", () => {
  it("sends POST with conversation body", async () => {
    vi.stubGlobal("fetch", mockFetch({ status: "ok" }))
    const conv = { id: "c1", title: "Test", messages: [], createdAt: 1000, updatedAt: 2000, model: "gpt-4" }

    await syncConversation(conv as never)
    expect(fetch).toHaveBeenCalledWith(
      "http://test-mcp:8888/user-state/conversations",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(conv),
      }),
    )
  })
})

describe("syncConversationsBulk", () => {
  it("sends POST with array body", async () => {
    vi.stubGlobal("fetch", mockFetch({ status: "ok" }))
    const convs = [
      { id: "c1", title: "A", messages: [], createdAt: 1, updatedAt: 2, model: "m" },
      { id: "c2", title: "B", messages: [], createdAt: 3, updatedAt: 4, model: "m" },
    ]

    await syncConversationsBulk(convs as never)
    expect(fetch).toHaveBeenCalledWith(
      "http://test-mcp:8888/user-state/conversations/bulk",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(convs),
      }),
    )
  })
})

describe("deleteConversationSync", () => {
  it("sends DELETE with conv ID in URL", async () => {
    vi.stubGlobal("fetch", mockFetch({}))

    await deleteConversationSync("conv-abc-123")
    expect(fetch).toHaveBeenCalledWith(
      "http://test-mcp:8888/user-state/conversations/conv-abc-123",
      expect.objectContaining({ method: "DELETE" }),
    )
  })
})

describe("syncPreferences", () => {
  it("sends PATCH with preferences body", async () => {
    vi.stubGlobal("fetch", mockFetch({}))
    const prefs = { theme: "dark", fontSize: 14 }

    await syncPreferences(prefs)
    expect(fetch).toHaveBeenCalledWith(
      "http://test-mcp:8888/user-state/preferences",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify(prefs),
      }),
    )
  })
})
