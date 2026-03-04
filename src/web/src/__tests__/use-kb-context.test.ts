// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"
import { createElement, type ReactNode } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { KBInjectionProvider } from "@/contexts/kb-injection-context"
import { useKBContext } from "@/hooks/use-kb-context"
import type { KBQueryResult } from "@/lib/types"

// Mock the API module
vi.mock("@/lib/api", () => ({
  queryKB: vi.fn(),
}))

import { queryKB } from "@/lib/api"

const mockQueryKB = queryKB as ReturnType<typeof vi.fn>

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(KBInjectionProvider, null, children),
    )
  }
}

const makeResult = (overrides: Partial<KBQueryResult> = {}): KBQueryResult => ({
  content: "Test content",
  relevance: 0.85,
  artifact_id: "art-1",
  filename: "test.py",
  domain: "coding",
  chunk_index: 0,
  collection: "kb_coding",
  ingested_at: "2026-01-15T10:00:00Z",
  ...overrides,
})

const mockResponse = {
  results: [
    makeResult({ artifact_id: "a1", filename: "file1.py", tags: ["python", "fastapi"] }),
    makeResult({ artifact_id: "a2", filename: "file2.py", tags: ["python", "django"] }),
    makeResult({ artifact_id: "a3", filename: "budget.xlsx", domain: "finance", tags: ["budget"] }),
  ],
  confidence: 0.8,
  total_results: 3,
  execution_time_ms: 150,
}

beforeEach(() => {
  vi.clearAllMocks()
  mockQueryKB.mockResolvedValue(mockResponse)
})

describe("useKBContext", () => {
  it("returns initial empty state for short queries", () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext(""), { wrapper })
    expect(result.current.results).toEqual([])
    expect(result.current.confidence).toBe(0)
    expect(result.current.isLoading).toBe(false)
    expect(result.current.hasQueried).toBe(false)
  })

  it("queries KB when message is long enough", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext("how does auth work"), { wrapper })

    await waitFor(() => {
      expect(result.current.results.length).toBe(3)
    })
    expect(result.current.confidence).toBe(0.8)
    expect(result.current.totalResults).toBe(3)
    expect(result.current.executionTime).toBe(150)
    expect(result.current.hasQueried).toBe(true)
    expect(mockQueryKB).toHaveBeenCalledWith("how does auth work", undefined, 10, undefined)
  })

  it("does not query for very short messages", () => {
    const wrapper = createWrapper()
    renderHook(() => useKBContext("ab"), { wrapper })
    expect(mockQueryKB).not.toHaveBeenCalled()
  })

  it("toggles domain filter", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext("test query"), { wrapper })

    act(() => { result.current.toggleDomain("coding") })
    expect(result.current.activeDomains.has("coding")).toBe(true)

    act(() => { result.current.toggleDomain("coding") })
    expect(result.current.activeDomains.has("coding")).toBe(false)
  })

  it("toggles tag filter", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext("test query"), { wrapper })

    act(() => { result.current.toggleTag("python") })
    expect(result.current.activeTags).toEqual(["python"])

    act(() => { result.current.toggleTag("python") })
    expect(result.current.activeTags).toEqual([])
  })

  it("filters results by active tags", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext("test query"), { wrapper })

    await waitFor(() => {
      expect(result.current.results.length).toBe(3)
    })

    // Filter by "python" tag — should include a1 and a2 (both have "python" tag)
    act(() => { result.current.toggleTag("python") })
    expect(result.current.results.length).toBe(2)
    expect(result.current.results.every((r) => r.tags?.includes("python"))).toBe(true)

    // Add "fastapi" filter — only a1 has both "python" and "fastapi"
    act(() => { result.current.toggleTag("fastapi") })
    expect(result.current.results.length).toBe(1)
    expect(result.current.results[0].artifact_id).toBe("a1")
  })

  it("manages manual search", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext("original query"), { wrapper })

    // Set manual query
    act(() => { result.current.setManualQuery("manual search term") })
    expect(result.current.manualQuery).toBe("manual search term")

    // Execute manual search (replaces the auto query)
    act(() => { result.current.executeManualSearch() })
    await waitFor(() => {
      expect(mockQueryKB).toHaveBeenCalledWith("manual search term", undefined, 10, undefined)
    })
  })

  it("clears manual search", () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext("test query"), { wrapper })

    act(() => { result.current.setManualQuery("search term") })
    expect(result.current.manualQuery).toBe("search term")

    act(() => { result.current.clearManualSearch() })
    expect(result.current.manualQuery).toBe("")
  })

  it("manages selected artifact ID", () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext("test query"), { wrapper })

    expect(result.current.selectedArtifactId).toBeNull()

    act(() => { result.current.setSelectedArtifactId("art-1") })
    expect(result.current.selectedArtifactId).toBe("art-1")

    act(() => { result.current.setSelectedArtifactId(null) })
    expect(result.current.selectedArtifactId).toBeNull()
  })

  it("manages injected context", () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext("test query"), { wrapper })

    expect(result.current.injectedContext).toEqual([])

    const item = makeResult({ artifact_id: "inject-1" })
    act(() => { result.current.injectResult(item) })
    expect(result.current.injectedContext).toHaveLength(1)
    expect(result.current.injectedContext[0].artifact_id).toBe("inject-1")

    // Duplicate inject is ignored
    act(() => { result.current.injectResult(item) })
    expect(result.current.injectedContext).toHaveLength(1)

    // Remove
    act(() => { result.current.removeInjected("inject-1") })
    expect(result.current.injectedContext).toHaveLength(0)
  })

  it("clears all injected context", () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext("test query"), { wrapper })

    act(() => {
      result.current.injectResult(makeResult({ artifact_id: "a1" }))
      result.current.injectResult(makeResult({ artifact_id: "a2", chunk_index: 1 }))
    })
    expect(result.current.injectedContext).toHaveLength(2)

    act(() => { result.current.clearInjected() })
    expect(result.current.injectedContext).toHaveLength(0)
  })

  it("returns error when query fails", async () => {
    mockQueryKB.mockRejectedValue(new Error("Network error"))
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext("test query"), { wrapper })

    await waitFor(() => {
      expect(result.current.error).toBeTruthy()
    })
    expect(result.current.error?.message).toBe("Network error")
  })

  it("does not execute manual search when query is too short", () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useKBContext(""), { wrapper })

    act(() => { result.current.setManualQuery("ab") })
    act(() => { result.current.executeManualSearch() })
    // "ab" is not > 2 characters after trim, so no query is set
    expect(mockQueryKB).not.toHaveBeenCalled()
  })
})
