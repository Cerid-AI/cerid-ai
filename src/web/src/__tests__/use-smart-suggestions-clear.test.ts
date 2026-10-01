// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Sending a message clears the suggestions, and that has to include the
 * search the last keystroke scheduled. Measured on the live stack,
 * 2026-09-27: the search fired 1,000 ms after the message was sent and ran
 * beside the send's retrieval, which took 10.7 s where it takes 4 alone.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useSmartSuggestions } from "@/hooks/use-smart-suggestions"

vi.mock("@/lib/api", () => ({
  queryKB: vi.fn(),
}))

import { queryKB } from "@/lib/api"

const mockQueryKB = queryKB as ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
  mockQueryKB.mockResolvedValue({ results: [] })
})

afterEach(() => {
  vi.useRealTimers()
})

const TEXT = "what did the mesh network diagnosis find?"

describe("useSmartSuggestions.clear", () => {
  it("cancels the search a keystroke scheduled", async () => {
    const { result } = renderHook(() => useSmartSuggestions({ enabled: true, injectedArtifactIds: [] }))

    act(() => result.current.debouncedSearch(TEXT))
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })
    act(() => result.current.clear())
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })

    expect(mockQueryKB).not.toHaveBeenCalled()
  })

  it("leaves the next keystroke able to search", async () => {
    const { result } = renderHook(() => useSmartSuggestions({ enabled: true, injectedArtifactIds: [] }))

    act(() => result.current.debouncedSearch(TEXT))
    act(() => result.current.clear())
    act(() => result.current.debouncedSearch(TEXT))
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500) })

    expect(mockQueryKB).toHaveBeenCalledTimes(1)
  })
})
