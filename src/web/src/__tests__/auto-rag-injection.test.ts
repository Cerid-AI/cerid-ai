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

// Mock model-router
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

function makeOptions(overrides: Record<string, unknown> = {}) {
  const sendSpy = vi.fn()
  return {
    activeId: "conv-1",
    activeMessages: [] as ChatMessage[],
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
    _sendSpy: sendSpy,
  }
}

function sentMessages(sendSpy: ReturnType<typeof vi.fn>): Pick<ChatMessage, "role" | "content">[] {
  expect(sendSpy).toHaveBeenCalled()
  return sendSpy.mock.calls[0][1]
}

// ===========================================================================
// 1. Auto-RAG Ephemeral Injection
// ===========================================================================

describe("Auto-RAG Ephemeral Injection", () => {
  it("adds system message with <document> tags when KB results exist", async () => {
    const kbChunk = makeKBResult({ artifact_id: "a1", filename: "auth.py", relevance: 0.9 })
    mockQueryKB.mockResolvedValue({ results: [kbChunk] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
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

  it("does not add system message when KB returns empty", async () => {
    mockQueryKB.mockResolvedValue({ results: [] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("Hello")
    })

    const msgs = sentMessages(opts._sendSpy)
    expect(msgs.every((m) => m.role !== "system")).toBe(true)
  })

  it("only injects chunks with relevance >= threshold", async () => {
    const above = makeKBResult({ artifact_id: "a1", relevance: 0.8, filename: "above.py" })
    const below = makeKBResult({ artifact_id: "a2", relevance: 0.3, filename: "below.py" })
    mockQueryKB.mockResolvedValue({ results: [above, below] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
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

  it("stops adding chunks when context budget is exhausted", async () => {
    const modelObj = MODELS[0]
    // Budget = effectiveContextWindow - reservedTokens (~1200 + user msg tokens)
    // estimateTokenCount = Math.ceil(chars / 3.5), so chars = tokens * 3.5
    // Create first chunk that uses 60% of budget, second chunk that uses 60% of budget.
    // First fits, second exceeds remaining 40% → break stops iteration.
    const budgetTokens = modelObj.effectiveContextWindow - 2000
    const chunk1Tokens = Math.floor(budgetTokens * 0.6)
    const chunk2Tokens = Math.floor(budgetTokens * 0.6)
    const content1 = "a".repeat(Math.floor(chunk1Tokens * 3.5))
    const content2 = "b".repeat(Math.floor(chunk2Tokens * 3.5))
    const first = makeKBResult({ artifact_id: "a1", relevance: 0.95, filename: "first.py", content: content1 })
    const second = makeKBResult({ artifact_id: "a2", relevance: 0.90, filename: "second.py", content: content2 })
    mockQueryKB.mockResolvedValue({ results: [first, second] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("test budget")
    })

    const msgs = sentMessages(opts._sendSpy)
    const sysMsg = msgs.find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    // first.py fits; second.py exceeds remaining budget → break stops iteration
    expect(sysMsg!.content).toContain("first.py")
    expect(sysMsg!.content).not.toContain("second.py")
    expect(result.current.lastAutoInjectCount).toBe(1)
  })

  it("prefers orchestrated results over basic KB results when passed as kbResults (smart mode simulation)", async () => {
    // In chat-panel.tsx, when ragMode="smart", orchestrated results are passed as kbResults.
    // We simulate this by passing orchestrated results directly as the kbResults prop.
    const orchestratedChunk = makeKBResult({
      artifact_id: "orch-1",
      filename: "orchestrated-result.md",
      relevance: 0.95,
      content: "Conversation-aware orchestrated content",
    })

    // No fresh KB query results (queryKB returns empty)
    mockQueryKB.mockResolvedValue({ results: [] })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      kbResults: [orchestratedChunk], // This is what effectiveKBResults provides
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("What about the orchestrated context?")
    })

    // Since queryKB returned empty, useChatSend falls back to kbResults (the orchestrated ones)
    const msgs = sentMessages(opts._sendSpy)
    const sysMsg = msgs.find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("orchestrated-result.md")
    expect(sysMsg!.content).toContain("Conversation-aware orchestrated content")
  })

  it("uses basic KB results when kbResults has no orchestrated results (manual mode simulation)", async () => {
    // In manual mode, kbContext.results are passed directly (no orchestrated merge).
    const basicChunk = makeKBResult({
      artifact_id: "basic-1",
      filename: "basic-result.py",
      relevance: 0.88,
    })
    mockQueryKB.mockResolvedValue({ results: [basicChunk] })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      kbResults: [], // In manual mode, orchestrated results are NOT merged
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("How does this work?")
    })

    // queryKB returns the basic chunk as fresh results
    const msgs = sentMessages(opts._sendSpy)
    const sysMsg = msgs.find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("basic-result.py")
  })

  it("falls back to basic KB results when orchestrated results are empty", async () => {
    // effectiveKBResults falls back to kbContext.results when orchestrated is empty.
    // Here we pass basic KB results as the fallback.
    const basicChunk = makeKBResult({
      artifact_id: "fallback-1",
      filename: "fallback.py",
      relevance: 0.82,
    })

    mockQueryKB.mockResolvedValue({ results: [] })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      kbResults: [basicChunk], // fallback from kbContext.results
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("Tell me something")
    })

    const msgs = sentMessages(opts._sendSpy)
    const sysMsg = msgs.find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("fallback.py")
  })
})

// ===========================================================================
// 2. Session Dedup Prevents Token Waste
// ===========================================================================

describe("Session Dedup Prevents Token Waste", () => {
  it("does not re-inject a chunk in subsequent turns", async () => {
    const chunk = makeKBResult({ artifact_id: "a1", chunk_index: 0, relevance: 0.9, filename: "auth.py" })
    mockQueryKB.mockResolvedValue({ results: [chunk] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    // Turn 1: chunk should be injected
    await act(async () => {
      await result.current.handleSend("first question about auth")
    })
    const sys1 = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sys1).toBeDefined()
    expect(sys1!.content).toContain("auth.py")

    opts._sendSpy.mockClear()

    // Turn 2: same chunk should be skipped by session dedup
    await act(async () => {
      await result.current.handleSend("follow up about auth")
    })
    const sys2 = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sys2).toBeUndefined()
  })

  it("includes prior context note referencing filenames from earlier turns", async () => {
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
      activeMessages: [makeMessage("user", "Tell me about the design"), previousMsg],
    })
    const { result } = renderHook(() => useChatSend(opts))

    // Turn 1
    await act(async () => {
      await result.current.handleSend("first question")
    })
    opts._sendSpy.mockClear()

    // Turn 2: new chunk, but prior context note should reference design.md
    mockQueryKB.mockResolvedValue({ results: [chunk2] })
    opts.activeMessages = [
      makeMessage("user", "Tell me about the design"),
      previousMsg,
      makeMessage("user", "first question"),
      makeMessage("assistant", "Here's what I found"),
    ]

    await act(async () => {
      await result.current.handleSend("now tell me about the API")
    })

    const sysMsg = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("In earlier turns you were also shown content from")
    expect(sysMsg!.content).toContain("design.md")
  })

  it("still injects new chunks after dedup filters prior ones", async () => {
    const oldChunk = makeKBResult({ artifact_id: "old-1", chunk_index: 0, relevance: 0.9, filename: "old.py" })
    const newChunk = makeKBResult({ artifact_id: "new-1", chunk_index: 0, relevance: 0.88, filename: "new.py" })

    mockQueryKB.mockResolvedValue({ results: [oldChunk] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    // Turn 1: inject oldChunk
    await act(async () => {
      await result.current.handleSend("first question")
    })
    opts._sendSpy.mockClear()

    // Turn 2: oldChunk deduped, newChunk injected
    mockQueryKB.mockResolvedValue({ results: [oldChunk, newChunk] })
    await act(async () => {
      await result.current.handleSend("second question")
    })

    const sysMsg = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("new.py")
    expect(sysMsg!.content).not.toContain("old.py")
  })

  it("resets dedup set when conversation changes", async () => {
    const chunk = makeKBResult({ artifact_id: "a1", chunk_index: 0, relevance: 0.9, filename: "auth.py" })
    mockQueryKB.mockResolvedValue({ results: [chunk] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result, rerender } = renderHook(() => useChatSend(opts))

    // Turn 1 in conv-1: inject chunk
    await act(async () => {
      await result.current.handleSend("question in conv 1")
    })
    expect(sentMessages(opts._sendSpy).find((m) => m.role === "system")).toBeDefined()
    opts._sendSpy.mockClear()

    // Switch conversation
    opts.activeId = "conv-2"
    rerender()

    // Turn 1 in conv-2: chunk should be injected again (dedup reset)
    await act(async () => {
      await result.current.handleSend("question in conv 2")
    })
    const sysMsg = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("auth.py")
  })

  it("removes overlapping chunks via semantic dedup", async () => {
    // Two chunks with very similar content — deduplicateChunks (Jaccard) should keep the first
    const chunk1 = makeKBResult({
      artifact_id: "a1", chunk_index: 0, relevance: 0.95, filename: "readme.md",
      content: "The authentication module handles user login verification and session management",
    })
    const chunk2 = makeKBResult({
      artifact_id: "a2", chunk_index: 0, relevance: 0.85, filename: "readme2.md",
      content: "The authentication module handles user login verification and session management end",
    })
    mockQueryKB.mockResolvedValue({ results: [chunk1, chunk2] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("auth question")
    })

    const sysMsg = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    // Higher-ranked chunk kept, overlapping one removed
    expect(sysMsg!.content).toContain("readme.md")
    expect(sysMsg!.content).not.toContain("readme2.md")
  })

  it("does not duplicate manually pinned chunk in auto-inject", async () => {
    const pinnedChunk = makeKBResult({
      artifact_id: "pinned-1", chunk_index: 0, relevance: 0.95, filename: "pinned.py",
      content: "Manually pinned content about security",
    })
    // Same chunk returned by KB query
    mockQueryKB.mockResolvedValue({ results: [pinnedChunk] })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      injectedContext: [pinnedChunk], // Manually pinned
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("security question")
    })

    const sysMsg = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    // The chunk should appear exactly once (dedup prevents duplication)
    const matches = sysMsg!.content.match(/pinned\.py/g) ?? []
    expect(matches.length).toBe(1)
  })
})

// ===========================================================================
// 3. Model Receives Context Seamlessly
// ===========================================================================

describe("Model Receives Context Seamlessly", () => {
  it("system message has <document> XML format with correct attributes", async () => {
    const chunk = makeKBResult({
      artifact_id: "doc-1",
      domain: "finance",
      filename: "report.xlsx",
      relevance: 0.92,
      chunk_index: 3,
    })
    mockQueryKB.mockResolvedValue({ results: [chunk] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("Show me the report")
    })

    const sysMsg = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toMatch(/<document\s+id="doc-1"/)
    expect(sysMsg!.content).toMatch(/domain="finance"/)
    expect(sysMsg!.content).toMatch(/source="report\.xlsx"/)
    expect(sysMsg!.content).toContain("</document>")
  })

  it("memory chunks have <memory> tags with type and relevance", async () => {
    const memoryResult = {
      content: "User prefers dark mode",
      relevance: 0.88,
      memory_type: "preference",
      age_days: 5,
      summary: "User prefers dark mode",
      memory_id: "mem-1",
      source_authority: 0.9,
      base_similarity: 0.88,
      access_count: 3,
      source_type: "memory" as const,
    }
    mockQueryKB.mockResolvedValue({ results: [makeKBResult({ relevance: 0.9 })] })
    mockRecallMemories.mockResolvedValue([memoryResult])

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("What are my preferences?")
    })

    const sysMsg = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toMatch(/<memory\s+type="preference"/)
    expect(sysMsg!.content).toMatch(/relevance="0\.88"/)
    expect(sysMsg!.content).toContain("</memory>")
  })

  it("messages array has correct order: system, history, user message", async () => {
    const chunk = makeKBResult({ artifact_id: "a1", relevance: 0.9 })
    mockQueryKB.mockResolvedValue({ results: [chunk] })

    const historyMsgs = [
      makeMessage("user", "Previous question"),
      makeMessage("assistant", "Previous answer"),
    ]

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      activeMessages: historyMsgs,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("Current question")
    })

    const msgs = sentMessages(opts._sendSpy)
    // Order: system (index 0), history user (1), history assistant (2), current user (3)
    expect(msgs[0].role).toBe("system")
    expect(msgs[1].role).toBe("user")
    expect(msgs[1].content).toBe("Previous question")
    expect(msgs[2].role).toBe("assistant")
    expect(msgs[2].content).toBe("Previous answer")
    expect(msgs[3].role).toBe("user")
    expect(msgs[3].content).toBe("Current question")
  })

  it("system message starts with knowledge base instruction text", async () => {
    const chunk = makeKBResult({ artifact_id: "a1", relevance: 0.9 })
    mockQueryKB.mockResolvedValue({ results: [chunk] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("test query")
    })

    const sysMsg = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toMatch(/^The user has a personal knowledge base/)
  })
})
