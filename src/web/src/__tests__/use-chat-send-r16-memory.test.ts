// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * E1 M3 R16 — FE auto-inject must not call recallMemories when Memory is off.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChatSend } from "@/hooks/use-chat-send"
import type { ChatMessage } from "@/lib/types"
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
const mockRecall = recallMemories as ReturnType<typeof vi.fn>

const DEFAULT_MODEL = MODELS[0].id

function makeOptions(overrides: Record<string, unknown> = {}) {
  const sendSpy = vi.fn()
  return {
    activeId: "conv-1",
    activeMessages: [
      { id: "m1", role: "assistant" as const, content: "Hi", timestamp: Date.now() },
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
    memoryEnabled: true,
    ...overrides,
    _sendSpy: sendSpy,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockQueryKB.mockResolvedValue({ results: [] })
  mockRecall.mockResolvedValue([{ text: "secret memory", adjusted_score: 0.9 }])
})

describe("useChatSend — R16 memory gate", () => {
  it("does not call recallMemories when memoryEnabled is false", async () => {
    const opts = makeOptions({ memoryEnabled: false })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => {
      await result.current.handleSend("enough text for auto inject path")
    })
    expect(mockRecall).not.toHaveBeenCalled()
  })

  it("calls recallMemories when memoryEnabled is true", async () => {
    const opts = makeOptions({ memoryEnabled: true })
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => {
      await result.current.handleSend("enough text for auto inject path")
    })
    expect(mockRecall).toHaveBeenCalled()
  })
})
