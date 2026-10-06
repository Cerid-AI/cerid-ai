// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Round 5 item 5.3 (D20-A) — regenerate re-sends the last user turn once,
 * with the same retrieval, and replaces the last assistant answer.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChatSend } from "@/hooks/use-chat-send"
import type { ChatMessage, KBQueryResult } from "@/lib/types"
import { MODELS } from "@/lib/types"

vi.mock("@/lib/api", () => ({
  queryKB: vi.fn(),
  recallMemories: vi.fn().mockResolvedValue([]),
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

import { queryKB } from "@/lib/api"

const mockQueryKB = queryKB as ReturnType<typeof vi.fn>
const DEFAULT_MODEL = MODELS[0].id

const chunk: KBQueryResult = {
  content: "grounding chunk",
  relevance: 0.9,
  artifact_id: "art-1",
  filename: "doc.md",
  domain: "general",
  chunk_index: 0,
  collection: "kb",
  ingested_at: "2026-01-01T00:00:00Z",
}

const history: ChatMessage[] = [
  { id: "u0", role: "user", content: "earlier question", timestamp: 1 },
  { id: "a0", role: "assistant", content: "earlier answer", timestamp: 2 },
  { id: "u1", role: "user", content: "what does the doc say", timestamp: 3 },
  { id: "a1", role: "assistant", content: "a weak answer", timestamp: 4 },
]

function makeOptions(overrides: Record<string, unknown> = {}) {
  const sendSpy = vi.fn()
  return {
    activeId: "conv-1",
    activeMessages: history,
    create: vi.fn().mockReturnValue("conv-new"),
    addMessage: vi.fn(),
    updateModel: vi.fn(),
    replaceMessages: vi.fn(),
    stop: vi.fn(),
    send: sendSpy,
    selectedModel: DEFAULT_MODEL,
    setSelectedModel: vi.fn(),
    routingMode: "manual",
    costSensitivity: "medium" as const,
    autoInject: true,
    autoInjectThreshold: 0.15,
    includePacks: true,
    injectedContext: [],
    // The panel already retrieved for the last user message — a regenerate
    // reuses that retrieval instead of querying again.
    kbResults: [chunk],
    kbResultsQuery: "what does the doc say",
    clearInjected: vi.fn(),
    privateModeLevel: 0,
    memoryEnabled: false,
    ...overrides,
    _sendSpy: sendSpy,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockQueryKB.mockResolvedValue({ results: [] })
})

describe("useChatSend — regenerate", () => {
  it("cancels the stream, drops the last turn, and re-sends the prior user text once", async () => {
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await act(async () => {
      await result.current.handleRegenerate()
    })

    expect(opts.stop).toHaveBeenCalledTimes(1)
    // The last user turn and its answer are removed; earlier turns stay.
    expect(opts.replaceMessages).toHaveBeenCalledWith("conv-1", history.slice(0, 2))
    // The user text is re-added once and sent once.
    const userAdds = (opts.addMessage.mock.calls as unknown as [string, ChatMessage][]).filter(
      ([, m]) => m.role === "user",
    )
    expect(userAdds).toHaveLength(1)
    expect(userAdds[0][1].content).toBe("what does the doc say")
    expect(opts._sendSpy).toHaveBeenCalledTimes(1)
    const sent = opts._sendSpy.mock.calls[0][1] as { role: string; content: string }[]
    expect(sent.at(-1)).toMatchObject({ role: "user", content: "what does the doc say" })
    expect(sent.some((m) => m.content === "a weak answer")).toBe(false)
    expect(sent.some((m) => m.content === "earlier answer")).toBe(true)
  })

  it("reuses the retrieval already made for that text instead of querying again", async () => {
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => {
      await result.current.handleRegenerate()
    })
    expect(mockQueryKB).not.toHaveBeenCalled()
    const sent = opts._sendSpy.mock.calls[0][1] as { role: string; content: string }[]
    expect(sent.find((m) => m.role === "system")?.content).toContain("doc.md")
  })

  it("does nothing without a user turn to re-send", async () => {
    const opts = makeOptions({ activeMessages: [] })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => {
      await result.current.handleRegenerate()
    })
    expect(opts.stop).not.toHaveBeenCalled()
    expect(opts._sendSpy).not.toHaveBeenCalled()
  })
})
