// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"

vi.mock("@/lib/api", () => ({
  syncConversation: vi.fn().mockResolvedValue(undefined),
  syncConversationsBulk: vi.fn().mockResolvedValue(undefined),
  deleteConversationSync: vi.fn().mockResolvedValue(undefined),
  fetchSyncedConversations: vi.fn().mockResolvedValue([]),
}))

import { renderHook, act } from "@testing-library/react"
import { useConversations } from "@/hooks/use-conversations"
import * as api from "@/lib/api"

describe("useConversations cloud sync", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it("syncs conversation to server on create", async () => {
    const { result } = renderHook(() => useConversations())
    act(() => { result.current.create("openrouter/openai/gpt-4o-mini") })
    await vi.waitFor(() => {
      expect(api.syncConversation).toHaveBeenCalledTimes(1)
    })
  })

  it("calls deleteConversationSync on remove", async () => {
    const { result } = renderHook(() => useConversations())
    let id: string
    act(() => { id = result.current.create("openrouter/openai/gpt-4o-mini") })
    vi.mocked(api.syncConversation).mockClear()
    act(() => { result.current.remove(id!) })
    await vi.waitFor(() => {
      expect(api.deleteConversationSync).toHaveBeenCalledWith(id!)
    })
  })

  it("fetches server conversations on mount", async () => {
    renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(api.fetchSyncedConversations).toHaveBeenCalledTimes(1)
    })
  })

  it("merges server conversations not in localStorage", async () => {
    const serverConvo = {
      id: "server-only", title: "From server", messages: [],
      model: "openrouter/openai/gpt-4o-mini",
      createdAt: 1710000000000, updatedAt: 1710000000000,
    }
    vi.mocked(api.fetchSyncedConversations).mockResolvedValueOnce([serverConvo])
    const { result } = renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(result.current.conversations.some(c => c.id === "server-only")).toBe(true)
    })
  })

  // ── Audit F-7: per-conversation version-vector reconciliation ───────────
  it("replaces local record when server.updatedAt is newer", async () => {
    // Seed localStorage with an older copy of convo "shared".
    localStorage.setItem("cerid-conversations", JSON.stringify([
      {
        id: "shared", title: "Old title", messages: [],
        model: "openrouter/openai/gpt-4o-mini",
        createdAt: 1_000, updatedAt: 1_000, archived: false,
      },
    ]))

    const newer = {
      id: "shared", title: "New title from other machine", messages: [],
      model: "openrouter/openai/gpt-4o-mini",
      createdAt: 1_000, updatedAt: 9_000, archived: false,
    }
    vi.mocked(api.fetchSyncedConversations).mockResolvedValueOnce([newer])

    const { result } = renderHook(() => useConversations())

    await vi.waitFor(() => {
      const c = result.current.conversations.find((x) => x.id === "shared")
      expect(c?.title).toBe("New title from other machine")
      expect(c?.updatedAt).toBe(9_000)
    })
    // Server was fresher — do NOT push the stale local copy back.
    expect(api.syncConversation).not.toHaveBeenCalled()
  })

  it("pushes local record when localUpdatedAt is newer than server", async () => {
    // Local convo edited more recently than the server copy — the previous
    // syncConversation() must have failed, so this hydrate should re-push.
    localStorage.setItem("cerid-conversations", JSON.stringify([
      {
        id: "shared", title: "Local edit", messages: [],
        model: "openrouter/openai/gpt-4o-mini",
        createdAt: 1_000, updatedAt: 9_000, archived: false,
      },
    ]))

    const stale = {
      id: "shared", title: "Stale server copy", messages: [],
      model: "openrouter/openai/gpt-4o-mini",
      createdAt: 1_000, updatedAt: 1_000, archived: false,
    }
    vi.mocked(api.fetchSyncedConversations).mockResolvedValueOnce([stale])

    const { result } = renderHook(() => useConversations())

    await vi.waitFor(() => {
      expect(api.syncConversation).toHaveBeenCalledWith(
        expect.objectContaining({ id: "shared", updatedAt: 9_000, title: "Local edit" }),
      )
    })
    // Local record preserved — server's stale version must not clobber it.
    const c = result.current.conversations.find((x) => x.id === "shared")
    expect(c?.title).toBe("Local edit")
    expect(c?.updatedAt).toBe(9_000)
  })
})
