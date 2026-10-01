// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

vi.mock("@/lib/api", () => ({
  syncConversation: vi.fn().mockResolvedValue(undefined),
  syncConversationsBulk: vi.fn().mockResolvedValue(undefined),
  deleteConversationSync: vi.fn().mockResolvedValue(undefined),
  fetchSyncedConversations: vi.fn().mockResolvedValue([]),
}))

import { renderHook, act } from "@testing-library/react"
import { useConversations } from "@/hooks/use-conversations"
import * as api from "@/lib/api"

const MODEL = "openrouter/openai/gpt-4o-mini"
const PHRASE = "the phrase is velvet-otter-91"

function setPrivateMode(on: boolean) {
  localStorage.setItem("cerid-private-mode", String(on))
}

function everythingInBrowserStorage(): string {
  const dump = (s: Storage) =>
    Array.from({ length: s.length }, (_, i) => s.key(i)!).map((k) => `${k}=${s.getItem(k)}`).join("\n")
  return `${dump(localStorage)}\n${dump(sessionStorage)}`
}

function syncedBodies(): string {
  return JSON.stringify([
    ...vi.mocked(api.syncConversation).mock.calls,
    ...vi.mocked(api.syncConversationsBulk).mock.calls,
  ])
}

function privateTurn(result: { current: ReturnType<typeof useConversations> }, id: string) {
  act(() => {
    result.current.addMessage(id, { id: "u1", role: "user", content: `Private test: ${PHRASE}`, timestamp: 1 })
    result.current.addMessage(id, { id: "a1", role: "assistant", content: "", timestamp: 2 })
    result.current.updateLastMessage(id, `You said ${PHRASE}`)
  })
}

describe("private conversations and browser storage (audit 49)", () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    vi.clearAllMocks()
    vi.mocked(api.fetchSyncedConversations).mockResolvedValue([])
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("keeps a private conversation readable in memory but never writes it to browser storage", () => {
    setPrivateMode(true)
    const { result, unmount } = renderHook(() => useConversations())
    let id!: string
    act(() => { id = result.current.create(MODEL) })
    privateTurn(result, id)
    act(() => { vi.advanceTimersByTime(5000) })

    const inMemory = result.current.conversations.find((c) => c.id === id)!
    expect(inMemory.messages.map((m) => m.content).join(" ")).toContain(PHRASE)
    expect(everythingInBrowserStorage()).not.toContain("velvet-otter-91")
    expect(everythingInBrowserStorage()).not.toContain("Private test")

    unmount()
    expect(everythingInBrowserStorage()).not.toContain("velvet-otter-91")
  })

  it("is gone from the conversation list after a reload, even with private mode turned off", () => {
    setPrivateMode(true)
    const first = renderHook(() => useConversations())
    let id!: string
    act(() => { id = first.result.current.create(MODEL) })
    privateTurn(first.result, id)
    act(() => { vi.advanceTimersByTime(5000) })
    setPrivateMode(false)
    first.unmount()

    const second = renderHook(() => useConversations())
    expect(second.result.current.conversations.some((c) => c.id === id)).toBe(false)
    expect(second.result.current.visibleConversations.some((c) => c.id === id)).toBe(false)
  })

  it("keeps storing ordinary conversations alongside a private one", () => {
    const { result } = renderHook(() => useConversations())
    let open!: string
    let secret!: string
    act(() => { open = result.current.create(MODEL) })
    act(() => {
      result.current.addMessage(open, { id: "o1", role: "user", content: "ordinary question", timestamp: 1 })
    })
    setPrivateMode(true)
    act(() => { secret = result.current.create(MODEL) })
    privateTurn(result, secret)
    act(() => { vi.advanceTimersByTime(5000) })

    const stored = JSON.parse(localStorage.getItem("cerid-conversations") ?? "[]") as { id: string }[]
    expect(stored.map((c) => c.id)).toEqual([open])
  })

  it("removes the stored copy once a stored conversation receives a private message", () => {
    const { result } = renderHook(() => useConversations())
    let id!: string
    act(() => { id = result.current.create(MODEL) })
    act(() => {
      result.current.addMessage(id, { id: "o1", role: "user", content: "ordinary question", timestamp: 1 })
    })
    expect(localStorage.getItem("cerid-conversations")).toContain("ordinary question")

    setPrivateMode(true)
    privateTurn(result, id)
    act(() => { vi.advanceTimersByTime(5000) })

    expect(localStorage.getItem("cerid-conversations")).not.toContain("ordinary question")
    expect(everythingInBrowserStorage()).not.toContain("velvet-otter-91")
    expect(result.current.conversations.find((c) => c.id === id)!.messages).toHaveLength(3)
  })

  it("removes a stored record marked private when the list is loaded", () => {
    const base = { messages: [], model: MODEL, createdAt: 1, updatedAt: 1, archived: false }
    localStorage.setItem("cerid-conversations", JSON.stringify([
      { ...base, id: "was-private", title: "Private test: m", private: true },
      { ...base, id: "ordinary", title: "Ordinary" },
    ]))

    const { result } = renderHook(() => useConversations())

    expect(result.current.conversations.map((c) => c.id)).toEqual(["ordinary"])
    expect(localStorage.getItem("cerid-conversations")).not.toContain("was-private")
  })

  it("never sends a private conversation to the server, even after private mode is turned off", () => {
    setPrivateMode(true)
    const { result, unmount } = renderHook(() => useConversations())
    let id!: string
    act(() => { id = result.current.create(MODEL) })
    privateTurn(result, id)
    act(() => { vi.advanceTimersByTime(5000) })

    setPrivateMode(false)
    act(() => { result.current.rename(id, "Renamed after private mode") })
    act(() => { result.current.archive(id) })
    act(() => { result.current.bulkArchive([id]) })
    act(() => { vi.advanceTimersByTime(5000) })
    unmount()

    expect(syncedBodies()).not.toContain("velvet-otter-91")
    expect(vi.mocked(api.syncConversation).mock.calls.some(([c]) => c.id === id)).toBe(false)
  })

  it("does not push a private conversation during the server merge on load", async () => {
    vi.useRealTimers()
    let release!: (v: never[]) => void
    vi.mocked(api.fetchSyncedConversations).mockReturnValue(new Promise((r) => { release = r }))
    setPrivateMode(true)
    const { result } = renderHook(() => useConversations())
    let id!: string
    act(() => { id = result.current.create(MODEL) })
    privateTurn(result, id)
    setPrivateMode(false)

    await act(async () => { release([]) })

    expect(vi.mocked(api.syncConversation).mock.calls.some(([c]) => c.id === id)).toBe(false)
  })
})
