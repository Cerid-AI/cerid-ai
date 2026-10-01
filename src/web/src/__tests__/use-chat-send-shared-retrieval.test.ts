// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * One retrieval per message.
 *
 * Measured on the live stack, 2026-09-27: the send and the panel each queried
 * the knowledge base for the same text at the same moment, and the three
 * requests took 16 s where one takes 4. The send outran its wait and the
 * first answer was deferred. Given the panel's retrieval, the send uses it
 * and makes no request of its own.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChatSend } from "@/hooks/use-chat-send"
import type { ChatMessage, KBQueryResult } from "@/lib/types"
import { MODELS } from "@/lib/types"

vi.mock("@/lib/api", () => ({
  queryKB: vi.fn(),
  recallMemories: vi.fn(),
  compressConversation: vi.fn().mockResolvedValue({ messages: [], original_tokens: 0, compressed_tokens: 0 }),
}))

import { queryKB, recallMemories } from "@/lib/api"

const mockQueryKB = queryKB as ReturnType<typeof vi.fn>
const mockRecallMemories = recallMemories as ReturnType<typeof vi.fn>

const CHUNK: KBQueryResult = {
  content: "The cutover moved DNS off the old provider",
  relevance: 0.9,
  artifact_id: "a1",
  filename: "cutover.md",
  domain: "projects",
  chunk_index: 0,
  collection: "kb_projects",
  ingested_at: "2026-09-01T10:00:00Z",
}

function makeOptions(overrides: Record<string, unknown> = {}) {
  const sendSpy = vi.fn()
  const addMessageSpy = vi.fn()
  return {
    activeId: null,
    activeMessages: undefined as ChatMessage[] | undefined,
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

describe("useChatSend: retrieval shared with the panel", () => {
  it("uses the panel's retrieval for the text being sent and makes no request of its own", async () => {
    const retrieve = vi.fn().mockResolvedValue({ results: [CHUNK] })
    const opts = makeOptions({ retrieve })
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what changed in the cutover?")

    expect(retrieve).toHaveBeenCalledTimes(1)
    expect(retrieve).toHaveBeenCalledWith("what changed in the cutover?")
    expect(mockQueryKB).not.toHaveBeenCalled()
    expect(opts._sendSpy.mock.calls[0][3].map((s: { artifact_id: string }) => s.artifact_id)).toEqual(["a1"])
  })

  it("waits for a shared retrieval that takes four seconds", async () => {
    const retrieve = vi.fn(
      () => new Promise((res) => setTimeout(() => res({ results: [CHUNK] }), 4_000)),
    )
    const opts = makeOptions({ retrieve })
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what changed in the cutover?")

    expect(opts._sendSpy.mock.calls[0][3]).toHaveLength(1)
  })

  it("reports a shared retrieval that fails as a failed search, and defers a question about the user's own data", async () => {
    const retrieve = vi.fn().mockRejectedValue(new Error("KB query failed: 503"))
    const opts = makeOptions({ retrieve })
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what changed in my cutover notes?")

    expect(opts._sendSpy).not.toHaveBeenCalled()
    const deferral = opts._addMessageSpy.mock.calls.map((c) => c[1]).find((m) => m.role === "assistant")
    expect(deferral.degradedReason).toBe("KB search failed")
  })
})

describe("useChatSend: its own request, when it has to make one", () => {
  it("cancels a request that outran the wait", async () => {
    let signal: AbortSignal | undefined
    mockQueryKB.mockImplementation((_q, _d, _k, _m, o: { signal: AbortSignal }) => {
      signal = o.signal
      return new Promise(() => {})
    })
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what changed in the cutover?")

    expect(opts._sendSpy).toHaveBeenCalledTimes(1)
    expect(signal?.aborted).toBe(true)
  })

  it("does not call a knowledge base that never answered one that failed", async () => {
    mockQueryKB.mockImplementation(
      (_q, _d, _k, _m, o: { signal: AbortSignal }) =>
        new Promise((_res, rej) => o.signal.addEventListener("abort", () => rej(new Error("aborted")))),
    )
    const opts = makeOptions()
    const { result } = renderHook(() => useChatSend(opts))

    await send(result, "what changed in my cutover notes?")

    const deferral = opts._addMessageSpy.mock.calls.map((c) => c[1]).find((m) => m.role === "assistant")
    expect(deferral.degradedReason).toMatch(/did not answer within 5000ms/)
  })
})
