// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for the graph-fetch timeout composition (P1 beta triage:
// slow-backend graph requests must abort at 30s instead of hanging until
// TanStack supersedes them / nginx 499s).

import { describe, it, expect } from "vitest"
import {
  withRequestTimeout,
  composeTimeoutSignal,
  timeoutToError,
  GRAPH_FETCH_TIMEOUT_MS,
} from "../embeddings-3d"

function abortedOnce(signal: AbortSignal): Promise<unknown> {
  return new Promise((resolve) => {
    if (signal.aborted) resolve(signal.reason)
    else signal.addEventListener("abort", () => resolve(signal.reason), { once: true })
  })
}

describe("withRequestTimeout", () => {
  it("stays un-aborted while neither input fires", () => {
    const upstream = new AbortController()
    const composed = withRequestTimeout(upstream.signal, 5_000)
    expect(composed.aborted).toBe(false)
  })

  it("aborts with the upstream reason when the caller's signal fires", async () => {
    const upstream = new AbortController()
    const composed = withRequestTimeout(upstream.signal, 5_000)
    const reason = new DOMException("superseded", "AbortError")
    upstream.abort(reason)
    const seen = await abortedOnce(composed)
    expect(composed.aborted).toBe(true)
    expect((seen as DOMException).name).toBe("AbortError")
  })

  it("aborts with a TimeoutError once the timeout elapses", async () => {
    const composed = withRequestTimeout(undefined, 10)
    const seen = await abortedOnce(composed)
    expect(composed.aborted).toBe(true)
    expect((seen as DOMException).name).toBe("TimeoutError")
  })

  it("defaults to the 30s graph-fetch budget", () => {
    expect(GRAPH_FETCH_TIMEOUT_MS).toBe(30_000)
  })
})

describe("composeTimeoutSignal (manual fallback path)", () => {
  it("aborts with a TimeoutError when the timer fires first", async () => {
    const composed = composeTimeoutSignal(undefined, 10)
    const seen = await abortedOnce(composed)
    expect((seen as DOMException).name).toBe("TimeoutError")
  })

  it("propagates the upstream abort reason and wins the race", async () => {
    const upstream = new AbortController()
    const composed = composeTimeoutSignal(upstream.signal, 5_000)
    upstream.abort(new DOMException("gone", "AbortError"))
    const seen = await abortedOnce(composed)
    expect((seen as DOMException).name).toBe("AbortError")
  })

  it("is immediately aborted when handed an already-aborted signal", () => {
    const upstream = new AbortController()
    upstream.abort(new DOMException("early", "AbortError"))
    const composed = composeTimeoutSignal(upstream.signal, 5_000)
    expect(composed.aborted).toBe(true)
  })
})

describe("timeoutToError", () => {
  it("maps a TimeoutError abort to an actionable Error", () => {
    const mapped = timeoutToError(new DOMException("signal timed out", "TimeoutError"), "Graph map fetch")
    expect(mapped).toBeInstanceOf(Error)
    expect((mapped as Error).message).toMatch(/Graph map fetch timed out/)
  })

  it("passes non-timeout errors through untouched (RQ cancellation stays a cancellation)", () => {
    const abort = new DOMException("aborted", "AbortError")
    expect(timeoutToError(abort, "x")).toBe(abort)
    const plain = new Error("boom")
    expect(timeoutToError(plain, "x")).toBe(plain)
  })
})
