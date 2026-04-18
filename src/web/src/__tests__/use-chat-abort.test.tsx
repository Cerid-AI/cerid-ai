// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChat } from "@/hooks/use-chat"

// Mock the API module — streamChat must receive an AbortSignal as its 4th arg
// (see src/web/src/lib/api/chat.ts::streamChat signature).
vi.mock("@/lib/api", () => ({
  streamChat: vi.fn(),
  ingestFeedback: vi.fn().mockResolvedValue(undefined),
  extractMemories: vi.fn().mockResolvedValue(undefined),
}))

import { streamChat } from "@/lib/api"

const mockStreamChat = streamChat as ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.clearAllMocks()
})

describe("useChat — abort cleanup (Task 9)", () => {
  it("aborts the in-flight streamChat when the hook unmounts", async () => {
    const abortSpy = vi.fn()

    // Capture the signal passed to streamChat and wire an abort listener; return
    // a promise that never resolves (simulating a hanging SSE stream).
    mockStreamChat.mockImplementation(
      async (
        _msgs: unknown,
        _model: unknown,
        _onChunk: (chunk: string) => void,
        signal?: AbortSignal,
      ) => {
        signal?.addEventListener("abort", abortSpy)
        return new Promise<void>(() => {
          // never resolves
        })
      },
    )

    const onMessageStart = vi.fn()
    const onMessageUpdate = vi.fn()

    const { result, unmount } = renderHook(() =>
      useChat({ onMessageStart, onMessageUpdate }),
    )

    // Fire a send and let it reach streamChat (don't await — the mock hangs).
    act(() => {
      void result.current.send("conv-1", [{ role: "user", content: "hello" }], "model-1")
    })

    // Microtask flush so useCallback body runs up to streamChat().
    await act(async () => {
      await Promise.resolve()
    })

    expect(mockStreamChat).toHaveBeenCalled()
    expect(abortSpy).not.toHaveBeenCalled()

    unmount()

    // Give the abort event a chance to fire.
    await new Promise((r) => setTimeout(r, 10))

    expect(abortSpy).toHaveBeenCalled()
  })
})
