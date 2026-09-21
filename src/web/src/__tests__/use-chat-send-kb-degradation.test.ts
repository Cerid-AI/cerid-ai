// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * H009 — the KB auto-inject leg must tell three states apart: the knowledge
 * base failed, the knowledge base did not answer at all, and the knowledge
 * base answered with nothing.
 *
 * The 500 ms inject budget is a responsiveness budget, not a health signal.
 * Treating a breach of it as degradation arms the honest-deferral gate on a
 * healthy backend that merely answered slowly, which replaces a good answer
 * with a refusal. The degradation verdict has its own, longer budget, and the
 * grounding-critical questions wait for it rather than guessing.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChatSend } from "@/hooks/use-chat-send"
import type { ChatMessage, KBQueryResult } from "@/lib/types"
import { MODELS } from "@/lib/types"

vi.mock("@/lib/api", () => ({
  queryKB: vi.fn(),
  recallMemories: vi.fn(),
  compressConversation: vi.fn().mockResolvedValue({
    messages: [],
    original_tokens: 0,
    compressed_tokens: 0,
  }),
}))

vi.mock("@/lib/model-router", () => ({
  recommendModel: vi.fn().mockReturnValue({
    model: { id: "openrouter/anthropic/claude-sonnet-4.6", effectiveContextWindow: 800_000 },
    estimatedCost: 0,
    reasoning: "",
    savingsVsCurrent: 0,
  }),
}))

import { queryKB, recallMemories } from "@/lib/api"

const mockQueryKB = queryKB as ReturnType<typeof vi.fn>
const mockRecallMemories = recallMemories as ReturnType<typeof vi.fn>

const makeMessage = (role: "user" | "assistant", content: string): ChatMessage => ({
  id: `msg-${Math.random().toString(36).slice(2, 8)}`,
  role,
  content,
  timestamp: Date.now(),
})

const CHUNK: KBQueryResult = {
  content: "Acme invoice received Tuesday, due in 30 days",
  relevance: 0.9,
  artifact_id: "a1",
  filename: "invoice.eml",
  domain: "mail",
  chunk_index: 0,
  collection: "kb_mail",
  ingested_at: "2026-08-01T10:00:00Z",
}

function makeOptions(overrides: Record<string, unknown> = {}) {
  const sendSpy = vi.fn()
  const addMessageSpy = vi.fn()
  return {
    activeId: "conv-1",
    activeMessages: [makeMessage("assistant", "Hello")] as ChatMessage[],
    create: vi.fn().mockReturnValue("conv-new"),
    addMessage: addMessageSpy,
    updateModel: vi.fn(),
    replaceMessages: vi.fn(),
    send: sendSpy,
    selectedModel: MODELS[0].id,
    setSelectedModel: vi.fn(),
    routingMode: "manual",
    costSensitivity: "medium" as const,
    autoInject: true,
    autoInjectThreshold: 0.5,
    includePacks: true,
    injectedContext: [] as KBQueryResult[],
    kbResults: [] as KBQueryResult[],
    clearInjected: vi.fn(),
    privateModeLevel: 0,
    onBeforeSend: vi.fn(),
    ...overrides,
    _sendSpy: sendSpy,
    _addMessageSpy: addMessageSpy,
  }
}

/** Drive handleSend to completion with the fake clock. */
async function send(result: { current: { handleSend: (s: string) => Promise<void> } }, text: string) {
  await act(async () => {
    const done = result.current.handleSend(text)
    await vi.advanceTimersByTimeAsync(10_000)
    await done
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  mockQueryKB.mockResolvedValue({ results: [] })
  mockRecallMemories.mockResolvedValue([])
})

afterEach(() => {
  vi.useRealTimers()
})

describe("useChatSend — KB degradation threshold", () => {
  it("does not call a slow-but-healthy answer a degradation", async () => {
    // 900 ms: past the inject budget, nowhere near a failure.
    mockQueryKB.mockImplementation(
      () => new Promise((res) => setTimeout(() => res({ results: [CHUNK] }), 900)),
    )
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what invoices arrived in my mail this week?")

    expect(opts._sendSpy).toHaveBeenCalled()
    expect(opts._sendSpy.mock.calls[0][4]).toBeUndefined()
  })

  it("uses the grounding a slow backend eventually returned instead of deferring", async () => {
    mockQueryKB.mockImplementation(
      () => new Promise((res) => setTimeout(() => res({ results: [CHUNK] }), 900)),
    )
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what invoices arrived in my mail this week?")

    expect(opts._sendSpy).toHaveBeenCalled()
    const sources = opts._sendSpy.mock.calls[0][3]
    expect(sources).toHaveLength(1)
    expect(sources[0].artifact_id).toBe("a1")
  })

  it("reports a knowledge base that never answers as degraded, and defers", async () => {
    mockQueryKB.mockImplementation(() => new Promise(() => {}))
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what invoices arrived in my mail this week?")

    expect(opts._sendSpy).not.toHaveBeenCalled()
    const added = opts._addMessageSpy.mock.calls.map((c: unknown[]) => c[1] as ChatMessage)
    const deferral = added[added.length - 1]
    expect(deferral.role).toBe("assistant")
    expect(deferral.degradedReason).toMatch(/did not answer/i)
  })

  it("renders a failed knowledge base differently from one that answered with nothing", async () => {
    mockQueryKB.mockRejectedValue(new Error("KB backend 503"))
    const failing = makeOptions()
    const failed = renderHook(() => useChatSend(failing))
    await send(failed.result, "what invoices arrived in my mail this week?")

    const failMsgs = failing._addMessageSpy.mock.calls.map((c: unknown[]) => c[1] as ChatMessage)
    const deferral = failMsgs[failMsgs.length - 1]
    expect(deferral.role).toBe("assistant")
    // The user has to be able to tell "your KB is unreachable" from
    // "your KB has nothing on this".
    expect(deferral.content).toMatch(/could not be reached|unreachable|failed/i)
    expect(deferral.content).not.toMatch(/nothing (matching|relevant)/i)

    mockQueryKB.mockResolvedValue({ results: [] })
    const empty = makeOptions()
    const emptyHook = renderHook(() => useChatSend(empty))
    await send(emptyHook.result, "what invoices arrived in my mail this week?")

    // A genuinely empty KB is not a degradation: the model answers normally.
    expect(empty._sendSpy).toHaveBeenCalled()
    expect(empty._sendSpy.mock.calls[0][4]).toBeUndefined()
  })

  it("does not degrade a general-knowledge question that outran the inject budget", async () => {
    mockQueryKB.mockImplementation(
      () => new Promise((res) => setTimeout(() => res({ results: [] }), 2_000)),
    )
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "explain how BM25 ranking works")

    expect(opts._sendSpy).toHaveBeenCalled()
    expect(opts._sendSpy.mock.calls[0][4]).toBeUndefined()
  })

  it("still reports an outright KB failure as degraded — the control", async () => {
    mockQueryKB.mockRejectedValue(new Error("KB backend 503"))
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "tell me about auth protocols")

    expect(opts._sendSpy.mock.calls[0][4]).toBe("KB search failed")
  })
})
