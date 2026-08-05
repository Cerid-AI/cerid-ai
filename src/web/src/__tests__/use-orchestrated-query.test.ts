// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { createElement, type ReactNode } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { KBInjectionProvider } from "@/contexts/kb-injection-context"
import { useOrchestratedQuery } from "@/hooks/use-orchestrated-query"

vi.mock("@/lib/api", () => ({
  queryKBOrchestrated: vi.fn(),
}))

import { queryKBOrchestrated } from "@/lib/api"

const mockOrchestrated = queryKBOrchestrated as ReturnType<typeof vi.fn>

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(KBInjectionProvider, null, children),
    )
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("useOrchestratedQuery — error propagation", () => {
  it("surfaces isError when the backend query fails (no silent empty)", async () => {
    // Regression guard: the queryFn used to catch and return an empty
    // response, leaving `isError` permanently false and the console's
    // Retry UI dead. Failures must now reach react-query.
    mockOrchestrated.mockRejectedValue(new Error("backend down"))
    const { result } = renderHook(
      () => useOrchestratedQuery("a real question", "smart"),
      { wrapper: createWrapper() },
    )

    // Hook config is retry:1 + retryDelay:2000, so settle can take ~2s.
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 4000 })
    expect(result.current.error).toBeInstanceOf(Error)
    expect(result.current.results).toEqual([])
    expect(result.current.hasQueried).toBe(false)
  })

  it("exposes results on success", async () => {
    mockOrchestrated.mockResolvedValue({
      results: [],
      confidence: 0.9,
      total_results: 0,
      execution_time_ms: 12,
      source_breakdown: null,
    })
    const { result } = renderHook(
      () => useOrchestratedQuery("another question", "smart"),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.hasQueried).toBe(true))
    expect(result.current.isError).toBe(false)
    expect(result.current.confidence).toBe(0.9)
  })

  it("CR-010: exposes low ordinal-relevance results without a client-side floor", async () => {
    // Post-rerank relevance is an ordinal cross-encoder sigmoid; the old 0.35
    // client floor re-created the emptied-envelope bug the backend already
    // fixed by flooring on its calibrated scale pre-rerank.
    mockOrchestrated.mockResolvedValue({
      results: [
        { content: "c", relevance: 0.28, artifact_id: "hot", filename: "hot.py", domain: "coding", chunk_index: 0 },
        { content: "c2", relevance: 0.09, artifact_id: "warm", filename: "warm.py", domain: "coding", chunk_index: 0 },
      ],
      confidence: 0.18,
      total_results: 2,
      execution_time_ms: 9,
      source_breakdown: null,
    })
    const { result } = renderHook(
      () => useOrchestratedQuery("indirect-evidence question", "smart"),
      { wrapper: createWrapper() },
    )
    await waitFor(() => expect(result.current.hasQueried).toBe(true))
    expect(result.current.results.map((r) => r.artifact_id)).toEqual(["hot", "warm"])
  })
})
