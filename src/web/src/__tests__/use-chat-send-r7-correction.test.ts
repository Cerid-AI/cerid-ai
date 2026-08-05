// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * E1 R7 — correction re-send clears inject dedup so KB re-injects.
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

function makeOptions(overrides: Record<string, unknown> = {}) {
  const sendSpy = vi.fn()
  return {
    activeId: "conv-1",
    activeMessages: [
      { id: "m1", role: "assistant" as const, content: "prior", timestamp: Date.now() },
    ] as ChatMessage[],
    create: vi.fn().mockReturnValue("conv-new"),
    addMessage: vi.fn(),
    updateModel: vi.fn(),
    replaceMessages: vi.fn(),
    send: sendSpy,
    selectedModel: DEFAULT_MODEL,
    setSelectedModel: vi.fn(),
    routingMode: "manual",
    costSensitivity: "medium" as const,
    autoInject: true,
    autoInjectThreshold: 0.15,
    includePacks: true,
    injectedContext: [],
    kbResults: [],
    clearInjected: vi.fn(),
    privateModeLevel: 0,
    memoryEnabled: false,
    ...overrides,
    _sendSpy: sendSpy,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockQueryKB.mockResolvedValue({ results: [chunk] })
})

describe("useChatSend — R7 correction re-inject", () => {
  it("baseMessages path injects KB even after a prior inject of the same chunk", async () => {
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    // First send injects art-1 into session history
    await act(async () => {
      await result.current.handleSend("first question about doc")
    })
    expect(opts._sendSpy).toHaveBeenCalled()
    const firstSys = opts._sendSpy.mock.calls[0][1].find(
      (m: { role: string }) => m.role === "system",
    )
    expect(firstSys?.content).toContain("doc.md")

    // Correction re-send with truncated baseMessages must still get grounding
    mockQueryKB.mockClear()
    mockQueryKB.mockResolvedValue({ results: [chunk] })
    await act(async () => {
      await result.current.handleSend("[Correction] fix it", [
        { id: "u1", role: "user", content: "first question about doc", timestamp: 1 },
      ])
    })
    const secondMsgs = opts._sendSpy.mock.calls[1][1] as { role: string; content: string }[]
    const secondSys = secondMsgs.find((m) => m.role === "system")
    expect(secondSys?.content).toContain("doc.md")
  })
})
