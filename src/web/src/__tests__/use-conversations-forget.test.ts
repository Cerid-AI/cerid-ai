// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"

const { ConversationGoneError, ForgetConflictError, ForgetHttpError } = vi.hoisted(() => {
  class ConversationGoneError extends Error {
    id: string
    constructor(id: string) { super(`gone: ${id}`); this.id = id }
  }
  class ForgetConflictError extends Error {}
  class ForgetHttpError extends Error {
    status: number
    constructor(status: number, message: string) { super(message); this.status = status }
  }
  return { ConversationGoneError, ForgetConflictError, ForgetHttpError }
})

vi.mock("@/lib/api", () => ({
  syncConversation: vi.fn().mockResolvedValue(undefined),
  syncConversationsBulk: vi.fn().mockResolvedValue(undefined),
  deleteConversationSync: vi.fn().mockResolvedValue(undefined),
  fetchSyncedConversations: vi.fn().mockResolvedValue([]),
  fetchForgottenConversations: vi.fn().mockResolvedValue({ items: [], cursor: null }),
  forgetSubjects: vi.fn().mockResolvedValue({ forget_id: "fg_1", state: "trashed", receipt: null }),
  restoreForget: vi.fn().mockResolvedValue({ restored: 1 }),
  ConversationGoneError,
  ForgetConflictError,
  ForgetHttpError,
}))

import { renderHook, act } from "@testing-library/react"
import { useConversations } from "@/hooks/use-conversations"
import * as api from "@/lib/api"
import type { Conversation } from "@/lib/types"

const MODEL = "openrouter/openai/gpt-4o-mini"
const local = (id: string, updatedAt = 2): Conversation => ({ id, title: id, messages: [], model: MODEL, createdAt: 1, updatedAt })
const tombstones = (): string[] => JSON.parse(localStorage.getItem("cerid-conversation-tombstones") ?? "[]")

async function mounted(convos: Conversation[]) {
  localStorage.setItem("cerid-conversations", JSON.stringify(convos))
  const hook = renderHook(() => useConversations())
  await vi.waitFor(() => expect(api.fetchSyncedConversations).toHaveBeenCalled())
  return hook
}

describe("useConversations forget and restore", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it("forgets the conversation with the chosen derived items and clears its tombstone", async () => {
    const { result } = await mounted([local("c1"), local("c2")])
    let outcome: Awaited<ReturnType<typeof result.current.forget>> | undefined
    await act(async () => {
      outcome = await result.current.forget(["c1"], { mode: "trash", derived: [{ kind: "artifact", id: "a1" }] })
    })
    expect(api.forgetSubjects).toHaveBeenCalledWith(
      [{ kind: "conversation", id: "c1" }, { kind: "artifact", id: "a1" }], "trash",
    )
    expect(outcome).toEqual({ status: "done", result: { forget_id: "fg_1", state: "trashed", receipt: null } })
    expect(result.current.conversations.map((c) => c.id)).toEqual(["c2"])
    expect(tombstones()).not.toContain("c1")
  })

  it("keeps the tombstone when the server cannot be reached", async () => {
    vi.mocked(api.forgetSubjects).mockRejectedValueOnce(new TypeError("Failed to fetch"))
    const { result } = await mounted([local("c1")])
    let outcome: Awaited<ReturnType<typeof result.current.forget>> | undefined
    await act(async () => { outcome = await result.current.forget(["c1"], { mode: "permanent" }) })
    expect(outcome).toEqual({ status: "failed" })
    expect(result.current.conversations).toEqual([])
    expect(tombstones()).toContain("c1")
    expect(result.current.syncFailing).toBe(true)
  })

  it("defers the server forget while private mode is on (nothing leaves the browser)", async () => {
    const { result } = await mounted([local("c1")])
    localStorage.setItem("cerid-private-mode", "true")
    let outcome: Awaited<ReturnType<typeof result.current.forget>> | undefined
    await act(async () => { outcome = await result.current.forget(["c1"], { mode: "trash" }) })
    expect(outcome).toEqual({ status: "deferred" })
    expect(api.forgetSubjects).not.toHaveBeenCalled()
    expect(tombstones()).toContain("c1")
  })

  it("restore puts the conversation back without pushing it again", async () => {
    const convo = local("c1")
    const { result } = await mounted([convo, local("c2", 1)])
    await act(async () => { await result.current.forget(["c1"], { mode: "trash" }) })
    vi.mocked(api.syncConversation).mockClear()
    await act(async () => { await result.current.restore("fg_1", [convo]) })
    expect(api.restoreForget).toHaveBeenCalledWith("fg_1")
    expect(result.current.conversations.map((c) => c.id)).toEqual(["c1", "c2"])
    expect(tombstones()).not.toContain("c1")
    expect(api.syncConversation).not.toHaveBeenCalled()
  })

  it("a refused restore rethrows and changes nothing", async () => {
    const convo = local("c1")
    const { result } = await mounted([convo])
    await act(async () => { await result.current.forget(["c1"], { mode: "trash" }) })
    vi.mocked(api.restoreForget).mockRejectedValueOnce(new ForgetConflictError("erasing"))
    await act(async () => {
      await expect(result.current.restore("fg_1", [convo])).rejects.toBeInstanceOf(ForgetConflictError)
    })
    expect(result.current.conversations).toEqual([])
  })

  it("remove and bulkDelete move to the Trash through the engine", async () => {
    const { result } = await mounted([local("c1"), local("c2"), local("c3")])
    await act(async () => { result.current.remove("c1") })
    await act(async () => { result.current.bulkDelete(["c2", "c3"]) })
    await vi.waitFor(() => expect(api.forgetSubjects).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.forgetSubjects).mock.calls).toEqual([
      [[{ kind: "conversation", id: "c1" }], "trash"],
      [[{ kind: "conversation", id: "c2" }, { kind: "conversation", id: "c3" }], "trash"],
    ])
    expect(api.deleteConversationSync).not.toHaveBeenCalled()
  })

  it("a refused forget puts the conversation back and says why", async () => {
    vi.mocked(api.forgetSubjects).mockRejectedValueOnce(new ForgetHttpError(403, "Forgetting needs an admin in multi-user mode"))
    const { result } = await mounted([local("c1"), local("c2", 1)])
    let outcome: Awaited<ReturnType<typeof result.current.forget>> | undefined
    await act(async () => { outcome = await result.current.forget(["c1"], { mode: "trash" }) })
    expect(outcome).toEqual({ status: "refused", message: "Forgetting needs an admin in multi-user mode" })
    expect(result.current.conversations.map((c) => c.id)).toEqual(["c1", "c2"])
    expect(tombstones()).not.toContain("c1")
  })

  it("a forget cancels the conversation's pending save", async () => {
    vi.useFakeTimers()
    try {
      localStorage.setItem("cerid-conversations", JSON.stringify([local("c1")]))
      const { result } = renderHook(() => useConversations())
      await act(async () => { await vi.advanceTimersByTimeAsync(50) })
      act(() => { result.current.rename("c1", "renamed") })
      vi.mocked(api.syncConversation).mockClear()
      await act(async () => { await result.current.forget(["c1"], { mode: "trash" }) })
      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
      expect(vi.mocked(api.syncConversation).mock.calls.map((c) => c[0].id)).not.toContain("c1")
    } finally {
      vi.useRealTimers()
    }
  })
})
