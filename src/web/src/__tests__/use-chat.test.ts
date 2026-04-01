// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChat } from "@/hooks/use-chat"

// Mock the API module
vi.mock("@/lib/api", () => ({
  streamChat: vi.fn(),
  ingestFeedback: vi.fn().mockResolvedValue(undefined),
  extractMemories: vi.fn().mockResolvedValue(undefined),
}))

import { streamChat, ingestFeedback, extractMemories } from "@/lib/api"

const mockStreamChat = streamChat as ReturnType<typeof vi.fn>
const mockIngestFeedback = ingestFeedback as ReturnType<typeof vi.fn>
const mockExtractMemories = extractMemories as ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.clearAllMocks()
})

function makeMessages(count: number) {
  const msgs: { role: "user" | "assistant"; content: string }[] = []
  for (let i = 0; i < count; i++) {
    msgs.push({ role: i % 2 === 0 ? "user" : "assistant", content: `msg ${i}` })
  }
  return msgs
}

describe("useChat", () => {
  it("returns initial state with isStreaming false", () => {
    const onMessageStart = vi.fn()
    const onMessageUpdate = vi.fn()
    const { result } = renderHook(() =>
      useChat({ onMessageStart, onMessageUpdate }),
    )
    expect(result.current.isStreaming).toBe(false)
    expect(typeof result.current.send).toBe("function")
    expect(typeof result.current.stop).toBe("function")
  })

  it("sends a message and streams response", async () => {
    const onMessageStart = vi.fn()
    const onMessageUpdate = vi.fn()

    // Simulate streaming: call the chunk callback
    mockStreamChat.mockImplementation(
      async (_msgs: unknown, _model: unknown, onChunk: (chunk: string) => void) => {
        onChunk("Hello ")
        onChunk("world!")
      },
    )

    const { result } = renderHook(() =>
      useChat({ onMessageStart, onMessageUpdate }),
    )

    await act(async () => {
      await result.current.send("conv-1", [{ role: "user", content: "Hi" }], "model-1")
    })

    expect(onMessageStart).toHaveBeenCalledTimes(1)
    expect(onMessageStart).toHaveBeenCalledWith(
      "conv-1",
      expect.objectContaining({ role: "assistant", model: "model-1" }),
    )

    // onMessageUpdate called for each chunk accumulation
    expect(onMessageUpdate).toHaveBeenCalledWith("conv-1", "Hello ")
    expect(onMessageUpdate).toHaveBeenCalledWith("conv-1", "Hello world!")
    expect(result.current.isStreaming).toBe(false)
  })

  it("sets isStreaming during send", async () => {
    const onMessageStart = vi.fn()
    const onMessageUpdate = vi.fn()
    let resolveStream: () => void = () => {}

    mockStreamChat.mockImplementation(() => {
      return new Promise<void>((resolve) => {
        resolveStream = resolve
      })
    })

    const { result } = renderHook(() =>
      useChat({ onMessageStart, onMessageUpdate }),
    )

    let sendPromise: Promise<void>
    act(() => {
      sendPromise = result.current.send("conv-1", [{ role: "user", content: "Hi" }], "model-1")
    })

    // isStreaming should be true while send is in progress
    expect(result.current.isStreaming).toBe(true)

    // Resolve and wait
    await act(async () => {
      resolveStream()
      await sendPromise!
    })
    expect(result.current.isStreaming).toBe(false)
  })

  it("shows error text on streaming failure", async () => {
    const onMessageStart = vi.fn()
    const onMessageUpdate = vi.fn()

    mockStreamChat.mockRejectedValue(new Error("Network error"))

    const { result } = renderHook(() =>
      useChat({ onMessageStart, onMessageUpdate }),
    )

    await act(async () => {
      await result.current.send("conv-1", [{ role: "user", content: "Hi" }], "model-1")
    })

    expect(onMessageUpdate).toHaveBeenCalledWith(
      "conv-1",
      expect.stringContaining("Network error"),
    )
  })

  it("shows warning when no response received", async () => {
    const onMessageStart = vi.fn()
    const onMessageUpdate = vi.fn()

    // Stream resolves but doesn't call onChunk
    mockStreamChat.mockResolvedValue(undefined)

    const { result } = renderHook(() =>
      useChat({ onMessageStart, onMessageUpdate }),
    )

    await act(async () => {
      await result.current.send("conv-1", [{ role: "user", content: "Hi" }], "model-1")
    })

    expect(onMessageUpdate).toHaveBeenCalledWith(
      "conv-1",
      expect.stringContaining("No response received"),
    )
  })

  it("triggers feedback loop when enabled and response is long", async () => {
    const onMessageStart = vi.fn()
    const onMessageUpdate = vi.fn()

    const longResponse = "x".repeat(200)
    mockStreamChat.mockImplementation(
      async (_msgs: unknown, _model: unknown, onChunk: (chunk: string) => void) => {
        onChunk(longResponse)
      },
    )

    const { result } = renderHook(() =>
      useChat({ onMessageStart, onMessageUpdate, feedbackEnabled: true }),
    )

    await act(async () => {
      await result.current.send("conv-1", [{ role: "user", content: "Tell me about X" }], "model-1")
    })

    expect(mockIngestFeedback).toHaveBeenCalledWith(
      "Tell me about X",
      longResponse,
      "model-1",
      "conv-1",
    )
  })

  it("does not trigger feedback when disabled", async () => {
    const onMessageStart = vi.fn()
    const onMessageUpdate = vi.fn()

    const longResponse = "x".repeat(200)
    mockStreamChat.mockImplementation(
      async (_msgs: unknown, _model: unknown, onChunk: (chunk: string) => void) => {
        onChunk(longResponse)
      },
    )

    const { result } = renderHook(() =>
      useChat({ onMessageStart, onMessageUpdate, feedbackEnabled: false }),
    )

    await act(async () => {
      await result.current.send("conv-1", [{ role: "user", content: "Hi" }], "model-1")
    })

    expect(mockIngestFeedback).not.toHaveBeenCalled()
  })

  it("triggers memory extraction after 3+ user messages", async () => {
    const onMessageStart = vi.fn()
    const onMessageUpdate = vi.fn()

    const longResponse = "x".repeat(200)
    mockStreamChat.mockImplementation(
      async (_msgs: unknown, _model: unknown, onChunk: (chunk: string) => void) => {
        onChunk(longResponse)
      },
    )

    const { result } = renderHook(() =>
      useChat({ onMessageStart, onMessageUpdate }),
    )

    // 6 messages = 3 user + 3 assistant (alternating)
    const messages = makeMessages(6)
    await act(async () => {
      await result.current.send("conv-1", messages, "model-1")
    })

    expect(mockExtractMemories).toHaveBeenCalledWith(longResponse, "conv-1", "model-1")
  })

  it("does not trigger memory extraction with fewer than 3 user messages", async () => {
    const onMessageStart = vi.fn()
    const onMessageUpdate = vi.fn()

    const longResponse = "x".repeat(200)
    mockStreamChat.mockImplementation(
      async (_msgs: unknown, _model: unknown, onChunk: (chunk: string) => void) => {
        onChunk(longResponse)
      },
    )

    const { result } = renderHook(() =>
      useChat({ onMessageStart, onMessageUpdate }),
    )

    // 2 messages = 1 user + 1 assistant
    await act(async () => {
      await result.current.send("conv-1", [{ role: "user", content: "Hi" }], "model-1")
    })

    expect(mockExtractMemories).not.toHaveBeenCalled()
  })

  it("passes sourcesUsed to assistant message", async () => {
    const onMessageStart = vi.fn()
    const onMessageUpdate = vi.fn()

    mockStreamChat.mockImplementation(
      async (_msgs: unknown, _model: unknown, onChunk: (chunk: string) => void) => {
        onChunk("response")
      },
    )

    const { result } = renderHook(() =>
      useChat({ onMessageStart, onMessageUpdate }),
    )

    const sources = [{ artifact_id: "a1", filename: "test.py", domain: "coding", relevance: 0.9, chunk_index: 0 }]
    await act(async () => {
      await result.current.send("conv-1", [{ role: "user", content: "Hi" }], "model-1", sources)
    })

    expect(onMessageStart).toHaveBeenCalledWith(
      "conv-1",
      expect.objectContaining({ sourcesUsed: sources }),
    )
  })
})
