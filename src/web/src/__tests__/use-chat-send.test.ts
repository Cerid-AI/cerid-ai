// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChatSend } from "@/hooks/use-chat-send"
import type { ChatMessage, KBQueryResult } from "@/lib/types"
import { MODELS } from "@/lib/types"
import { estimateTokenCount, TOKEN_CHARS_RATIO } from "@/lib/utils"

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
    includePacks: true,
    injectedContext: [] as KBQueryResult[],
    kbResults: [] as KBQueryResult[],
    clearInjected: vi.fn(),
    privateModeLevel: 0,
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

  it("sends exclude_packs to queryKB when includePacks is OFF (Slice 7.3)", async () => {
    mockQueryKB.mockResolvedValue({ results: [] })
    const opts = makeOptions({ autoInject: true, includePacks: false })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => {
      await result.current.handleSend("anything")
    })
    expect(mockQueryKB).toHaveBeenCalled()
    const callOpts = mockQueryKB.mock.calls[0][4]
    expect(callOpts.excludePacks).toBe(true)
  })

  it("does not exclude packs when includePacks is ON (default)", async () => {
    mockQueryKB.mockResolvedValue({ results: [] })
    const opts = makeOptions({ autoInject: true, includePacks: true })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => {
      await result.current.handleSend("anything")
    })
    expect(mockQueryKB).toHaveBeenCalled()
    const callOpts = mockQueryKB.mock.calls[0][4]
    expect(callOpts.excludePacks).toBe(false)
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

  it("injects NOTHING at privateModeLevel >= 2 — KB bypass honored (CHAT-01)", async () => {
    // Manual context + an auto-inject KB hit + a recalled memory are all present,
    // but Private Mode L2 ("model sees only what you type") must drop them all.
    const manual = makeKBResult({ artifact_id: "m1", filename: "private.xlsx", domain: "finance" })
    const kbChunk = makeKBResult({ artifact_id: "a1", filename: "auth.py", relevance: 0.95 })
    mockQueryKB.mockResolvedValue({ results: [kbChunk] })
    mockRecallMemories.mockResolvedValue([
      { content: "secret pref", relevance: 0.9, memory_type: "preference", summary: "secret pref",
        memory_id: "m", source_authority: 0.9, base_similarity: 0.9, access_count: 1, source_type: "memory" as const },
    ])

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      injectedContext: [manual],
      privateModeLevel: 2,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("What is in my private docs?")
    })

    const msgs = sentMessages(opts._sendSpy)
    // No system message at all → the payload carries no KB documents or memories.
    expect(msgs.every((m) => m.role !== "system")).toBe(true)
    // No source refs leak onto the assistant message either.
    expect(sentSources(opts._sendSpy)).toBeUndefined()
    // Auto-inject is structurally skipped — the KB is never even queried.
    expect(mockQueryKB).not.toHaveBeenCalled()
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

  it("stops adding chunks when the token budget is exhausted — and stops for good", async () => {
    // A small model window keeps the fixture strings sane. The reserve the hook
    // subtracts is history + user message + already-injected + 1200, so filling
    // history to within a known margin of the window pins `remainingBudget`.
    const model = MODELS.find((m) => m.id === "openrouter/openai/gpt-4o-mini")!
    const userText = "which chunk survives the budget"
    const HEADROOM_TOKENS = 200
    const fillerTokens = model.effectiveContextWindow - 1200 - estimateTokenCount(userText) - HEADROOM_TOKENS
    const filler = "x".repeat(Math.floor(fillerTokens * TOKEN_CHARS_RATIO))

    const remaining =
      model.effectiveContextWindow -
      estimateTokenCount(filler) -
      estimateTokenCount(userText) -
      1200
    expect(remaining).toBeGreaterThan(60)
    expect(remaining).toBeLessThan(HEADROOM_TOKENS + 5)

    // Sized so: fits.py consumes all but ~20 tokens, toobig.py needs far more
    // than that, and trailing.py would comfortably fit in what's left. Only a
    // `break` (not a `continue`) drops trailing.py too.
    const fits = makeKBResult({
      artifact_id: "a1", relevance: 0.95, filename: "fits.py",
      content: "f".repeat(Math.floor((remaining - 20) * TOKEN_CHARS_RATIO)),
    })
    const tooBig = makeKBResult({
      artifact_id: "a2", relevance: 0.9, filename: "toobig.py",
      content: "b".repeat(Math.floor(60 * TOKEN_CHARS_RATIO)),
    })
    const trailing = makeKBResult({
      artifact_id: "a3", relevance: 0.85, filename: "trailing.py",
      content: "tiny",
    })
    mockQueryKB.mockResolvedValue({ results: [fits, tooBig, trailing] })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      selectedModel: model.id,
      activeMessages: [makeMessage("assistant", filler)],
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend(userText)
    })

    // Assert on the sources handed to send(): those are the chunks the budget
    // loop actually admitted, before the separate per-model char budget trims
    // the rendered system message.
    const sources = sentSources(opts._sendSpy) ?? []
    const names = sources.map((s: { filename: string }) => s.filename)
    expect(names).toContain("fits.py")
    expect(names).not.toContain("toobig.py")
    expect(names).not.toContain("trailing.py")
    expect(result.current.lastAutoInjectCount).toBe(1)
  })

  it("keeps injecting while chunks fit — the budget loop is not a one-shot", async () => {
    // Control for the test above: same wiring, chunks that all fit, so a
    // spurious `break` (or an off-by-one in remainingBudget) shows up as a
    // missing chunk rather than passing silently.
    const a = makeKBResult({ artifact_id: "a1", relevance: 0.95, filename: "one.py", content: "one" })
    const b = makeKBResult({ artifact_id: "a2", relevance: 0.9, filename: "two.py", content: "two" })
    const c = makeKBResult({ artifact_id: "a3", relevance: 0.85, filename: "three.py", content: "three" })
    mockQueryKB.mockResolvedValue({ results: [a, b, c] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("everything fits")
    })

    const names = (sentSources(opts._sendSpy) ?? []).map((s: { filename: string }) => s.filename)
    expect(names).toEqual(["one.py", "two.py", "three.py"])
    expect(result.current.lastAutoInjectCount).toBe(3)
  })

  it("a rejected queryKB does not abort the send — the message still goes out", async () => {
    // A general-knowledge question: the model can answer it without KB
    // grounding, so a KB outage must not swallow the turn. (The
    // personal-data counterpart defers instead — see below.)
    mockQueryKB.mockRejectedValue(new Error("KB backend 503"))

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("explain the oauth device flow")
    })

    expect(opts._sendSpy).toHaveBeenCalled()
    const msgs = sentMessages(opts._sendSpy)
    expect(msgs.some((m) => m.role === "user" && m.content === "explain the oauth device flow")).toBe(true)
  })

  it("a rejected queryKB reports a degraded reason, not an empty KB (F330)", async () => {
    // The whole point: a 5xx and a knowledge base with nothing relevant used
    // to produce the same send. They must not.
    mockQueryKB.mockRejectedValue(new Error("KB backend 503"))

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("tell me about auth protocols")
    })

    expect(sentSources(opts._sendSpy)).toBeUndefined()
    expect(result.current.lastAutoInjectCount).toBe(0)
    // The 5th send() arg is the degraded reason.
    expect(opts._sendSpy.mock.calls[0][4]).toBe("KB search failed")
  })

  it("an empty KB result leaves the degraded reason unset — the control", async () => {
    // Without this, a fix that always sets a degraded reason would pass the
    // test above while lying in the far more common case.
    mockQueryKB.mockResolvedValue({ results: [] })

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("tell me about auth protocols")
    })

    expect(opts._sendSpy.mock.calls[0][4]).toBeUndefined()
  })

  it("a KB failure on a personal-data question defers instead of fabricating", async () => {
    // The degraded reason is not decoration: it is what arms the honest-
    // deferral path, so an outage stops producing "I don't have access to
    // your mail" as though it were an answer.
    mockQueryKB.mockRejectedValue(new Error("KB backend 503"))

    const opts = makeOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("what did I write about auth")
    })

    expect(opts._sendSpy).not.toHaveBeenCalled()
    const deferral = opts.addMessage.mock.calls.at(-1)?.[1] as ChatMessage
    expect(deferral.role).toBe("assistant")
    expect(deferral.degradedReason).toBe("KB search failed")
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

  it("applies per-model-family char budget — drops docs that exceed the budget (Phase 2.4)", async () => {
    // GPT-4o-mini budget is 20_000 chars. Create a doc that uses 15k,
    // and a second doc that would push total over 20k.
    const first = makeKBResult({
      artifact_id: "budget-a",
      filename: "first.py",
      relevance: 0.95,
      content: "a".repeat(15_000),
    })
    const second = makeKBResult({
      artifact_id: "budget-b",
      filename: "second.py",
      relevance: 0.90,
      content: "b".repeat(8_000),
    })
    mockQueryKB.mockResolvedValue({ results: [first, second] })

    const gptMiniModel = "openrouter/openai/gpt-4o-mini"
    // Override the model-router mock to return gpt-4o-mini
    const { recommendModel } = await import("@/lib/model-router")
    ;(recommendModel as ReturnType<typeof vi.fn>).mockReturnValue({
      model: { id: gptMiniModel, effectiveContextWindow: 102_400 },
      estimatedCost: 0, reasoning: "", savingsVsCurrent: 0,
    })

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      selectedModel: gptMiniModel,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("query triggering budget check")
    })

    const msgs = sentMessages(opts._sendSpy)
    const sysMsg = msgs.find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    // first.py (15k) fits; second.py (15k+8k=23k) exceeds 20k budget → dropped
    expect(sysMsg!.content).toContain("first.py")
    expect(sysMsg!.content).not.toContain("second.py")
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

  it("does NOT mutate the caller's kbResults array on the cold-cache path", async () => {
    // Cold cache + KB returns nothing + a memory comes back: the merge must
    // clone before pushing, or it pollutes the caller's React state array
    // (audit: stale/duplicated memory entries).
    mockQueryKB.mockResolvedValue({ results: [] })
    mockRecallMemories.mockResolvedValue([
      {
        memory_id: "mem-x",
        memory_type: "fact",
        summary: "a recalled fact",
        content: "a recalled fact",
        relevance: 0.8,
      },
    ])
    const callerKbResults = [] as KBQueryResult[]

    const opts = makeOptions({
      autoInject: true,
      autoInjectThreshold: 0.5,
      kbResults: callerKbResults,
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("tell me something")
    })

    expect(callerKbResults).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// UX-03 — smart routing must explain the swap at the moment it happens
// ---------------------------------------------------------------------------

describe("useChatSend — auto-routing explanation (UX-03)", () => {
  it("notice names the model AND carries the router's reasoning", async () => {
    const { recommendModel } = await import("@/lib/model-router")
    vi.mocked(recommendModel).mockReturnValue({
      model: { id: "openrouter/meta-llama/llama-3.3-70b", label: "Llama 3.3 70B" },
      estimatedCost: 0.0001,
      reasoning: "Llama 3.3 70B scores 82 for this coding task — saves ~$0.01/turn",
      savingsVsCurrent: 0.01,
    } as ReturnType<typeof recommendModel>)

    const opts = makeOptions({ routingMode: "auto" })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("implement a parser")
    })

    expect(opts.setSelectedModel).toHaveBeenCalledWith("openrouter/meta-llama/llama-3.3-70b")
    // The combobox rewrite must be explained inline at swap time — the model
    // name alone reads as the UI silently overriding the user's selection.
    expect(result.current.autoRouteNotice).toContain("Llama 3.3 70B")
    expect(result.current.autoRouteNotice).toContain("saves ~$0.01/turn")
  })
})
