// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChatSend } from "@/hooks/use-chat-send"
import type { ChatMessage, KBQueryResult } from "@/lib/types"
import { MODELS } from "@/lib/types"

// Mock API module
vi.mock("@/lib/api", () => ({
  queryKB: vi.fn(),
  recallMemories: vi.fn(),
  compressConversation: vi.fn().mockResolvedValue({
    messages: [],
    original_tokens: 0,
    compressed_tokens: 0,
  }),
}))

// Mock model-router (avoid testing routing logic here).
vi.mock("@/lib/model-router", () => ({
  recommendModel: vi.fn().mockReturnValue({
    model: { id: "openrouter/anthropic/claude-sonnet-4.6", effectiveContextWindow: 800_000 },
    estimatedCost: 0, reasoning: "", savingsVsCurrent: 0,
  }),
}))

import { queryKB, recallMemories } from "@/lib/api"

const mockQueryKB = queryKB as ReturnType<typeof vi.fn>
const mockRecallMemories = recallMemories as ReturnType<typeof vi.fn>

const DEFAULT_MODEL = MODELS[0].id

beforeEach(() => {
  vi.clearAllMocks()
  mockQueryKB.mockResolvedValue({ results: [] })
  mockRecallMemories.mockResolvedValue([])
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const makeKBResult = (overrides: Partial<KBQueryResult> = {}): KBQueryResult => ({
  content: "Test chunk content for KB result",
  relevance: 0.85,
  artifact_id: "art-1",
  filename: "test.py",
  domain: "coding",
  chunk_index: 0,
  collection: "kb_coding",
  ingested_at: "2026-01-15T10:00:00Z",
  ...overrides,
})

const makeMessage = (role: "user" | "assistant", content: string, extra?: Partial<ChatMessage>): ChatMessage => ({
  id: `msg-${Math.random().toString(36).slice(2, 8)}`,
  role,
  content,
  timestamp: Date.now(),
  ...extra,
})

/** Build a minimal options object with vi.fn() stubs for all callbacks. */
function makeOptions(overrides: Record<string, unknown> = {}) {
  const sendSpy = vi.fn()
  return {
    activeId: "conv-1",
    activeMessages: [makeMessage("assistant", "Hello")] as ChatMessage[],
    create: vi.fn().mockReturnValue("conv-new"),
    addMessage: vi.fn(),
    updateModel: vi.fn(),
    replaceMessages: vi.fn(),
    send: sendSpy,
    selectedModel: DEFAULT_MODEL,
    setSelectedModel: vi.fn(),
    routingMode: "manual",
    costSensitivity: "medium" as const,
    autoInject: false,
    autoInjectThreshold: 0.6,
    injectedContext: [] as KBQueryResult[],
    kbResults: [] as KBQueryResult[],
    clearInjected: vi.fn(),
    onBeforeSend: vi.fn(),
    ...overrides,
    // expose sendSpy separately so callers can inspect it
    _sendSpy: sendSpy,
  }
}

/** Extract the `allMessages` array passed to `options.send`. */
function sentMessages(sendSpy: ReturnType<typeof vi.fn>): Pick<ChatMessage, "role" | "content">[] {
  expect(sendSpy).toHaveBeenCalled()
  // send(convoId, allMessages, model, sources?)
  return sendSpy.mock.calls[0][1]
}

/** Extract the sources array passed to `options.send` (4th arg). */
function sentSources(sendSpy: ReturnType<typeof vi.fn>) {
  return sendSpy.mock.calls[0][3]
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useChatSend — KB injection payload assembly", () => {
  it("does not inject system message when autoInject is OFF and no manual context", async () => {
    const opts = makeOptions({ autoInject: false, injectedContext: [] })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("Hello")
    })

    const msgs = sentMessages(opts._sendSpy)
    expect(msgs.every((m) => m.role !== "system")).toBe(true)
  })

  it("injects system message with <document> tags when autoInject is ON and KB returns results", async () => {
    const kbChunk = makeKBResult({ artifact_id: "a1", filename: "auth.py", relevance: 0.9 })
    mockQueryKB.mockResolvedValue({ results: [kbChunk] })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("How does auth work?")
    })

    const msgs = sentMessages(opts._sendSpy)
    const sysMsg = msgs.find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("<document")
    expect(sysMsg!.content).toContain("auth.py")
    expect(sysMsg!.content).toContain("</document>")
  })

  it("injects system message with <document> tags from manually injected context (autoInject OFF)", async () => {
    const manual = makeKBResult({ artifact_id: "m1", filename: "budget.xlsx", domain: "finance" })
    const opts = makeOptions({
      autoInject: false,
      injectedContext: [manual],
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("What is the Q3 budget?")
    })

    const msgs = sentMessages(opts._sendSpy)
    const sysMsg = msgs.find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("<document")
    expect(sysMsg!.content).toContain("budget.xlsx")
    expect(opts.clearInjected).toHaveBeenCalled()
  })

  it("filters out results below autoInjectThreshold", async () => {
    const above = makeKBResult({ artifact_id: "a1", relevance: 0.8, filename: "above.py" })
    const below = makeKBResult({ artifact_id: "a2", relevance: 0.3, filename: "below.py" })
    mockQueryKB.mockResolvedValue({ results: [above, below] })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("query about threshold filtering")
    })

    const msgs = sentMessages(opts._sendSpy)
    const sysMsg = msgs.find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("above.py")
    expect(sysMsg!.content).not.toContain("below.py")
  })

  it.skip("stops adding chunks when token budget is exhausted", async () => {
    const modelObj = MODELS[0]
    // Budget ≈ effectiveContextWindow - reservedTokens (~1200 + user msg tokens)
    // Create a first chunk that fits, a second that exceeds the remaining budget,
    // and a third that would fit if budget weren't already spent.
    // estimateTokenCount = Math.ceil(chars / 3.5), so chars = tokens * 3.5
    const budgetTokens = modelObj.effectiveContextWindow - 2000 // generous reserved estimate
    const firstContent = "a".repeat(Math.floor(budgetTokens * 3.5 * 0.8)) // uses 80% of budget
    const secondContent = "b".repeat(Math.floor(budgetTokens * 3.5 * 0.5)) // needs 50%, only 20% left → breaks
    const small = makeKBResult({ artifact_id: "a1", relevance: 0.95, filename: "first.py", content: firstContent })
    const overBudget = makeKBResult({ artifact_id: "a2", relevance: 0.90, filename: "overbudget.py", content: secondContent })
    const trailing = makeKBResult({ artifact_id: "a3", relevance: 0.85, filename: "trailing.py", content: "Trailing chunk" })
    mockQueryKB.mockResolvedValue({ results: [small, overBudget, trailing] })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("test budget")
    })

    const msgs = sentMessages(opts._sendSpy)
    const sysMsg = msgs.find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    // first.py should be included; overbudget.py exceeds remaining budget → break stops iteration
    expect(sysMsg!.content).toContain("first.py")
    expect(sysMsg!.content).not.toContain("overbudget.py")
    expect(sysMsg!.content).not.toContain("trailing.py")
  })

  it("deduplicates chunks already injected in prior turns (session dedup via injectedHistoryRef)", async () => {
    const chunk = makeKBResult({ artifact_id: "a1", chunk_index: 0, relevance: 0.9, filename: "auth.py" })
    mockQueryKB.mockResolvedValue({ results: [chunk] })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
    })
    const { result } = renderHook(() => useChatSend(opts))

    // First send — chunk should be injected
    await act(async () => {
      await result.current.handleSend("first question about auth")
    })

    const msgs1 = sentMessages(opts._sendSpy)
    const sys1 = msgs1.find((m) => m.role === "system")
    expect(sys1).toBeDefined()
    expect(sys1!.content).toContain("auth.py")

    // Reset send spy for second call
    opts._sendSpy.mockClear()

    // Second send — same chunk should be skipped by session dedup
    await act(async () => {
      await result.current.handleSend("follow up about auth")
    })

    const msgs2 = sentMessages(opts._sendSpy)
    const sys2 = msgs2.find((m) => m.role === "system")
    // No system message because the only candidate was already injected
    expect(sys2).toBeUndefined()
  })

  it("injects memories as <memory> tags in system message", async () => {
    const kbChunk = makeKBResult({ artifact_id: "a1", relevance: 0.9, filename: "data.py" })
    const memoryResult = {
      content: "User prefers Python over JavaScript",
      relevance: 0.88,
      memory_type: "preference",
      age_days: 5,
      summary: "User prefers Python over JavaScript",
      memory_id: "mem-1",
      source_authority: 0.9,
      base_similarity: 0.88,
      access_count: 3,
      source_type: "memory" as const,
    }
    mockQueryKB.mockResolvedValue({ results: [kbChunk] })
    mockRecallMemories.mockResolvedValue([memoryResult])

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("What language should I use?")
    })

    const msgs = sentMessages(opts._sendSpy)
    const sysMsg = msgs.find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("<memory")
    expect(sysMsg!.content).toContain("preference")
    expect(sysMsg!.content).toContain("[Remembered Context]")
  })

  it("includes prior-context note on subsequent turns when history contains previously injected sources", async () => {
    const chunk1 = makeKBResult({ artifact_id: "a1", chunk_index: 0, relevance: 0.9, filename: "design.md" })
    const chunk2 = makeKBResult({ artifact_id: "a2", chunk_index: 0, relevance: 0.88, filename: "api-spec.md" })

    mockQueryKB.mockResolvedValue({ results: [chunk1] })

    const previousMsg = makeMessage("assistant", "Here is the design info", {
      id: "prev-1",
      sourcesUsed: [{ artifact_id: "a1", filename: "design.md", domain: "coding", relevance: 0.9, chunk_index: 0 }],
    })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      activeMessages: [
        makeMessage("user", "Tell me about the design"),
        previousMsg,
      ],
    })
    const { result } = renderHook(() => useChatSend(opts))

    // First send — injects chunk1
    await act(async () => {
      await result.current.handleSend("first question")
    })
    opts._sendSpy.mockClear()

    // Second send — chunk1 is deduped, but chunk2 is new
    mockQueryKB.mockResolvedValue({ results: [chunk2] })

    // Update activeMessages to include the sourcesUsed from first turn
    opts.activeMessages = [
      makeMessage("user", "Tell me about the design"),
      previousMsg,
      makeMessage("user", "first question"),
      makeMessage("assistant", "Here's what I found"),
    ]

    await act(async () => {
      await result.current.handleSend("now tell me about the API")
    })

    const msgs = sentMessages(opts._sendSpy)
    const sysMsg = msgs.find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    // The prior context note references design.md since it was injected in a prior turn
    expect(sysMsg!.content).toContain("In earlier turns you were also shown content from")
    expect(sysMsg!.content).toContain("design.md")
  })

  it("passes SourceRef array to send() when context is injected", async () => {
    const chunk = makeKBResult({
      artifact_id: "a1",
      filename: "report.pdf",
      domain: "finance",
      relevance: 0.92,
      chunk_index: 2,
      tags: ["q3"],
      quality_score: 0.8,
    })
    mockQueryKB.mockResolvedValue({ results: [chunk] })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("Show me Q3 numbers")
    })

    const sources = sentSources(opts._sendSpy)
    expect(sources).toBeDefined()
    expect(sources).toHaveLength(1)
    expect(sources[0]).toMatchObject({
      artifact_id: "a1",
      filename: "report.pdf",
      domain: "finance",
      chunk_index: 2,
    })
  })

  it("creates a new conversation when activeId is null", async () => {
    const opts = makeOptions({ activeId: null })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("Start new chat")
    })

    expect(opts.create).toHaveBeenCalledWith(DEFAULT_MODEL)
    // send should use the new conversation ID returned by create
    expect(opts._sendSpy.mock.calls[0][0]).toBe("conv-new")
  })

  it("reports lastAutoInjectCount when auto-inject adds chunks", async () => {
    const chunks = [
      makeKBResult({ artifact_id: "a1", relevance: 0.9 }),
      makeKBResult({ artifact_id: "a2", relevance: 0.85, chunk_index: 1 }),
    ]
    mockQueryKB.mockResolvedValue({ results: chunks })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
    })
    const { result } = renderHook(() => useChatSend(opts))

    expect(result.current.lastAutoInjectCount).toBe(0)

    await act(async () => {
      await result.current.handleSend("query with multiple results")
    })

    expect(result.current.lastAutoInjectCount).toBe(2)

    // resetAutoInjectCount should clear it
    act(() => {
      result.current.resetAutoInjectCount()
    })
    expect(result.current.lastAutoInjectCount).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// Task 3: KB query deduplication (Wave-0 reliability remediation)
// ---------------------------------------------------------------------------
// Background: three hooks fire /agent/query per chat turn. useKBContext (POST 1)
// and useOrchestratedQuery (POST 2) both populate TanStack cache with
// staleTime: 15_000. useChatSend.handleSend (POST 3) historically re-fired
// queryKB unconditionally — redundant and, with _QUERY_SEMAPHORE(2) + 10s
// budgets, enough to monopolize the backend for ~30s per user message.
//
// Fix: skip the fresh queryKB call when options.kbResults is already populated
// (cache warm). Still fetch fresh memories — they aren't covered by the KB
// cache and the prior duplication was KB-specific.

describe("useChatSend — KB query deduplication (Task 3)", () => {
  it("does NOT call queryKB when options.kbResults is non-empty (cache warm)", async () => {
    const prePopulated = [
      makeKBResult({ artifact_id: "pre-1", filename: "pre.md", relevance: 0.9, content: "hello" }),
    ]

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      kbResults: prePopulated,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("hi there")
    })

    expect(mockQueryKB).not.toHaveBeenCalled()
  })

  it("DOES call queryKB when options.kbResults is empty (cache cold)", async () => {
    mockQueryKB.mockResolvedValue({ results: [] })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      kbResults: [] as KBQueryResult[],
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("hi there")
    })

    expect(mockQueryKB).toHaveBeenCalledTimes(1)
  })

  it("still fetches fresh memories when cache is warm (memories not in KB cache)", async () => {
    const prePopulated = [
      makeKBResult({ artifact_id: "pre-1", filename: "pre.md", relevance: 0.9 }),
    ]

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      kbResults: prePopulated,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("hi there")
    })

    expect(mockRecallMemories).toHaveBeenCalledTimes(1)
    expect(mockQueryKB).not.toHaveBeenCalled()
  })
})
