// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// E1 Phase-4 gate — CONVERSATION-PERSISTENCE coordinator (frontend).
//
// Audit: docs/superpowers/plans/2026-07-19-e1-remediation-program.md Phase 4.
// Registry: docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl.
//
// use-conversations.ts now routes every mutator through a single persistence
// coordinator: one localStorage channel (immediate writes cancel the pending
// debounce so a stale streaming snapshot can't clobber a delete — CR-101), a
// per-id server-flush queue (a second conversation's sync can't drop the
// first's — CR-083; and clear/replace/archive/model-change all sync now —
// CR-060/CR-110), and persisted delete tombstones with server-ack so a failed
// or remote-replica delete can't resurrect on the mount merge (CR-092).
// Private mode is resolved at flush time, symmetric for upsert AND delete
// (CR-061).

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
// A second model id for mutation assertions (kept off the default so a no-op
// update can't masquerade as a sync).
const MODELS_SECOND = "openrouter/anthropic/claude-sonnet-4.5"

function serverCopyOf(id: string) {
  return {
    id,
    title: "resurrectable",
    messages: [],
    model: MODEL,
    createdAt: 1_000,
    updatedAt: 1_000,
    archived: false,
  }
}

describe("useConversations delete durability (E1 CR-092)", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    vi.mocked(api.deleteConversationSync).mockRejectedValue(new Error("500"))
    vi.mocked(api.fetchSyncedConversations).mockResolvedValue([])
  })

  it(
    "a locally-deleted conversation must NOT resurrect on remount when its DELETE failed",
    async () => {
      // First session: create then delete a conversation. The DELETE rejects,
      // so the server still holds the record (and the tombstone persists).
      const first = renderHook(() => useConversations())
      let id!: string
      act(() => {
        id = first.result.current.create(MODEL)
      })
      await vi.waitFor(() => {
        expect(api.syncConversation).toHaveBeenCalled()
      })
      act(() => {
        first.result.current.remove(id)
      })
      await vi.waitFor(() => {
        expect(api.deleteConversationSync).toHaveBeenCalledWith(id)
      })
      first.unmount()

      // Second session (remount, shared localStorage): the server still returns
      // the record whose DELETE failed. Let the mount-merge fully settle.
      vi.mocked(api.fetchSyncedConversations).mockResolvedValue([serverCopyOf(id)])
      const second = renderHook(() => useConversations())
      await act(async () => {
        await vi.waitFor(() => {
          expect(api.fetchSyncedConversations).toHaveBeenCalled()
        })
        await Promise.resolve()
        await Promise.resolve()
      })

      // The tombstone suppresses the merge → the deleted conversation stays gone.
      expect(second.result.current.conversations.some((c) => c.id === id)).toBe(false)
    },
  )

  it("green anchor: a conversation created and NOT deleted survives a remount", async () => {
    vi.mocked(api.deleteConversationSync).mockResolvedValue(undefined)
    const first = renderHook(() => useConversations())
    let id!: string
    act(() => {
      id = first.result.current.create(MODEL)
    })
    await vi.waitFor(() => {
      expect(first.result.current.conversations.some((c) => c.id === id)).toBe(true)
    })
    first.unmount()

    const second = renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(second.result.current.conversations.some((c) => c.id === id)).toBe(true)
    })
  })
})

describe("useConversations persistence coordinator (E1 CR-060/083/101/110/061)", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    vi.mocked(api.deleteConversationSync).mockResolvedValue(undefined)
    vi.mocked(api.syncConversation).mockResolvedValue(undefined)
    vi.mocked(api.fetchSyncedConversations).mockResolvedValue([])
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  // ── CR-060: non-add/update/rename mutators must reach the server ──────────
  it("CR-060: archive syncs the conversation to the server", () => {
    const { result } = renderHook(() => useConversations())
    let id!: string
    act(() => { id = result.current.create(MODEL) })
    vi.mocked(api.syncConversation).mockClear()

    act(() => { result.current.archive(id) })
    // Debounced — not immediate.
    expect(api.syncConversation).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(2000) })
    expect(api.syncConversation).toHaveBeenCalledWith(
      expect.objectContaining({ id, archived: true }),
    )
  })

  it("CR-060: clearMessages and updateModel sync to the server", () => {
    const { result } = renderHook(() => useConversations())
    let id!: string
    act(() => { id = result.current.create(MODEL) })
    act(() => {
      result.current.addMessage(id, { id: "m1", role: "user", content: "hi", timestamp: 1 })
    })
    vi.mocked(api.syncConversation).mockClear()

    act(() => { result.current.clearMessages(id) })
    act(() => { result.current.updateModel(id, MODELS_SECOND) })
    act(() => { vi.advanceTimersByTime(2000) })

    // Both mutators funnel through the same per-id queue; the latest op wins,
    // but the point is the conversation reaches the server at all (it did not
    // before — CR-060).
    expect(api.syncConversation).toHaveBeenCalledWith(expect.objectContaining({ id }))
  })

  // ── CR-110: updateLastMessageModel must reach the server ──────────────────
  it("CR-110: updateLastMessageModel syncs the fallback attribution", () => {
    const { result } = renderHook(() => useConversations())
    let id!: string
    act(() => { id = result.current.create(MODEL) })
    act(() => {
      result.current.addMessage(id, { id: "a1", role: "assistant", content: "", timestamp: 1 })
    })
    vi.mocked(api.syncConversation).mockClear()

    act(() => { result.current.updateLastMessageModel(id, MODELS_SECOND) })
    act(() => { vi.advanceTimersByTime(2000) })

    expect(api.syncConversation).toHaveBeenCalledWith(expect.objectContaining({ id }))
  })

  // ── CR-083: a second conversation's sync must not drop the first's ────────
  it("CR-083: two conversations synced within the window both reach the server", () => {
    const { result } = renderHook(() => useConversations())
    let a!: string, b!: string
    act(() => { a = result.current.create(MODEL) })
    act(() => { b = result.current.create(MODEL) })
    vi.mocked(api.syncConversation).mockClear()

    // Two debounced upserts within the same window — the old single-slot ref
    // dropped the first when the second arrived.
    act(() => { result.current.updateModel(a, MODELS_SECOND) })
    act(() => { result.current.updateModel(b, MODELS_SECOND) })
    act(() => { vi.advanceTimersByTime(2000) })

    const synced = vi.mocked(api.syncConversation).mock.calls.map((c) => (c[0] as { id: string }).id)
    expect(synced).toContain(a)
    expect(synced).toContain(b)
  })

  // ── CR-101: an immediate delete must survive a pending streaming save ─────
  it("CR-101: a pending streaming save does not revert an immediate delete", () => {
    const { result } = renderHook(() => useConversations())
    let a!: string, b!: string
    act(() => { a = result.current.create(MODEL) })
    act(() => { b = result.current.create(MODEL) })
    act(() => {
      result.current.addMessage(a, { id: "a1", role: "assistant", content: "", timestamp: 1 })
    })

    // Streaming update on A schedules a debounced localStorage write whose
    // snapshot still contains B…
    act(() => { result.current.updateLastMessage(a, "streaming…") })
    // …then B is deleted (immediate write). The immediate write must cancel the
    // pending debounce so B is not re-added when it would have fired.
    act(() => { result.current.remove(b) })
    act(() => { vi.advanceTimersByTime(600) })

    const stored = JSON.parse(localStorage.getItem("cerid-conversations") ?? "[]") as { id: string }[]
    expect(stored.some((c) => c.id === b)).toBe(false)
    expect(stored.some((c) => c.id === a)).toBe(true)
  })

  // ── CR-061: private mode blocks BOTH server saves and server deletes ──────
  it("CR-061: a delete in private mode does not propagate to the server", () => {
    localStorage.setItem("cerid-private-mode", "true")
    const { result } = renderHook(() => useConversations())
    let id!: string
    act(() => { id = result.current.create(MODEL) })
    // Private → create did not sync.
    expect(api.syncConversation).not.toHaveBeenCalled()

    act(() => { result.current.remove(id) })
    act(() => { vi.advanceTimersByTime(2000) })

    // Symmetric with the blocked save: the delete must NOT reach the server.
    expect(api.deleteConversationSync).not.toHaveBeenCalled()
    // …but it is gone locally (tombstone suppresses any server replica).
    expect(result.current.conversations.some((c) => c.id === id)).toBe(false)
  })

  // ── CR-003: compression must not wipe the turn appended during its window ──
  it("CR-003: mergeCompressedHistory preserves messages appended after the snapshot", () => {
    const { result } = renderHook(() => useConversations())
    let id!: string
    act(() => { id = result.current.create(MODEL) })
    // The history the compression runs on: 3 messages.
    act(() => {
      result.current.addMessage(id, { id: "h1", role: "user", content: "old1", timestamp: 1 })
      result.current.addMessage(id, { id: "h2", role: "assistant", content: "old2", timestamp: 2 })
      result.current.addMessage(id, { id: "h3", role: "user", content: "old3", timestamp: 3 })
    })
    // A live turn is appended AFTER compression started (the async window).
    act(() => {
      result.current.addMessage(id, { id: "live", role: "user", content: "in-flight", timestamp: 4 })
    })

    // Compression of the first 3 resolves with a 1-message summary. A wholesale
    // replace (the old bug) would leave only ["sum"], wiping the live turn.
    const compressed = [{ id: "sum", role: "system" as const, content: "summary", timestamp: 0 }]
    act(() => { result.current.mergeCompressedHistory(id, compressed, 3) })

    const msgs = result.current.conversations.find((c) => c.id === id)!.messages
    expect(msgs.map((m) => m.id)).toEqual(["sum", "live"])
  })

  it("CR-003: mergeCompressedHistory skips a stale snapshot larger than the list", () => {
    const { result } = renderHook(() => useConversations())
    let id!: string
    act(() => { id = result.current.create(MODEL) })
    act(() => {
      result.current.addMessage(id, { id: "only", role: "user", content: "one", timestamp: 1 })
    })
    // Snapshot claims 5 messages but the list has 1 (cleared/replaced under us).
    act(() => {
      result.current.mergeCompressedHistory(id, [
        { id: "sum", role: "system", content: "summary", timestamp: 0 },
      ], 5)
    })
    // Left untouched rather than corrupted.
    const msgs = result.current.conversations.find((c) => c.id === id)!.messages
    expect(msgs.map((m) => m.id)).toEqual(["only"])
  })
})
