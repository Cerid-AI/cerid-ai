// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Tests for useClaimFeedback hook (Phase R.1).
 *
 * Covers:
 * - Optimistic state update on submit
 * - Roll-back on error
 * - submit calls the API module
 * - isPending state during in-flight mutation
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"

// Mock the API module before the hook imports it
vi.mock("@/lib/api/feedback", () => ({
  submitClaimFeedbackV2: vi.fn(),
}))

// Mock the logSwallowedError import used in the hook
vi.mock("@/lib/log-swallowed", () => ({
  logSwallowedError: vi.fn(),
}))

import { submitClaimFeedbackV2 } from "@/lib/api/feedback"
import { useClaimFeedback } from "@/hooks/use-claim-feedback"

const mockedSubmit = submitClaimFeedbackV2 as ReturnType<typeof vi.fn>

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

describe("useClaimFeedback", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("starts with activeSentiment null and isPending false", () => {
    const { result } = renderHook(() => useClaimFeedback(), { wrapper: makeWrapper() })
    expect(result.current.activeSentiment).toBeNull()
    expect(result.current.isPending).toBe(false)
  })

  it("optimistically sets activeSentiment on submit", async () => {
    mockedSubmit.mockResolvedValueOnce({ ok: true, rating_id: "r-001" })

    const { result } = renderHook(() => useClaimFeedback({ sessionId: "sess-test" }), {
      wrapper: makeWrapper(),
    })

    act(() => {
      void result.current.submit("claim-001", 1)
    })

    // Optimistic update should be immediate
    await waitFor(() => {
      expect(result.current.activeSentiment).toBe(1)
    })

    expect(mockedSubmit).toHaveBeenCalledWith({
      claim_id: "claim-001",
      sentiment: 1,
      session_id: "sess-test",
      user_id: undefined,
    })
  })

  it("sets activeSentiment to -1 for thumbs-down", async () => {
    mockedSubmit.mockResolvedValueOnce({ ok: true, rating_id: "r-002" })

    const { result } = renderHook(() => useClaimFeedback(), { wrapper: makeWrapper() })

    await act(async () => {
      await result.current.submit("claim-002", -1)
    })

    expect(result.current.activeSentiment).toBe(-1)
  })

  it("rolls back activeSentiment on API error", async () => {
    // First successful submit to set an initial state
    mockedSubmit.mockResolvedValueOnce({ ok: true, rating_id: "r-003" })

    const { result } = renderHook(() => useClaimFeedback(), { wrapper: makeWrapper() })

    await act(async () => {
      await result.current.submit("claim-003", 1)
    })
    expect(result.current.activeSentiment).toBe(1)

    // Now fail a re-rate
    mockedSubmit.mockRejectedValueOnce(new Error("network error"))

    await act(async () => {
      try {
        await result.current.submit("claim-003", -1)
      } catch {
        // expected
      }
    })

    await waitFor(() => {
      // Should roll back to the previous value (1)
      expect(result.current.activeSentiment).toBe(1)
    })
  })

  it("passes userId when provided", async () => {
    mockedSubmit.mockResolvedValueOnce({ ok: true, rating_id: "r-004" })

    const { result } = renderHook(
      () => useClaimFeedback({ userId: "user-abc" }),
      { wrapper: makeWrapper() },
    )

    await act(async () => {
      await result.current.submit("claim-004", 0)
    })

    expect(mockedSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ user_id: "user-abc", sentiment: 0 }),
    )
  })
})
