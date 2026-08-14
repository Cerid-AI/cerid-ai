// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

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
  it("parses the REAL backend shape — a bare JSON array (UX-06)", async () => {
    // GET /user-state/conversations returns a bare list
    // (app.routers.user_state.list_conversations → read_conversations).
    // The old parser read `data.conversations` and got undefined → [] on
    // every hydration, so the sidebar said "No conversations yet" despite
    // prior-session chats syncing fine.
    const convs = [{ id: "c1", title: "Test", messages: [], createdAt: 1000, updatedAt: 2000, model: "gpt-4" }]
    vi.stubGlobal("fetch", mockFetch(convs))

    const result = await fetchSyncedConversations()
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe("c1")
  })

  it("tolerates a wrapped {conversations: [...]} shape", async () => {
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

  // WB-46: a swallowed non-2xx here is how a conversation the caller believes
  // is synced silently never reaches the server (E1 CR-092).
  it("rejects on a non-2xx response", async () => {
    vi.stubGlobal("fetch", mockFetch({ error_code: "SERVER_ERROR", message: "boom" }, 500))
    const conv = { id: "c1", title: "Test", messages: [], createdAt: 1000, updatedAt: 2000, model: "gpt-4" }

    await expect(syncConversation(conv as never)).rejects.toThrow()
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

  it("rejects on a non-2xx response", async () => {
    vi.stubGlobal("fetch", mockFetch({ error_code: "SERVER_ERROR", message: "boom" }, 500))
    const convs = [{ id: "c1", title: "A", messages: [], createdAt: 1, updatedAt: 2, model: "m" }]

    await expect(syncConversationsBulk(convs as never)).rejects.toThrow()
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

  it("rejects on a non-2xx response", async () => {
    vi.stubGlobal("fetch", mockFetch({ error_code: "SERVER_ERROR", message: "boom" }, 500))

    await expect(syncPreferences({ theme: "dark" })).rejects.toThrow()
  })
})
