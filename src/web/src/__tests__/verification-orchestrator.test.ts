// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Regression tests for useVerificationOrchestrator — specifically the length
 * gate that decides whether to fire a verification request when a new
 * assistant message completes streaming.
 *
 * Bug #11 (2026-04): the FE gate was MIN_VERIFIABLE_LENGTH=200, which
 * suppressed verification for short-but-verifiable responses such as
 * "The capital of France is Paris." (31 chars).  The backend already
 * accepts responses >=25 chars (HALLUCINATION_MIN_RESPONSE_LENGTH), so the
 * FE gate must match (or be lower than) the BE gate, otherwise the side
 * panel falls through to "No factual claims to verify".
 *
 * These tests mock useVerificationStream so we observe the triggerKey the
 * orchestrator hands to it — that's the exact decision point that was buggy.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ChatMessage, HallucinationReport } from "@/lib/types"
import { MODELS } from "@/lib/types"

// Mock useVerificationStream so we can observe the triggerKey it receives
// and (for the re-save tests) control the phase/report it returns.
const mockStreamHook: Mock = vi.fn()
const defaultStreamReturn = () => ({
  claims: [],
  phase: "idle",
  summary: null,
  loading: false,
  verifiedCount: 0,
  totalClaims: 0,
  extractionMethod: null,
  report: null,
  sessionClaimsChecked: 0,
  sessionEstCost: 0,
  creditError: null,
  activityLog: [],
})
let mockStreamReturn: Record<string, unknown> = defaultStreamReturn()
vi.mock("@/hooks/use-verification-stream", () => ({
  useVerificationStream: (...args: unknown[]) => {
    mockStreamHook(...args)
    return mockStreamReturn
  },
}))

// Mock conversations + KB injection contexts — orchestrator reads callbacks
// off them; save/mark are shared spies so the persistence tests can assert.
const mockSaveVerification: Mock = vi.fn()
const mockMarkVerified: Mock = vi.fn()
vi.mock("@/contexts/conversations-context", () => ({
  useConversationsContext: () => ({
    markVerified: mockMarkVerified,
    clearVerified: vi.fn(),
    saveVerification: mockSaveVerification,
    getVerification: vi.fn(() => null),
    getAllVerificationReports: vi.fn(() => ({})),
  }),
}))

vi.mock("@/contexts/kb-injection-context", () => ({
  useKBInjection: () => ({ injectedContext: [] }),
}))

// Sprint C: saveVerificationReport is no longer called from the hook —
// the backend auto-persists on stream completion. No mock needed here.

import { useVerificationOrchestrator } from "@/hooks/use-verification-orchestrator"

const currentModel = MODELS[0]

function makeMessages(assistantContent: string | null): ChatMessage[] {
  const msgs: ChatMessage[] = [
    { id: "u1", role: "user", content: "What is the capital of France?", timestamp: 1 },
  ]
  if (assistantContent !== null) {
    msgs.push({ id: "a1", role: "assistant", content: assistantContent, timestamp: 2, model: currentModel.id })
  }
  return msgs
}

/** Pull triggerKey (4th positional arg) out of the most recent call. */
function lastTriggerKey(): number {
  const calls = mockStreamHook.mock.calls
  if (calls.length === 0) return -1
  return calls[calls.length - 1][3] as number
}

describe("useVerificationOrchestrator — length gate (Bug #11)", () => {
  beforeEach(() => {
    mockStreamHook.mockClear()
    mockStreamReturn = defaultStreamReturn()
  })

  it("fires verification for a short-but-verifiable response (31 chars) once streaming completes", async () => {
    const shortResponse = "The capital of France is Paris." // 31 chars
    expect(shortResponse.length).toBeGreaterThanOrEqual(25)
    expect(shortResponse.length).toBeLessThan(200)

    // Initial render mirrors the real app flow: user message only (no
    // assistant yet), isStreaming=true.  lastKnownCount initializes to 0.
    const { rerender } = renderHook(
      ({ messages, isStreaming }: { messages: ChatMessage[]; isStreaming: boolean }) =>
        useVerificationOrchestrator({
          activeMessages: messages,
          activeId: "conv-short-1",
          isStreaming,
          hallucinationEnabled: true,
          currentModel,
        }),
      {
        initialProps: {
          messages: makeMessages(null), // user only, no assistant yet
          isStreaming: true,
        },
      },
    )

    // While streaming, triggerKey should be 0 (stream not fired yet).
    expect(lastTriggerKey()).toBe(0)

    // Streaming completes: assistant message appears AND isStreaming flips false.
    rerender({ messages: makeMessages(shortResponse), isStreaming: false })

    // After the length gate passes (>=25 chars) and isStreaming is false,
    // triggerKey MUST increment above 0 so useVerificationStream actually
    // fires a request.  Prior bug: FE gate was 200, so this stayed at 0
    // forever and the side panel rendered "No factual claims to verify".
    // The trigger logic runs in a useEffect, so wait for the state bump
    // + re-render to settle.
    await waitFor(() => {
      expect(lastTriggerKey()).toBeGreaterThan(0)
    })
  })

  it("does NOT fire verification for a trivially short response (<25 chars)", async () => {
    const trivial = "OK." // 3 chars — below both FE and BE gates

    const { rerender } = renderHook(
      ({ messages, isStreaming }: { messages: ChatMessage[]; isStreaming: boolean }) =>
        useVerificationOrchestrator({
          activeMessages: messages,
          activeId: "conv-trivial-1",
          isStreaming,
          hallucinationEnabled: true,
          currentModel,
        }),
      {
        initialProps: {
          messages: makeMessages(null), // user only
          isStreaming: true,
        },
      },
    )

    rerender({ messages: makeMessages(trivial), isStreaming: false })

    // Flush any pending effects — triggerKey must stay 0 because the response
    // is below MIN_VERIFIABLE_LENGTH.  A single microtask is enough since the
    // trigger effect runs synchronously after the post-commit phase.
    await Promise.resolve()
    expect(lastTriggerKey()).toBe(0)
  })

  it("does NOT fire verification on initial open of an existing conversation", () => {
    // Open a conversation that already has an assistant message — this is
    // conversation-switch, not a new message, so no verification trigger.
    const existing = "The capital of France is Paris."
    renderHook(() =>
      useVerificationOrchestrator({
        activeMessages: makeMessages(existing),
        activeId: "conv-existing-1",
        isStreaming: false,
        hallucinationEnabled: true,
        currentModel,
      }),
    )

    // No trigger: lastKnownCount initializes to current count, so no delta.
    expect(lastTriggerKey()).toBe(0)
  })
})

/**
 * Per-message verification scoping — the orchestrator exposes
 * ``selectedVerificationMsgId`` and ``setSelectedVerificationMsgId`` so a user
 * can click a prior assistant reply and swap the verification cards to that
 * message. The feature was shipped in v0.84.0 but had no dedicated tests;
 * these guard the three invariants documented in the hook header.
 */
function makeMultiTurnMessages(): ChatMessage[] {
  return [
    { id: "u1", role: "user", content: "q1", timestamp: 1 },
    { id: "a1", role: "assistant", content: "First response is long enough for verification.", timestamp: 2, model: currentModel.id },
    { id: "u2", role: "user", content: "q2", timestamp: 3 },
    { id: "a2", role: "assistant", content: "Second response is also long enough for verification.", timestamp: 4, model: currentModel.id },
  ]
}

describe("useVerificationOrchestrator — per-message scoping", () => {
  beforeEach(() => {
    mockStreamHook.mockClear()
    mockStreamReturn = defaultStreamReturn()
  })

  it("defaults selectedVerificationMsgId to the latest assistant message", () => {
    const { result } = renderHook(() =>
      useVerificationOrchestrator({
        activeMessages: makeMultiTurnMessages(),
        activeId: "conv-scope-default",
        isStreaming: false,
        hallucinationEnabled: true,
        currentModel,
      }),
    )

    // With no manual selection, the effective id falls through to the latest
    // assistant message so the panel renders current-turn verification by
    // default. Regression would show a stale prior-message id here.
    expect(result.current.lastAssistantMsgId).toBe("a2")
    expect(result.current.selectedVerificationMsgId).toBe("a2")
  })

  it("setSelectedVerificationMsgId swaps the selected message", () => {
    const { result } = renderHook(() =>
      useVerificationOrchestrator({
        activeMessages: makeMultiTurnMessages(),
        activeId: "conv-scope-swap",
        isStreaming: false,
        hallucinationEnabled: true,
        currentModel,
      }),
    )

    expect(result.current.selectedVerificationMsgId).toBe("a2")

    act(() => {
      result.current.setSelectedVerificationMsgId("a1")
    })

    expect(result.current.selectedVerificationMsgId).toBe("a1")
  })

  it("resets to the latest message when a new stream begins", () => {
    const { result, rerender } = renderHook(
      ({ isStreaming }: { isStreaming: boolean }) =>
        useVerificationOrchestrator({
          activeMessages: makeMultiTurnMessages(),
          activeId: "conv-scope-reset",
          isStreaming,
          hallucinationEnabled: true,
          currentModel,
        }),
      { initialProps: { isStreaming: false } },
    )

    // User clicks an older message to inspect its verification.
    act(() => {
      result.current.setSelectedVerificationMsgId("a1")
    })
    expect(result.current.selectedVerificationMsgId).toBe("a1")

    // New response starts streaming — selection resets so the streaming
    // card doesn't appear attached to the wrong (older) bubble. This is
    // the 5th TODO step from the original hook header: "clear streaming
    // verification state when a new user message is sent."
    rerender({ isStreaming: true })
    expect(result.current.selectedVerificationMsgId).toBe("a2")
  })
})

/**
 * Post-summary retry sweep persistence — the backend re-emits claim_verified
 * events and a final summary_update AFTER the first summary, revising the
 * report while phase stays "done". The orchestrator's save guard used to be
 * key-only, so the revised report never replaced the cached/persisted copy.
 */
describe("useVerificationOrchestrator — post-summary report refresh", () => {
  beforeEach(() => {
    mockStreamHook.mockClear()
    mockSaveVerification.mockClear()
    mockMarkVerified.mockClear()
    mockStreamReturn = defaultStreamReturn()
  })

  it("re-persists the report when the retry sweep revises counts, and only then", async () => {
    const messages = makeMessages("The capital of France is Paris.")
    const round1: HallucinationReport = {
      conversation_id: "conv-resave",
      timestamp: "2026-07-13T00:00:00Z",
      skipped: false,
      claims: [
        { claim: "A", status: "verified", similarity: 0.9 },
        { claim: "B", status: "uncertain", similarity: 0, verification_method: "timeout" },
      ],
      summary: { total: 2, verified: 1, unverified: 0, uncertain: 1 },
    }
    mockStreamReturn = { ...defaultStreamReturn(), phase: "done", report: round1 }

    const { rerender } = renderHook(
      ({ tick }: { tick: number }) => {
        void tick // rerender knob only
        return useVerificationOrchestrator({
          activeMessages: messages,
          activeId: "conv-resave",
          isStreaming: false,
          hallucinationEnabled: true,
          currentModel,
        })
      },
      { initialProps: { tick: 0 } },
    )

    await waitFor(() => {
      expect(mockSaveVerification).toHaveBeenCalledTimes(1)
    })
    expect(mockSaveVerification).toHaveBeenLastCalledWith("conv-resave", "a1", round1)

    // Same report content — must NOT save again.
    rerender({ tick: 1 })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(mockSaveVerification).toHaveBeenCalledTimes(1)

    // Round-2 sweep resolves the timed-out claim and refreshes counts.
    const round2: HallucinationReport = {
      ...round1,
      claims: [
        { claim: "A", status: "verified", similarity: 0.9 },
        { claim: "B", status: "verified", similarity: 0.85, verification_method: "web_search" },
      ],
      summary: { total: 2, verified: 2, unverified: 0, uncertain: 0 },
    }
    mockStreamReturn = { ...defaultStreamReturn(), phase: "done", report: round2 }
    rerender({ tick: 2 })

    await waitFor(() => {
      expect(mockSaveVerification).toHaveBeenCalledTimes(2)
    })
    expect(mockSaveVerification).toHaveBeenLastCalledWith("conv-resave", "a1", round2)
  })
})
