// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Task 1.2b — "Show isolated (N)" toggle wired to include_isolated param.
//
// Verifies:
//  1. fetchGraphMap appends &include_isolated=true to the URL when the flag
//     is true, and omits it when false (URL cache-stable default).
//  2. fetchEmbeddings3D appends &include_isolated=true when includeIsolated is true.
//  3. fetchNeighborhood appends &include_isolated=true when the option is set.
//  4. useGraphMap with includeIsolated=true produces a distinct query key
//     so toggling causes a refetch with the new param.
//  5. Response types carry isolated_count.
//
// No WebGL/R3F/sigma — tests operate purely at the API client + hook layer.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type React from "react"
import { fetchGraphMap } from "@/lib/api/graph-map"
import { fetchEmbeddings3D } from "@/lib/api/embeddings-3d"
import { fetchNeighborhood } from "@/lib/api/graph"
import { useGraphMap } from "@/components/subjects/constellation/map/use-graph-map"

// ---------------------------------------------------------------------------
// Mock the underlying fetch so we can inspect URLs without a live server
// ---------------------------------------------------------------------------

const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

function mockOkResponse(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as Response)
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeGraphMapResponse(isolatedCount = 42) {
  return {
    count: 10,
    entities: [],
    links: [],
    communities: [],
    silhouette: null,
    computed_at: null,
    cached: false,
    isolated_count: isolatedCount,
  }
}

function makeNeighborhoodResponse(isolatedCount = 17) {
  return {
    focal_entity: "entity:test",
    nodes: [],
    edges: [],
    truncated: false,
    cached: false,
    isolated_count: isolatedCount,
  }
}

function makeEmbeddings3DResponse(isolatedCount = 5) {
  return {
    count: 0,
    entities: [],
    links: [],
    cached: false,
    computed_at: null,
    isolated_count: isolatedCount,
  }
}

// ---------------------------------------------------------------------------
// Test wrapper
// ---------------------------------------------------------------------------

function createWrapper(): React.FC<{ children: React.ReactNode }> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "QueryWrapper"
  return Wrapper
}

// ---------------------------------------------------------------------------
// 1. fetchGraphMap URL — include_isolated param
// ---------------------------------------------------------------------------

describe("fetchGraphMap — include_isolated URL param", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockImplementation(() => mockOkResponse(makeGraphMapResponse()))
  })

  it("omits include_isolated when includeIsolated is false (URL cache-stable)", async () => {
    await fetchGraphMap("force", false, undefined)
    const calledUrl = String(mockFetch.mock.calls[0]?.[0])
    expect(calledUrl).not.toContain("include_isolated")
  })

  it("omits include_isolated when includeIsolated is omitted", async () => {
    await fetchGraphMap("force")
    const calledUrl = String(mockFetch.mock.calls[0]?.[0])
    expect(calledUrl).not.toContain("include_isolated")
  })

  it("appends include_isolated=true when includeIsolated is true", async () => {
    await fetchGraphMap("force", true, undefined)
    const calledUrl = String(mockFetch.mock.calls[0]?.[0])
    expect(calledUrl).toContain("include_isolated=true")
  })

  it("response carries isolated_count field", async () => {
    mockFetch.mockImplementation(() => mockOkResponse(makeGraphMapResponse(99)))
    const result = await fetchGraphMap("force", false, undefined)
    expect(result.isolated_count).toBe(99)
  })
})

// ---------------------------------------------------------------------------
// 2. fetchEmbeddings3D URL — include_isolated param
// ---------------------------------------------------------------------------

describe("fetchEmbeddings3D — include_isolated URL param", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockImplementation(() => mockOkResponse(makeEmbeddings3DResponse()))
  })

  it("omits include_isolated when option not set", async () => {
    await fetchEmbeddings3D({})
    const calledUrl = String(mockFetch.mock.calls[0]?.[0])
    expect(calledUrl).not.toContain("include_isolated")
  })

  it("appends include_isolated=true when includeIsolated is true", async () => {
    await fetchEmbeddings3D({ includeIsolated: true })
    const calledUrl = String(mockFetch.mock.calls[0]?.[0])
    expect(calledUrl).toContain("include_isolated=true")
  })

  it("response carries isolated_count field", async () => {
    mockFetch.mockImplementation(() => mockOkResponse(makeEmbeddings3DResponse(7)))
    const result = await fetchEmbeddings3D({})
    expect(result.isolated_count).toBe(7)
  })
})

// ---------------------------------------------------------------------------
// 3. fetchNeighborhood URL — include_isolated param
// ---------------------------------------------------------------------------

describe("fetchNeighborhood — include_isolated URL param", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockImplementation(() => mockOkResponse(makeNeighborhoodResponse()))
  })

  it("omits include_isolated by default", async () => {
    await fetchNeighborhood("entity:test", 2, undefined, {})
    const calledUrl = String(mockFetch.mock.calls[0]?.[0])
    expect(calledUrl).not.toContain("include_isolated")
  })

  it("appends include_isolated=true when includeIsolated is true", async () => {
    await fetchNeighborhood("entity:test", 2, undefined, { includeIsolated: true })
    const calledUrl = String(mockFetch.mock.calls[0]?.[0])
    expect(calledUrl).toContain("include_isolated=true")
  })

  it("response carries isolated_count field", async () => {
    mockFetch.mockImplementation(() => mockOkResponse(makeNeighborhoodResponse(23)))
    const result = await fetchNeighborhood("entity:test", 2, undefined, {})
    expect(result.isolated_count).toBe(23)
  })
})

// ---------------------------------------------------------------------------
// 4. useGraphMap hook — includeIsolated changes query key
// ---------------------------------------------------------------------------

describe("useGraphMap — includeIsolated query key isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockImplementation(() => mockOkResponse(makeGraphMapResponse(42)))
  })

  it("returns isolated_count from data", async () => {
    mockFetch.mockImplementation(() => mockOkResponse(makeGraphMapResponse(2395)))
    const wrapper = createWrapper()
    const { result } = renderHook(() => useGraphMap(undefined, false), { wrapper })
    await waitFor(() => {
      expect(result.current.data?.isolated_count).toBe(2395)
    })
  })

  it("toggling includeIsolated triggers refetch with include_isolated=true param", async () => {
    const wrapper = createWrapper()
    const { result, rerender } = renderHook(
      ({ includeIsolated }: { includeIsolated: boolean }) =>
        useGraphMap(undefined, includeIsolated),
      { wrapper, initialProps: { includeIsolated: false } },
    )

    await waitFor(() => {
      expect(result.current.data).toBeDefined()
    })

    const firstCallCount = mockFetch.mock.calls.length
    expect(firstCallCount).toBeGreaterThan(0)

    // First call should NOT have include_isolated
    const firstUrl = String(mockFetch.mock.calls[0]?.[0])
    expect(firstUrl).not.toContain("include_isolated")

    // Toggle to true — different query key should trigger a new fetch
    rerender({ includeIsolated: true })

    await waitFor(() => {
      expect(mockFetch.mock.calls.length).toBeGreaterThan(firstCallCount)
    })

    const newCalls = mockFetch.mock.calls.slice(firstCallCount)
    const hasIsolatedParam = newCalls.some((call) =>
      String(call[0]).includes("include_isolated=true")
    )
    expect(hasIsolatedParam).toBe(true)
  })
})
