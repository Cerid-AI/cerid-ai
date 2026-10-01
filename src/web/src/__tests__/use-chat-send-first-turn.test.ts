// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * The first message of a conversation is grounded like any other.
 *
 * Auto-inject used to be skipped when the conversation had no messages, so
 * the first answer never saw the knowledge base: "According to my documents,
 * what port does the relay listen on?" was answered "I don't have access to
 * your documents" while the Knowledge Console listed the document at 100%.
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

import { queryKB, recallMemories } from "@/lib/api"

const mockQueryKB = queryKB as ReturnType<typeof vi.fn>
const mockRecallMemories = recallMemories as ReturnType<typeof vi.fn>

const CHUNK: KBQueryResult = {
  content: "The relay service listens on port 7443",
  relevance: 0.9,
  artifact_id: "a1",
  filename: "relay.md",
  domain: "projects",
  chunk_index: 0,
  collection: "kb_projects",
  ingested_at: "2026-09-01T10:00:00Z",
}

function makeOptions(overrides: Record<string, unknown> = {}) {
  const sendSpy = vi.fn()
  return {
    activeId: null,
    activeMessages: undefined as ChatMessage[] | undefined,
    create: vi.fn().mockReturnValue("conv-new"),
    addMessage: vi.fn(),
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
  }
}

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
  mockQueryKB.mockResolvedValue({ results: [CHUNK] })
  mockRecallMemories.mockResolvedValue([])
})

afterEach(() => {
  vi.useRealTimers()
})

describe("useChatSend: the first message of a conversation", () => {
  it.each([
    ["a conversation that does not exist yet", { activeId: null, activeMessages: undefined }],
    ["an empty conversation", { activeId: "conv-1", activeMessages: [] }],
  ])("queries the knowledge base and sends what it found (%s)", async (_name, state) => {
    const opts = makeOptions(state)
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what port does the relay service listen on?")

    expect(mockQueryKB).toHaveBeenCalledTimes(1)
    expect(mockQueryKB.mock.calls[0][0]).toBe("what port does the relay service listen on?")
    const [, messages, , sources] = opts._sendSpy.mock.calls[0]
    expect(sources).toHaveLength(1)
    expect(sources[0].artifact_id).toBe("a1")
    expect(messages[0].role).toBe("system")
    expect(messages[0].content).toContain("The relay service listens on port 7443")
  })

  it("waits for a knowledge base that takes four seconds, whatever the question", async () => {
    mockQueryKB.mockImplementation(
      () => new Promise((res) => setTimeout(() => res({ results: [CHUNK] }), 4_000)),
    )
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what port does the relay service listen on?")

    expect(opts._sendSpy.mock.calls[0][3]).toHaveLength(1)
    expect(opts._sendSpy.mock.calls[0][4]).toBeUndefined()
  })

  it("sends without grounding once the knowledge base has been silent for five seconds", async () => {
    mockQueryKB.mockImplementation(
      () => new Promise((res) => setTimeout(() => res({ results: [CHUNK] }), 6_000)),
    )
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what port does the relay service listen on?")

    expect(opts._sendSpy).toHaveBeenCalledTimes(1)
    expect(opts._sendSpy.mock.calls[0][3]).toBeUndefined()
  })

  it("sends nothing from the knowledge base in private mode level 2", async () => {
    const opts = makeOptions({ privateModeLevel: 2 })
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what port does the relay service listen on?")

    expect(mockQueryKB).not.toHaveBeenCalled()
    expect(opts._sendSpy.mock.calls[0][3]).toBeUndefined()
  })
})
