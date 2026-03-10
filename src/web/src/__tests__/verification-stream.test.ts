// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for useVerificationStream — the SSE-based verification streaming hook.
 *
 * Covers:
 * 1. Idle when preconditions not met (triggerKey=0, disabled, no text)
 * 2. Happy path: extracting → verifying → done
 * 3. Error on stream without summary
 * 4. Error on fetch failure
 * 5. Error on backend error event
 * 6. responseText changes don't restart stream (ref-based)
 * 7. triggerKey changes restart stream
 * 8. Abort on unmount
 * 9. Claim counts tracked
 * 10. Conversation switch resets state
 */

import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { useVerificationStream } from "@/hooks/use-verification-stream"

// ---------------------------------------------------------------------------
// Mock streamVerification
// ---------------------------------------------------------------------------

type SSEEvent = Record<string, unknown>

function makeSSEStream(events: SSEEvent[]) {
  let aborted = false
  const abort = vi.fn(() => { aborted = true })

  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const event of events) {
        if (aborted) break
        const line = `data: ${JSON.stringify(event)}\n\n`
        controller.enqueue(new TextEncoder().encode(line))
      }
      controller.close()
    },
  })

  const response = Promise.resolve({
    ok: true,
    status: 200,
    body,
  } as unknown as Response)

  return { response, abort }
}

let mockStreamFn: Mock

vi.mock("@/lib/api", () => ({
  streamVerification: (...args: unknown[]) => mockStreamFn(...args),
}))

const HAPPY_EVENTS: SSEEvent[] = [
  { type: "extraction_complete", method: "llm", count: 2 },
  { type: "claim_extracted", claim: "Claim A", index: 0, claim_type: "factual" },
  { type: "claim_extracted", claim: "Claim B", index: 1, claim_type: "factual" },
  { type: "claim_verified", index: 0, claim: "Claim A", claim_type: "factual", status: "verified", confidence: 0.95, source: "", reason: "OK", verification_method: "cross_model" },
  { type: "claim_verified", index: 1, claim: "Claim B", claim_type: "factual", status: "unverified", confidence: 0.3, source: "", reason: "Nope", verification_method: "kb" },
  { type: "summary", verified: 1, unverified: 1, uncertain: 0, total: 2, overall_confidence: 0.625, extraction_method: "llm" },
]

beforeEach(() => {
  mockStreamFn = vi.fn()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("useVerificationStream", () => {
  // --- Precondition guards ---

  it("stays idle when triggerKey is 0", () => {
    const { result } = renderHook(() =>
      useVerificationStream("Some response", "conv-1", true, 0),
    )
    expect(result.current.phase).toBe("idle")
    expect(result.current.loading).toBe(false)
    expect(mockStreamFn).not.toHaveBeenCalled()
  })

  it("stays idle when disabled", () => {
    const { result } = renderHook(() =>
      useVerificationStream("Some response", "conv-1", false, 1),
    )
    expect(result.current.phase).toBe("idle")
    expect(mockStreamFn).not.toHaveBeenCalled()
  })

  it("stays idle when responseText is null", () => {
    const { result } = renderHook(() =>
      useVerificationStream(null, "conv-1", true, 1),
    )
    expect(result.current.phase).toBe("idle")
    expect(mockStreamFn).not.toHaveBeenCalled()
  })

  // --- Happy path ---

  it("completes happy path: extracting → verifying → done", async () => {
    mockStreamFn.mockReturnValue(makeSSEStream(HAPPY_EVENTS))

    const { result } = renderHook(() =>
      useVerificationStream("Test response text", "conv-1", true, 1),
    )

    // Should immediately start extracting
    expect(result.current.phase).toBe("extracting")
    expect(result.current.loading).toBe(true)

    await waitFor(() => {
      expect(result.current.phase).toBe("done")
    })

    expect(result.current.loading).toBe(false)
    expect(result.current.claims).toHaveLength(2)
    expect(result.current.summary?.verified).toBe(1)
    expect(result.current.summary?.unverified).toBe(1)
    expect(result.current.report).not.toBeNull()
    expect(result.current.report?.summary.total).toBe(2)
    expect(result.current.sessionClaimsChecked).toBe(2)
  })

  // --- Error scenarios ---

  it("transitions to error when stream ends without summary", async () => {
    const eventsNoSummary = HAPPY_EVENTS.slice(0, 5) // no summary event
    mockStreamFn.mockReturnValue(makeSSEStream(eventsNoSummary))

    const { result } = renderHook(() =>
      useVerificationStream("Test response", "conv-1", true, 1),
    )

    await waitFor(() => {
      expect(result.current.phase).toBe("error")
    })

    expect(result.current.loading).toBe(false)
    expect(result.current.report).toBeNull()
  })

  it("transitions to error when fetch response is not ok", async () => {
    mockStreamFn.mockReturnValue({
      response: Promise.resolve({ ok: false, status: 500, body: null } as unknown as Response),
      abort: vi.fn(),
    })

    const { result } = renderHook(() =>
      useVerificationStream("Test response", "conv-1", true, 1),
    )

    await waitFor(() => {
      expect(result.current.phase).toBe("error")
    })
    expect(result.current.loading).toBe(false)
  })

  it("transitions to error on backend error event", async () => {
    const events: SSEEvent[] = [
      { type: "extraction_complete", method: "llm", count: 1 },
      { type: "claim_extracted", claim: "Claim A", index: 0, claim_type: "factual" },
      { type: "error", message: "Internal server error" },
    ]
    mockStreamFn.mockReturnValue(makeSSEStream(events))

    const { result } = renderHook(() =>
      useVerificationStream("Test response", "conv-1", true, 1),
    )

    await waitFor(() => {
      expect(result.current.phase).toBe("error")
    })
  })

  // --- Stability: ref-based dependencies ---

  it("does NOT restart stream when responseText changes (uses ref)", async () => {
    mockStreamFn.mockReturnValue(makeSSEStream(HAPPY_EVENTS))

    const { rerender } = renderHook(
      ({ text }) => useVerificationStream(text, "conv-1", true, 1),
      { initialProps: { text: "Original response" } },
    )

    expect(mockStreamFn).toHaveBeenCalledTimes(1)

    // Simulate responseText changing (e.g., debounced save)
    mockStreamFn.mockReturnValue(makeSSEStream(HAPPY_EVENTS))
    rerender({ text: "Original response with minor update" })

    // Should NOT have re-triggered — still only 1 call
    expect(mockStreamFn).toHaveBeenCalledTimes(1)
  })

  it("restarts stream when triggerKey changes", () => {
    mockStreamFn.mockReturnValue(makeSSEStream(HAPPY_EVENTS))

    const { rerender } = renderHook(
      ({ key }) => useVerificationStream("Some text", "conv-1", true, key),
      { initialProps: { key: 1 } },
    )

    expect(mockStreamFn).toHaveBeenCalledTimes(1)

    mockStreamFn.mockReturnValue(makeSSEStream(HAPPY_EVENTS))
    rerender({ key: 2 })

    expect(mockStreamFn).toHaveBeenCalledTimes(2)
  })

  // --- Cleanup ---

  it("aborts stream on unmount", () => {
    const stream = makeSSEStream(HAPPY_EVENTS)
    mockStreamFn.mockReturnValue(stream)

    const { unmount } = renderHook(() =>
      useVerificationStream("text", "conv-1", true, 1),
    )

    unmount()
    expect(stream.abort).toHaveBeenCalled()
  })

  // --- Conversation switch ---

  it("resets claims when conversationId changes", async () => {
    mockStreamFn.mockReturnValue(makeSSEStream(HAPPY_EVENTS))

    const { result, rerender } = renderHook(
      ({ convId }) => useVerificationStream("text", convId, true, 1),
      { initialProps: { convId: "conv-1" } },
    )

    await waitFor(() => {
      expect(result.current.phase).toBe("done")
    })
    expect(result.current.claims).toHaveLength(2)

    // Switch conversation — claims should reset
    mockStreamFn.mockReturnValue(makeSSEStream(HAPPY_EVENTS))
    rerender({ convId: "conv-2" })

    // The conversationId reset effect clears claims synchronously
    // Then a new stream starts for the new conversation
    await waitFor(() => {
      expect(result.current.phase).toBe("done")
    })
    // Claims should be from the new stream (2 claims again, not accumulated)
    expect(result.current.claims).toHaveLength(2)
  })

  // --- Claim tracking ---

  it("tracks verified and total claim counts", async () => {
    mockStreamFn.mockReturnValue(makeSSEStream(HAPPY_EVENTS))

    const { result } = renderHook(() =>
      useVerificationStream("text", "conv-1", true, 1),
    )

    await waitFor(() => {
      expect(result.current.phase).toBe("done")
    })

    expect(result.current.verifiedCount).toBe(2) // both claims have non-pending status
    expect(result.current.totalClaims).toBe(2)
  })

  it("builds report with correct structure on completion", async () => {
    mockStreamFn.mockReturnValue(makeSSEStream(HAPPY_EVENTS))

    const { result } = renderHook(() =>
      useVerificationStream("text", "conv-1", true, 1),
    )

    await waitFor(() => {
      expect(result.current.report).not.toBeNull()
    })

    const report = result.current.report!
    expect(report.conversation_id).toBe("conv-1")
    expect(report.skipped).toBe(false)
    expect(report.claims).toHaveLength(2)
    expect(report.claims[0].status).toBe("verified")
    expect(report.claims[1].status).toBe("unverified")
    expect(report.summary.total).toBe(2)
    expect(report.summary.verified).toBe(1)
    expect(report.summary.unverified).toBe(1)
  })

  // --- SSE keepalive comment handling ---

  it("ignores SSE keepalive comments interleaved with data events", async () => {
    // Simulate a stream with keepalive comments (: keepalive) mixed in
    let aborted = false
    const abort = vi.fn(() => { aborted = true })

    const lines = [
      ": keepalive\n",
      `data: ${JSON.stringify(HAPPY_EVENTS[0])}\n`,
      "\n",
      ": keepalive\n",
      `data: ${JSON.stringify(HAPPY_EVENTS[1])}\n`,
      "\n",
      `data: ${JSON.stringify(HAPPY_EVENTS[2])}\n`,
      "\n",
      ": keepalive\n",
      `data: ${JSON.stringify(HAPPY_EVENTS[3])}\n`,
      "\n",
      `data: ${JSON.stringify(HAPPY_EVENTS[4])}\n`,
      "\n",
      `data: ${JSON.stringify(HAPPY_EVENTS[5])}\n`,
      "\n",
    ]

    const body = new ReadableStream<Uint8Array>({
      async start(controller) {
        for (const line of lines) {
          if (aborted) break
          controller.enqueue(new TextEncoder().encode(line))
        }
        controller.close()
      },
    })

    const response = Promise.resolve({
      ok: true,
      status: 200,
      body,
    } as unknown as Response)

    mockStreamFn.mockReturnValue({ response, abort })

    const { result } = renderHook(() =>
      useVerificationStream("Test response with keepalives", "conv-ka-1", true, 1),
    )

    await waitFor(() => {
      expect(result.current.phase).toBe("done")
    })

    // Should complete successfully despite keepalive comments
    expect(result.current.claims).toHaveLength(2)
    expect(result.current.summary?.verified).toBe(1)
    expect(result.current.summary?.total).toBe(2)
  })
})
