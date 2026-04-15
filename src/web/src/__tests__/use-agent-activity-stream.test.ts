// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  __testing__,
  useAgentActivityStream,
} from "@/hooks/use-agent-activity-stream"

// ---------------------------------------------------------------------------
// EventSource mock — capture instances so we can drive onopen/onmessage/onerror
// deterministically from the tests.
// ---------------------------------------------------------------------------

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  readyState = 0
  closed = false

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  close() {
    this.closed = true
  }

  // Helpers used by tests
  emitOpen() {
    this.onopen?.(new Event("open"))
  }
  emitMessage(data: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(data) }))
  }
  emitError() {
    this.onerror?.(new Event("error"))
  }
}

beforeEach(() => {
  MockEventSource.instances = []
  ;(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource =
    MockEventSource
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  // Cleanup for subsequent non-hook tests that don't expect our mock.
  delete (globalThis as unknown as { EventSource?: unknown }).EventSource
})

describe("computeBackoff", () => {
  it("follows 500 * 2^n with a 30s cap", () => {
    expect(__testing__.computeBackoff(0)).toBe(500)
    expect(__testing__.computeBackoff(1)).toBe(1000)
    expect(__testing__.computeBackoff(2)).toBe(2000)
    // 500 * 2^6 = 32_000 → capped at 30_000
    expect(__testing__.computeBackoff(6)).toBe(30_000)
    expect(__testing__.computeBackoff(20)).toBe(30_000)
  })
})

describe("useAgentActivityStream", () => {
  it("opens a connection, surfaces events, and flips status to open", () => {
    const { result } = renderHook(() =>
      useAgentActivityStream({ url: "http://example/stream" }),
    )

    expect(MockEventSource.instances).toHaveLength(1)
    expect(result.current.status).toBe("connecting")

    act(() => {
      MockEventSource.instances[0]!.emitOpen()
    })
    expect(result.current.status).toBe("open")

    act(() => {
      MockEventSource.instances[0]!.emitMessage({
        agent: "QueryAgent",
        message: "hi",
        level: "info",
        timestamp: 1234,
      })
    })
    expect(result.current.entries).toHaveLength(1)
    expect(result.current.entries[0]?.agent).toBe("QueryAgent")
  })

  it("retries with exponential back-off, then gives up after maxRetries", () => {
    const { result } = renderHook(() =>
      useAgentActivityStream({
        url: "http://example/stream",
        maxRetries: 3,
      }),
    )

    // Drive the failure loop: error → wait backoff → reconnect → error …
    // 3 retries means 3 reconnect attempts after the initial failure.
    act(() => {
      MockEventSource.instances[0]!.emitError()
    })
    expect(result.current.status).toBe("retrying")
    expect(result.current.retryCount).toBe(1)

    // First back-off = 500ms → second EventSource created.
    act(() => {
      vi.advanceTimersByTime(500)
    })
    expect(MockEventSource.instances).toHaveLength(2)
    act(() => {
      MockEventSource.instances[1]!.emitError()
    })
    expect(result.current.retryCount).toBe(2)

    // Second back-off = 1000ms.
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(MockEventSource.instances).toHaveLength(3)
    act(() => {
      MockEventSource.instances[2]!.emitError()
    })
    expect(result.current.retryCount).toBe(3)

    // Third back-off = 2000ms.
    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(MockEventSource.instances).toHaveLength(4)
    // Now one more error puts us past maxRetries → unavailable.
    act(() => {
      MockEventSource.instances[3]!.emitError()
    })
    expect(result.current.status).toBe("unavailable")
    expect(result.current.error).toMatch(/reload to retry/i)
  })

  it("resets the retry counter after a successful message", () => {
    const { result } = renderHook(() =>
      useAgentActivityStream({ url: "http://example/stream", maxRetries: 5 }),
    )

    act(() => {
      MockEventSource.instances[0]!.emitError()
    })
    expect(result.current.retryCount).toBe(1)

    act(() => {
      vi.advanceTimersByTime(500)
    })
    const second = MockEventSource.instances[1]!
    act(() => {
      second.emitOpen()
      second.emitMessage({
        agent: "Memory",
        message: "ok",
        level: "info",
        timestamp: 1,
      })
    })
    expect(result.current.retryCount).toBe(0)
    expect(result.current.status).toBe("open")
  })

  it("closes the EventSource on unmount (no leak across tab changes)", () => {
    const { unmount } = renderHook(() =>
      useAgentActivityStream({ url: "http://example/stream" }),
    )
    const es = MockEventSource.instances[0]!
    expect(es.closed).toBe(false)
    unmount()
    expect(es.closed).toBe(true)
  })

  it("tears down when `enabled` flips to false", () => {
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        useAgentActivityStream({ url: "http://example/stream", enabled }),
      { initialProps: { enabled: true } },
    )
    const es = MockEventSource.instances[0]!
    expect(es.closed).toBe(false)

    rerender({ enabled: false })
    expect(es.closed).toBe(true)
    expect(result.current.status).toBe("idle")
  })

  it("reset() re-opens a new connection after abandonment", () => {
    const { result } = renderHook(() =>
      useAgentActivityStream({ url: "http://example/stream", maxRetries: 0 }),
    )
    act(() => {
      MockEventSource.instances[0]!.emitError()
    })
    expect(result.current.status).toBe("unavailable")
    const priorCount = MockEventSource.instances.length

    act(() => {
      result.current.reset()
    })
    expect(MockEventSource.instances.length).toBe(priorCount + 1)
    expect(result.current.status).toBe("connecting")
  })
})
