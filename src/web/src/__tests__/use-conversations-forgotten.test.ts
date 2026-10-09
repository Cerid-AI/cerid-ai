// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"

// vi.mock is hoisted above every top-level statement, so the error class the
// mock exports has to be created in vi.hoisted.
const { ConversationGoneError } = vi.hoisted(() => {
  class ConversationGoneError extends Error {
    id: string
    constructor(id: string) { super(`gone: ${id}`); this.id = id }
  }
  return { ConversationGoneError }
})

vi.mock("@/lib/api", () => ({
  syncConversation: vi.fn().mockResolvedValue(undefined),
  syncConversationsBulk: vi.fn().mockResolvedValue(undefined),
  deleteConversationSync: vi.fn().mockResolvedValue(undefined),
  fetchSyncedConversations: vi.fn().mockResolvedValue([]),
  fetchForgottenConversations: vi.fn().mockResolvedValue({ items: [], cursor: null }),
  ConversationGoneError,
}))

import { renderHook, act } from "@testing-library/react"
import { useConversations } from "@/hooks/use-conversations"
import * as api from "@/lib/api"
import type { Conversation } from "@/lib/types"

const MODEL = "openrouter/openai/gpt-4o-mini"

const local = (id: string, updatedAt = 2): Conversation => ({
  id, title: id, messages: [], model: MODEL, createdAt: 1, updatedAt,
})

const stored = (): string[] =>
  (JSON.parse(localStorage.getItem("cerid-conversations") ?? "[]") as Conversation[]).map((c) => c.id)

describe("useConversations honours server-side forgets", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it("drops a local conversation the server lists as forgotten and never re-pushes it", async () => {
    localStorage.setItem("cerid-conversations", JSON.stringify([local("gone"), local("kept")]))
    vi.mocked(api.fetchForgottenConversations).mockResolvedValueOnce({
      items: [{ kind: "conversation", id: "gone", state: "trashed", at: "2026-10-07T10:00:00Z", forget_id: "fg_1" }],
      cursor: "2026-10-07T10:00:00Z",
    })
    const { result } = renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(result.current.conversations.map((c) => c.id)).toEqual(["kept"])
    })
    await vi.waitFor(() => {
      expect(api.syncConversation).toHaveBeenCalled()
    })
    const pushed = vi.mocked(api.syncConversation).mock.calls.map((c) => c[0].id)
    expect(pushed).not.toContain("gone")
    expect(stored()).toEqual(["kept"])
    expect(localStorage.getItem("cerid-forgotten-cursor")).toBe("2026-10-07T10:00:00Z")
  })

  it("drops the local copy when a save answers 410", async () => {
    localStorage.setItem("cerid-conversations", JSON.stringify([local("c410")]))
    vi.mocked(api.syncConversation).mockRejectedValueOnce(new ConversationGoneError("c410"))
    const { result } = renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(result.current.conversations.some((c) => c.id === "c410")).toBe(false)
    })
    expect(stored()).not.toContain("c410")
    expect(result.current.syncFailing).toBe(false)
  })

  it("asks only for forgets newer than the stored cursor", async () => {
    localStorage.setItem("cerid-forgotten-cursor", "2026-10-07T09:00:00Z")
    renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(api.fetchForgottenConversations).toHaveBeenCalledWith("2026-10-07T09:00:00Z")
    })
  })

  it("keeps a conversation whose latest state is restored", async () => {
    localStorage.setItem("cerid-conversations", JSON.stringify([local("back")]))
    vi.mocked(api.fetchForgottenConversations).mockResolvedValueOnce({
      items: [{ kind: "conversation", id: "back", state: "restored", at: "2026-10-07T11:00:00Z", forget_id: "fg_2" }],
      cursor: "2026-10-07T11:00:00Z",
    })
    const { result } = renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(api.fetchSyncedConversations).toHaveBeenCalled()
    })
    await vi.waitFor(() => {
      expect(vi.mocked(api.syncConversation).mock.calls.map((c) => c[0].id)).toContain("back")
    })
    expect(result.current.conversations.map((c) => c.id)).toEqual(["back"])
  })

  it("does not add a server row the feed lists as forgotten", async () => {
    vi.mocked(api.fetchForgottenConversations).mockResolvedValueOnce({
      items: [{ kind: "conversation", id: "stale", state: "purged", at: "2026-10-07T12:00:00Z", forget_id: "fg_3" }],
      cursor: "2026-10-07T12:00:00Z",
    })
    vi.mocked(api.fetchSyncedConversations).mockResolvedValueOnce([local("stale"), local("fresh")])
    const { result } = renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(result.current.conversations.map((c) => c.id)).toEqual(["fresh"])
    })
  })

  it("still hydrates from the server when the forgotten feed is unavailable", async () => {
    localStorage.setItem("cerid-forgotten-cursor", "2026-10-07T09:00:00Z")
    vi.mocked(api.fetchForgottenConversations).mockRejectedValueOnce(new Error("offline"))
    vi.mocked(api.fetchSyncedConversations).mockResolvedValueOnce([local("srv")])
    const { result } = renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(result.current.conversations.map((c) => c.id)).toEqual(["srv"])
    })
    expect(localStorage.getItem("cerid-forgotten-cursor")).toBe("2026-10-07T09:00:00Z")
  })

  it("drops the conversation and clears the selection when an edit answers 410", async () => {
    const { result } = renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(api.fetchSyncedConversations).toHaveBeenCalled()
    })
    let id = ""
    act(() => { id = result.current.create(MODEL) })
    await vi.waitFor(() => {
      expect(api.syncConversation).toHaveBeenCalledTimes(1)
    })
    expect(result.current.activeId).toBe(id)

    vi.mocked(api.syncConversation).mockRejectedValueOnce(new ConversationGoneError(id))
    act(() => { result.current.updateModel(id, "openrouter/anthropic/claude-sonnet-4.5") })
    await vi.waitFor(() => {
      expect(result.current.conversations.some((c) => c.id === id)).toBe(false)
    }, { timeout: 4000 })
    expect(result.current.activeId).toBeNull()
    expect(result.current.syncFailing).toBe(false)
  })

  it("follows the forget assistant: drops what it forgot and takes back what it restored", async () => {
    const { CONVERSATIONS_FORGOTTEN_EVENT, CONVERSATIONS_RESTORED_EVENT } = await import("@/lib/forget-items-with-undo")
    localStorage.setItem("cerid-conversations", JSON.stringify([local("a"), local("b")]))
    const { result } = renderHook(() => useConversations())
    await vi.waitFor(() => expect(result.current.conversations.map((c) => c.id)).toEqual(["a", "b"]))

    act(() => { window.dispatchEvent(new CustomEvent(CONVERSATIONS_FORGOTTEN_EVENT, { detail: ["a"] })) })
    expect(result.current.conversations.map((c) => c.id)).toEqual(["b"])
    expect(stored()).toEqual(["b"])

    vi.mocked(api.fetchSyncedConversations).mockResolvedValueOnce([local("a", 3), local("other", 4)])
    act(() => { window.dispatchEvent(new CustomEvent(CONVERSATIONS_RESTORED_EVENT, { detail: ["a"] })) })
    await vi.waitFor(() => expect(result.current.conversations.map((c) => c.id).sort()).toEqual(["a", "b"]))
  })
})
