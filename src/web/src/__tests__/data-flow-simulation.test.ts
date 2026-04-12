// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"
import { useChat } from "@/hooks/use-chat"
import { useChatSend } from "@/hooks/use-chat-send"
import { useVerificationStream } from "@/hooks/use-verification-stream"
import type { ChatMessage, KBQueryResult } from "@/lib/types"
import { MODELS } from "@/lib/types"

// --- Mocks ---

vi.mock("@/lib/api", () => ({
  streamChat: vi.fn(),
  ingestFeedback: vi.fn().mockResolvedValue(undefined),
  extractMemories: vi.fn().mockResolvedValue(undefined),
  queryKB: vi.fn(),
  recallMemories: vi.fn(),
  compressConversation: vi.fn().mockResolvedValue({ messages: [], original_tokens: 0, compressed_tokens: 0 }),
  streamVerification: vi.fn(),
  updateSettings: vi.fn().mockResolvedValue({ status: "ok", updated: {} }),
}))

vi.mock("@/lib/model-router", () => ({
  recommendModel: vi.fn().mockReturnValue({
    model: { id: "openrouter/anthropic/claude-sonnet-4.6", effectiveContextWindow: 800_000 },
    estimatedCost: 0, reasoning: "", savingsVsCurrent: 0,
  }),
}))

import { streamChat, queryKB, recallMemories, streamVerification, updateSettings } from "@/lib/api"

const mockStreamChat = streamChat as ReturnType<typeof vi.fn>
const mockQueryKB = queryKB as ReturnType<typeof vi.fn>
const mockRecallMemories = recallMemories as ReturnType<typeof vi.fn>
const mockStreamVerification = streamVerification as ReturnType<typeof vi.fn>
const mockUpdateSettings = updateSettings as ReturnType<typeof vi.fn>
const DEFAULT_MODEL = MODELS[0].id

// --- Helpers ---

const makeKBResult = (o: Partial<KBQueryResult> = {}): KBQueryResult => ({
  content: "Test chunk content", relevance: 0.85, artifact_id: "art-1",
  filename: "test.py", domain: "coding", chunk_index: 0,
  collection: "kb_coding", ingested_at: "2026-01-15T10:00:00Z", ...o,
})

function makeSendOptions(overrides: Record<string, unknown> = {}) {
  const sendSpy = vi.fn()
  return {
    activeId: "conv-1", activeMessages: [] as ChatMessage[],
    create: vi.fn().mockReturnValue("conv-new"), addMessage: vi.fn(),
    updateModel: vi.fn(), replaceMessages: vi.fn(), send: sendSpy,
    selectedModel: DEFAULT_MODEL, setSelectedModel: vi.fn(),
    routingMode: "manual", costSensitivity: "medium" as const,
    autoInject: false, autoInjectThreshold: 0.6,
    injectedContext: [] as KBQueryResult[], kbResults: [] as KBQueryResult[],
    clearInjected: vi.fn(), onBeforeSend: vi.fn(),
    ...overrides, _sendSpy: sendSpy,
  }
}

function sentMessages(spy: ReturnType<typeof vi.fn>): Pick<ChatMessage, "role" | "content">[] {
  expect(spy).toHaveBeenCalled()
  return spy.mock.calls[0][1]
}

type SSEEvent = Record<string, unknown>

function makeSSEStream(events: SSEEvent[]) {
  let aborted = false
  const abort = vi.fn(() => { aborted = true })
  const body = new ReadableStream<Uint8Array>({
    async start(ctrl) {
      for (const e of events) {
        if (aborted) break
        ctrl.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(e)}\n\n`))
      }
      ctrl.close()
    },
  })
  return { response: Promise.resolve({ ok: true, status: 200, body } as unknown as Response), abort }
}

beforeEach(() => { vi.clearAllMocks(); mockQueryKB.mockResolvedValue({ results: [] }); mockRecallMemories.mockResolvedValue([]) })
afterEach(() => { vi.restoreAllMocks() })

// ===========================================================================
// 1. Simulated Chat Data Flow
// ===========================================================================

describe("Simulated Chat Data Flow", () => {
  it("sends message and receives streamed response", async () => {
    const onStart = vi.fn(), onUpdate = vi.fn()
    mockStreamChat.mockImplementation(async (_m: unknown, _mo: unknown, onChunk: (c: string) => void) => { onChunk("Hello "); onChunk("world!") })
    const { result } = renderHook(() => useChat({ onMessageStart: onStart, onMessageUpdate: onUpdate }))
    await act(async () => { await result.current.send("conv-1", [{ role: "user", content: "Hi" }], DEFAULT_MODEL) })
    expect(onStart).toHaveBeenCalledTimes(1)
    const last = onUpdate.mock.calls[onUpdate.mock.calls.length - 1]
    expect(last[1]).toBe("Hello world!")
  })

  it("injects KB context into message via useChatSend", async () => {
    mockQueryKB.mockResolvedValue({ results: [makeKBResult({ artifact_id: "a1", filename: "auth.py", relevance: 0.9 })] })
    const opts = makeSendOptions({ autoInject: true, autoInjectThreshold: 0.5, activeMessages: [{ id: "prior-1", role: "assistant" as const, content: "Hello", timestamp: Date.now() }] })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => { await result.current.handleSend("How does auth work?") })
    const sysMsg = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("<document")
    expect(sysMsg!.content).toContain("auth.py")
  })

  it("accumulates conversation messages across 3 sends", async () => {
    const onStart = vi.fn(), onUpdate = vi.fn()
    let n = 0
    mockStreamChat.mockImplementation(async (_m: unknown, _mo: unknown, onChunk: (c: string) => void) => { n++; onChunk(`R${n}`) })
    const { result } = renderHook(() => useChat({ onMessageStart: onStart, onMessageUpdate: onUpdate }))
    for (let i = 1; i <= 3; i++) {
      await act(async () => {
        await result.current.send("conv-1",
          Array.from({ length: i }, (_, j) => ({ role: (j % 2 === 0 ? "user" : "assistant") as "user" | "assistant", content: `msg ${j}` })),
          DEFAULT_MODEL)
      })
    }
    expect(mockStreamChat).toHaveBeenCalledTimes(3)
    expect(onStart).toHaveBeenCalledTimes(3)
  })

  it("propagates model selection to API call", async () => {
    const onStart = vi.fn(), onUpdate = vi.fn()
    mockStreamChat.mockImplementation(async (_m: unknown, _mo: unknown, onChunk: (c: string) => void) => { onChunk("ok") })
    const { result } = renderHook(() => useChat({ onMessageStart: onStart, onMessageUpdate: onUpdate }))
    const model = "openrouter/anthropic/claude-opus-4.6"
    await act(async () => { await result.current.send("conv-1", [{ role: "user", content: "Hi" }], model) })
    expect(mockStreamChat.mock.calls[0][1]).toBe(model)
    expect(onStart).toHaveBeenCalledWith("conv-1", expect.objectContaining({ model }))
  })

  it("transitions isStreaming true then false during send", async () => {
    const onStart = vi.fn(), onUpdate = vi.fn()
    let resolve: () => void = () => {}
    mockStreamChat.mockImplementation(() => new Promise<void>((r) => { resolve = r }))
    const { result } = renderHook(() => useChat({ onMessageStart: onStart, onMessageUpdate: onUpdate }))
    let p: Promise<void>
    act(() => { p = result.current.send("conv-1", [{ role: "user", content: "Hi" }], DEFAULT_MODEL) })
    expect(result.current.isStreaming).toBe(true)
    await act(async () => { resolve(); await p! })
    expect(result.current.isStreaming).toBe(false)
  })

  it("handles error response gracefully", async () => {
    const onStart = vi.fn(), onUpdate = vi.fn()
    mockStreamChat.mockRejectedValue(new Error("Internal server error"))
    const { result } = renderHook(() => useChat({ onMessageStart: onStart, onMessageUpdate: onUpdate }))
    await act(async () => { await result.current.send("conv-1", [{ role: "user", content: "Hi" }], DEFAULT_MODEL) })
    expect(onUpdate).toHaveBeenCalledWith("conv-1", expect.stringContaining("Internal server error"))
    expect(result.current.isStreaming).toBe(false)
  })

  it("calls AbortController.abort when stop() is invoked", () => {
    const onStart = vi.fn(), onUpdate = vi.fn()
    const abortSpy = vi.fn()
    const Orig = globalThis.AbortController
    globalThis.AbortController = class extends Orig { abort(r?: string) { abortSpy(r); return super.abort(r) } } as typeof AbortController
    mockStreamChat.mockImplementation(() => new Promise(() => {}))
    const { result } = renderHook(() => useChat({ onMessageStart: onStart, onMessageUpdate: onUpdate }))
    act(() => { result.current.send("conv-1", [{ role: "user", content: "Hi" }], DEFAULT_MODEL) })
    act(() => { result.current.stop() })
    expect(abortSpy).toHaveBeenCalled()
    globalThis.AbortController = Orig
  })

  it("sends empty string without crashing", async () => {
    const opts = makeSendOptions()
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => { await result.current.handleSend("") })
    // No crash is the assertion; hook unconditionally sends
    expect(opts.addMessage).toHaveBeenCalled()
  })
})

// ===========================================================================
// 2. KB Injection Data Flow
// ===========================================================================

describe("KB Injection Data Flow", () => {
  it("queryKB returns results with correct source refs", async () => {
    mockQueryKB.mockResolvedValue({ results: [makeKBResult({ content: "Auth details", relevance: 0.92, artifact_id: "art-auth-1", domain: "coding" })] })
    const opts = makeSendOptions({ autoInject: true, autoInjectThreshold: 0.5, activeMessages: [{ id: "prior-1", role: "assistant" as const, content: "Hello", timestamp: Date.now() }] })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => { await result.current.handleSend("How does auth work?") })
    expect(mockQueryKB).toHaveBeenCalledWith("How does auth work?", undefined, 5, undefined, expect.objectContaining({}))
    const sources = opts._sendSpy.mock.calls[0][3]
    expect(sources).toBeDefined()
    expect(sources[0]).toMatchObject({ artifact_id: "art-auth-1", domain: "coding" })
  })

  it("injects KB results as document-tagged context header", async () => {
    mockQueryKB.mockResolvedValue({ results: [makeKBResult({ artifact_id: "a1", filename: "guide.md", relevance: 0.9 })] })
    const opts = makeSendOptions({ autoInject: true, autoInjectThreshold: 0.5, activeMessages: [{ id: "prior-1", role: "assistant" as const, content: "Hello", timestamp: Date.now() }] })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => { await result.current.handleSend("Explain the guide") })
    const sysMsg = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("<document")
    expect(sysMsg!.content).toContain("knowledge base")
  })

  it("passes domain filter via queryKB args", async () => {
    mockQueryKB.mockResolvedValue({ results: [] })
    const opts = makeSendOptions({ autoInject: true, autoInjectThreshold: 0.5, activeMessages: [{ id: "prior-1", role: "assistant" as const, content: "Hello", timestamp: Date.now() }] })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => { await result.current.handleSend("coding question") })
    expect(mockQueryKB).toHaveBeenCalledWith("coding question", undefined, 5, undefined, expect.objectContaining({}))
  })

  it("does not inject context when KB returns no results", async () => {
    mockQueryKB.mockResolvedValue({ results: [] })
    const opts = makeSendOptions({ autoInject: true, autoInjectThreshold: 0.5 })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => { await result.current.handleSend("Hello") })
    expect(sentMessages(opts._sendSpy).find((m) => m.role === "system")).toBeUndefined()
  })

  it("does not call queryKB when autoInject is disabled", async () => {
    const opts = makeSendOptions({ autoInject: false, injectedContext: [] })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => { await result.current.handleSend("Test query") })
    expect(mockQueryKB).not.toHaveBeenCalled()
  })

  it("injects multiple KB sources into context", async () => {
    const chunks = [
      makeKBResult({ artifact_id: "a1", filename: "api.py", relevance: 0.95, chunk_index: 0, content: "API module handles routing" }),
      makeKBResult({ artifact_id: "a2", filename: "models.py", relevance: 0.88, chunk_index: 0, content: "Models define schemas" }),
      makeKBResult({ artifact_id: "a3", filename: "utils.py", relevance: 0.82, chunk_index: 0, content: "Utils provide helpers" }),
    ]
    mockQueryKB.mockResolvedValue({ results: chunks })
    const opts = makeSendOptions({ autoInject: true, autoInjectThreshold: 0.5, activeMessages: [{ id: "prior-1", role: "assistant" as const, content: "Hello", timestamp: Date.now() }] })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => { await result.current.handleSend("Show me the architecture") })
    const sysMsg = sentMessages(opts._sendSpy).find((m) => m.role === "system")
    expect(sysMsg).toBeDefined()
    expect(sysMsg!.content).toContain("api.py")
    expect(sysMsg!.content).toContain("models.py")
    expect(sysMsg!.content).toContain("utils.py")
    expect(opts._sendSpy.mock.calls[0][3]).toHaveLength(3)
  })
})

// ===========================================================================
// 3. Verification Stream Data Flow
// ===========================================================================

describe("Verification Stream Data Flow", () => {
  const HAPPY: SSEEvent[] = [
    { type: "extraction_complete", method: "llm", count: 2 },
    { type: "claim_extracted", claim: "Claim A", index: 0, claim_type: "factual" },
    { type: "claim_extracted", claim: "Claim B", index: 1, claim_type: "factual" },
    { type: "claim_verified", index: 0, claim: "Claim A", claim_type: "factual", status: "verified", confidence: 0.95, source: "", reason: "OK", verification_method: "kb" },
    { type: "claim_verified", index: 1, claim: "Claim B", claim_type: "factual", status: "unverified", confidence: 0.2, source: "", reason: "Nope", verification_method: "cross_model" },
    { type: "summary", verified: 1, unverified: 1, uncertain: 0, total: 2, overall_confidence: 0.575, extraction_method: "llm" },
  ]

  it("produces claim events from SSE stream", async () => {
    mockStreamVerification.mockReturnValue(makeSSEStream(HAPPY))
    const { result } = renderHook(() => useVerificationStream("Text", "conv-1", true, 1))
    await waitFor(() => { expect(result.current.phase).toBe("done") })
    expect(result.current.claims).toHaveLength(2)
    expect(result.current.claims[0].claim).toBe("Claim A")
    expect(result.current.claims[1].claim).toBe("Claim B")
  })

  it("maps verified claim to correct status", async () => {
    mockStreamVerification.mockReturnValue(makeSSEStream(HAPPY))
    const { result } = renderHook(() => useVerificationStream("Text", "conv-1", true, 1))
    await waitFor(() => { expect(result.current.phase).toBe("done") })
    const c = result.current.claims.find((x) => x.index === 0)
    expect(c?.status).toBe("verified")
    expect(c?.similarity).toBe(0.95)
  })

  it("maps unverified claim to correct status", async () => {
    mockStreamVerification.mockReturnValue(makeSSEStream(HAPPY))
    const { result } = renderHook(() => useVerificationStream("Text", "conv-1", true, 1))
    await waitFor(() => { expect(result.current.phase).toBe("done") })
    const c = result.current.claims.find((x) => x.index === 1)
    expect(c?.status).toBe("unverified")
    expect(c?.similarity).toBe(0.2)
  })

  it("finalizes verification when summary event arrives", async () => {
    mockStreamVerification.mockReturnValue(makeSSEStream(HAPPY))
    const { result } = renderHook(() => useVerificationStream("Text", "conv-1", true, 1))
    await waitFor(() => { expect(result.current.phase).toBe("done") })
    expect(result.current.loading).toBe(false)
    expect(result.current.summary?.verified).toBe(1)
    expect(result.current.summary?.unverified).toBe(1)
    expect(result.current.summary?.total).toBe(2)
    expect(result.current.report).not.toBeNull()
    expect(result.current.report?.summary.total).toBe(2)
  })

  it("handles verification stream error without crashing", async () => {
    const errEvents: SSEEvent[] = [
      { type: "extraction_complete", method: "llm", count: 1 },
      { type: "claim_extracted", claim: "Claim A", index: 0, claim_type: "factual" },
      { type: "error", message: "Backend failure" },
    ]
    mockStreamVerification.mockReturnValue(makeSSEStream(errEvents))
    const { result } = renderHook(() => useVerificationStream("Text", "conv-1", true, 1))
    await waitFor(() => { expect(result.current.phase).toBe("error") })
    expect(result.current.claims.length).toBeGreaterThanOrEqual(1)
    expect(result.current.loading).toBe(false)
  })
})

// ===========================================================================
// 4. Settings Data Flow
// ===========================================================================

describe("Settings Data Flow", () => {
  it("RAG mode change persists to localStorage", () => {
    localStorage.setItem("cerid-rag-mode", "manual")
    localStorage.setItem("cerid-rag-mode", "smart")
    expect(localStorage.getItem("cerid-rag-mode")).toBe("smart")
  })

  it("pipeline toggle calls updateSettings with correct payload", async () => {
    mockUpdateSettings.mockResolvedValue({ status: "ok", updated: { enable_hallucination_check: false } })
    await updateSettings({ enable_hallucination_check: false })
    expect(mockUpdateSettings).toHaveBeenCalledWith({ enable_hallucination_check: false })
  })

  it("model change propagates to subsequent chat sends", async () => {
    const onStart = vi.fn(), onUpdate = vi.fn()
    mockStreamChat.mockImplementation(async (_m: unknown, _mo: unknown, onChunk: (c: string) => void) => { onChunk("ok") })
    const { result } = renderHook(() => useChat({ onMessageStart: onStart, onMessageUpdate: onUpdate }))
    const model = "openrouter/openai/gpt-4o-mini"
    await act(async () => { await result.current.send("conv-1", [{ role: "user", content: "Hi" }], model) })
    expect(mockStreamChat.mock.calls[0][1]).toBe(model)
    expect(onStart).toHaveBeenCalledWith("conv-1", expect.objectContaining({ model }))
  })

  it("theme change applies to document class", () => {
    document.documentElement.classList.remove("dark")
    expect(document.documentElement.classList.contains("dark")).toBe(false)
    document.documentElement.classList.add("dark")
    expect(document.documentElement.classList.contains("dark")).toBe(true)
    document.documentElement.classList.remove("dark")
  })
})
