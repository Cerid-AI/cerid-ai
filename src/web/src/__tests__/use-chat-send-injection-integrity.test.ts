// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Retrieval and logic audit, group D (client injection) and the memory leg of
 * group B: what the send path puts in the prompt, what it keeps of the user's
 * pins, and what history and evidence survive pruning and compression.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChatSend } from "@/hooks/use-chat-send"
import type { ChatMessage, KBQueryResult, SourceRef } from "@/lib/types"
import { MODELS } from "@/lib/types"
import { getContextBudget } from "@/lib/kb-utils"
import { TOKEN_CHARS_RATIO } from "@/lib/utils"

vi.mock("@/lib/api", () => ({
  queryKB: vi.fn(),
  recallMemories: vi.fn(),
  compressConversation: vi.fn(),
}))

vi.mock("@/lib/model-router", () => ({
  recommendModel: vi.fn().mockReturnValue({
    model: { id: "openrouter/anthropic/claude-sonnet-4.6", effectiveContextWindow: 800_000 },
    estimatedCost: 0,
    reasoning: "",
    savingsVsCurrent: 0,
  }),
}))

import { compressConversation, queryKB, recallMemories } from "@/lib/api"

const mockQueryKB = queryKB as ReturnType<typeof vi.fn>
const mockRecall = recallMemories as ReturnType<typeof vi.fn>
const mockCompress = compressConversation as ReturnType<typeof vi.fn>

const SMALLEST = MODELS.reduce((a, b) => (b.effectiveContextWindow < a.effectiveContextWindow ? b : a))

const row = (over: Partial<KBQueryResult> = {}): KBQueryResult => ({
  content: "chunk content",
  relevance: 0.9,
  artifact_id: "art-1",
  filename: "doc.md",
  domain: "coding",
  chunk_index: 0,
  collection: "kb_coding",
  ingested_at: "2026-01-01T00:00:00Z",
  ...over,
})

const msg = (role: ChatMessage["role"], content: string, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id: `m-${Math.random().toString(36).slice(2, 10)}`,
  role,
  content,
  timestamp: 1,
  ...extra,
})

function makeOptions(overrides: Record<string, unknown> = {}) {
  const send = vi.fn()
  const addMessage = vi.fn()
  return {
    activeId: "conv-1",
    activeMessages: [msg("assistant", "Hello")] as ChatMessage[],
    create: vi.fn().mockReturnValue("conv-new"),
    addMessage,
    updateModel: vi.fn(),
    replaceMessages: vi.fn(),
    send,
    selectedModel: MODELS[0].id,
    setSelectedModel: vi.fn(),
    routingMode: "manual",
    costSensitivity: "medium" as const,
    autoInject: true,
    autoInjectThreshold: 0.1,
    includePacks: true,
    injectedContext: [] as KBQueryResult[],
    kbResults: [] as KBQueryResult[],
    clearInjected: vi.fn(),
    privateModeLevel: 0,
    ...overrides,
  }
}

const systemOf = (send: ReturnType<typeof vi.fn>) =>
  (send.mock.calls[0][1] as Pick<ChatMessage, "role" | "content">[]).find((m) => m.role === "system")

/** History heavy enough to cross the pruning trigger on the smallest model. */
function heavyFiller(n: number): ChatMessage[] {
  const perMessage = "x".repeat(Math.ceil((SMALLEST.effectiveContextWindow * 0.8 * TOKEN_CHARS_RATIO) / n))
  return Array.from({ length: n }, (_, i) => msg(i % 2 === 0 ? "user" : "assistant", perMessage))
}

beforeEach(() => {
  vi.clearAllMocks()
  mockQueryKB.mockResolvedValue({ results: [] })
  mockRecall.mockResolvedValue([])
  mockCompress.mockReturnValue(new Promise(() => {}))
})

describe("memory recall is reported, not emptied (F71, F72)", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it("defers a personal-data question when memory recall never answers", async () => {
    mockRecall.mockImplementation(() => new Promise(() => {}))
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      const done = result.current.handleSend("what did I save about my passport?")
      await vi.advanceTimersByTimeAsync(10_000)
      await done
    })

    expect(opts.send).not.toHaveBeenCalled()
    const last = opts.addMessage.mock.calls.at(-1)![1] as ChatMessage
    expect(last.deferral).toBe(true)
    expect(last.degradedReason).toMatch(/memory recall did not answer/i)
  })

  it("marks the answer degraded when memory recall fails", async () => {
    mockRecall.mockRejectedValue(new Error("memory backend 503"))
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("explain the oauth device flow")
    })

    expect(opts.send.mock.calls[0][4]).toBe("Memory recall failed")
  })

  it("does not let a hung recall hold a send the panel already retrieved for", async () => {
    mockRecall.mockImplementation(() => new Promise(() => {}))
    const text = "explain the oauth device flow"
    const opts = makeOptions({ kbResults: [row({ filename: "oauth.md" })], kbResultsQuery: text })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      void result.current.handleSend(text)
      await vi.advanceTimersByTimeAsync(10_000)
    })

    expect(mockQueryKB).not.toHaveBeenCalled()
    expect(opts.send).toHaveBeenCalled()
    const names = (opts.send.mock.calls[0][3] as SourceRef[]).map((s) => s.filename)
    expect(names).toEqual(["oauth.md"])
  })
})

describe("a pin excludes its own chunk, not its artifact (F73)", () => {
  it("auto-injects another chunk of a pinned artifact", async () => {
    const pinned = row({ artifact_id: "spec", chunk_index: 0, filename: "spec.md", content: "Section one: scope and goals of the rollout" })
    const other = row({ artifact_id: "spec", chunk_index: 4, filename: "spec.md", content: "Section five: the rollback procedure and its owners" })
    mockQueryKB.mockResolvedValue({ results: [other] })
    const opts = makeOptions({ injectedContext: [pinned] })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("how do we roll back?")
    })

    const sources = opts.send.mock.calls[0][3] as SourceRef[]
    expect(sources.map((s) => s.chunk_index)).toEqual([0, 4])
    expect(result.current.lastAutoInjectCount).toBe(1)
  })
})

describe("pins the prompt could not carry stay pinned (F75)", () => {
  it("unpins only what was sent", async () => {
    const budget = getContextBudget(MODELS[0].id)
    const small = row({ artifact_id: "small", filename: "small.md", content: "a short pinned note" })
    const huge = row({ artifact_id: "huge", filename: "huge.md", content: "h ".repeat(budget) })
    const opts = makeOptions({ autoInject: false, injectedContext: [small, huge] })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("summarise what I pinned")
    })

    const names = (opts.send.mock.calls[0][3] as SourceRef[]).map((s) => s.filename)
    expect(names).toEqual(["small.md"])
    expect(opts.clearInjected).toHaveBeenCalledTimes(1)
    expect(opts.clearInjected.mock.calls[0][0]).toEqual([small])
  })
})

describe("pruning and compression keep evidence honest (F76, F44)", () => {
  it("names prior files only from turns the pruned prompt still carries", async () => {
    const old = row({ artifact_id: "old", filename: "old-notes.md", content: "old notes" })
    const opts = makeOptions({
      selectedModel: SMALLEST.id,
      autoInject: false,
      injectedContext: [old],
      activeMessages: [],
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("first question")
    })
    opts.send.mockClear()

    const earlier = msg("assistant", "From your old notes…", {
      sourcesUsed: [{ artifact_id: "old", filename: "old-notes.md", domain: "coding", relevance: 0.9, chunk_index: 0 }],
    })
    opts.activeMessages = [msg("user", "first question"), earlier, ...heavyFiller(8)]
    opts.injectedContext = [row({ artifact_id: "new", filename: "new-notes.md", content: "new notes" })]

    await act(async () => {
      await result.current.handleSend("and now?")
    })

    const sent = opts.send.mock.calls[0][1] as Pick<ChatMessage, "role" | "content">[]
    expect(sent).not.toContain(earlier)
    const sys = systemOf(opts.send)!
    expect(sys.content).toContain("new-notes.md")
    expect(sys.content).not.toContain("old-notes.md")
  })

  it("keeps the note when the turn that saw the file is still sent — the control", async () => {
    const old = row({ artifact_id: "old", filename: "old-notes.md", content: "old notes" })
    const opts = makeOptions({ selectedModel: SMALLEST.id, autoInject: false, injectedContext: [old], activeMessages: [] })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("first question")
    })
    opts.send.mockClear()

    const filler = heavyFiller(8)
    const earlier = msg("assistant", "From your old notes…", {
      sourcesUsed: [{ artifact_id: "old", filename: "old-notes.md", domain: "coding", relevance: 0.9, chunk_index: 0 }],
    })
    opts.activeMessages = [...filler, msg("user", "first question"), earlier]
    opts.injectedContext = [row({ artifact_id: "new", filename: "new-notes.md", content: "new notes" })]

    await act(async () => {
      await result.current.handleSend("and now?")
    })

    expect(systemOf(opts.send)!.content).toContain("old-notes.md")
  })

  it("persists compressed history with the kept turns' ids and sources", async () => {
    const sourceA: SourceRef = { artifact_id: "a", filename: "a.md", domain: "coding", relevance: 0.9, chunk_index: 0 }
    const sourceB: SourceRef = { artifact_id: "b", filename: "b.md", domain: "coding", relevance: 0.8, chunk_index: 2 }
    const filler = heavyFiller(6)
    const answeredA = msg("assistant", "answer grounded in a", { sourcesUsed: [sourceA] })
    const askB = msg("user", "ask about b")
    const answeredB = msg("assistant", "answer grounded in b", { sourcesUsed: [sourceB], degradedReason: "slow" })
    const history = [...filler, answeredA, askB, answeredB]
    mockCompress.mockResolvedValue({
      messages: [
        { role: "user", content: "[Compressed conversation summary]\n…" },
        { role: "user", content: askB.content },
        { role: "assistant", content: answeredB.content },
      ],
      original_tokens: 100,
      compressed_tokens: 10,
    })
    const merge = vi.fn()
    const opts = makeOptions({
      selectedModel: SMALLEST.id,
      autoInject: false,
      activeMessages: history,
      mergeCompressedHistory: merge,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("next question")
    })
    await vi.waitFor(() => expect(merge).toHaveBeenCalled())

    const [, compressed, originalCount] = merge.mock.calls[0] as [string, ChatMessage[], number]
    expect(originalCount).toBe(history.length)
    expect(compressed[1]).toBe(askB)
    expect(compressed[2]).toBe(answeredB)
    expect(compressed[2].sourcesUsed).toEqual([sourceB])
    // The summary stands in for the turns it replaced, evidence included.
    expect(compressed[0].sourcesUsed).toEqual([sourceA])
  })
})
