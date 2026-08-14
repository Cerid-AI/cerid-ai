// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Honest degradation deferral (sf-2 / UX-01): when retrieval is degraded and
 * a personal-data question has zero grounding, chat must NOT stream the LLM
 * (which reliably fabricates "I don't have access to your Apple Mail") —
 * it appends an honest deferral message carrying degradedReason instead.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChatSend, isPersonalDataQuery } from "@/hooks/use-chat-send"
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
    estimatedCost: 0, reasoning: "", savingsVsCurrent: 0,
  }),
}))

import { queryKB, recallMemories } from "@/lib/api"

const mockQueryKB = queryKB as ReturnType<typeof vi.fn>
const mockRecallMemories = recallMemories as ReturnType<typeof vi.fn>

const DEGRADED = "Retrieval took longer than the configured budget."

beforeEach(() => {
  vi.clearAllMocks()
  mockQueryKB.mockResolvedValue({ results: [] })
  mockRecallMemories.mockResolvedValue([])
})

const makeMessage = (role: "user" | "assistant", content: string): ChatMessage => ({
  id: `msg-${Math.random().toString(36).slice(2, 8)}`,
  role,
  content,
  timestamp: Date.now(),
})

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
    autoInject: false,
    autoInjectThreshold: 0.6,
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

describe("isPersonalDataQuery", () => {
  it("matches questions about the user's own data", () => {
    for (const q of [
      "what invoices arrived in my mail this week?",
      "did I pay the water bill?",
      "show receipts from my Apple Mail",
      "have I received anything from the bank?",
    ]) {
      expect(isPersonalDataQuery(q), q).toBe(true)
    }
  })

  it("does not match general-knowledge questions", () => {
    for (const q of [
      "what is the capital of France?",
      "explain how BM25 ranking works",
      "compare React and Vue",
    ]) {
      expect(isPersonalDataQuery(q), q).toBe(false)
    }
  })
})

describe("useChatSend — degraded-retrieval deferral", () => {
  it("defers instead of streaming when degraded + personal-data + zero grounding", async () => {
    const opts = makeOptions({ degradedReason: DEGRADED })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("what invoices arrived in my mail this week?")
    })

    // No LLM stream fired.
    expect(opts._sendSpy).not.toHaveBeenCalled()
    // User message + honest assistant deferral were appended.
    const added = opts._addMessageSpy.mock.calls.map((c: unknown[]) => c[1] as ChatMessage)
    expect(added).toHaveLength(2)
    expect(added[1].role).toBe("assistant")
    expect(added[1].degradedReason).toBe(DEGRADED)
    expect(added[1].content).toMatch(/won't\s+guess about your personal data/)
  })

  it("streams normally when degraded but the question is general knowledge", async () => {
    const opts = makeOptions({ degradedReason: DEGRADED })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("what is the capital of France?")
    })

    expect(opts._sendSpy).toHaveBeenCalled()
    // degradedReason still travels with the send for the per-message banner.
    expect(opts._sendSpy.mock.calls[0][4]).toBe(DEGRADED)
  })

  it("streams normally when retrieval is not degraded", async () => {
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("what invoices arrived in my mail this week?")
    })

    expect(opts._sendSpy).toHaveBeenCalled()
  })

  it("streams normally when grounding exists despite degradation", async () => {
    const kbChunk: KBQueryResult = {
      content: "Invoice #123 from Acme, received Tuesday", // drift-allowed: an invoice number in fixture prose, not a colour
      relevance: 0.9,
      artifact_id: "a1",
      filename: "invoice.eml",
      domain: "mail",
      chunk_index: 0,
      collection: "kb_mail",
      ingested_at: "2026-08-01T10:00:00Z",
    }
    const opts = makeOptions({
      degradedReason: DEGRADED,
      injectedContext: [kbChunk],
    })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("what invoices arrived in my mail this week?")
    })

    expect(opts._sendSpy).toHaveBeenCalled()
  })

  it("respects Private Mode L2 (user chose ungrounded chat)", async () => {
    const opts = makeOptions({ degradedReason: DEGRADED, privateModeLevel: 2 })
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleSend("what invoices arrived in my mail this week?")
    })

    expect(opts._sendSpy).toHaveBeenCalled()
  })
})
