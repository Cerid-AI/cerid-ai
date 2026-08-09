// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useExternalAPIs, useExternalAPIToggle } from "@/hooks/use-external-apis"
import type { ExternalAPISummary } from "@/lib/types/external-apis"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/api/external-apis", () => ({
  fetchExternalAPIs: vi.fn(),
  fetchExternalAPIHealth: vi.fn(),
  toggleExternalAPI: vi.fn(),
}))

import { fetchExternalAPIs, toggleExternalAPI } from "@/lib/api/external-apis"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ADAPTERS: ExternalAPISummary[] = [
  { slug: "wikipedia",    display_name: "Wikipedia",     enabled: true,  requires_key: false, key_configured: true },
  { slug: "wikidata",     display_name: "Wikidata",      enabled: true,  requires_key: false, key_configured: true },
  { slug: "openlibrary",  display_name: "Open Library",  enabled: false, requires_key: false, key_configured: true },
  { slug: "stackexchange",display_name: "Stack Exchange",enabled: true,  requires_key: false, key_configured: true },
  { slug: "arxiv",        display_name: "arXiv",         enabled: true,  requires_key: false, key_configured: true },
  { slug: "github",       display_name: "GitHub",        enabled: false, requires_key: true,  key_configured: false },
  { slug: "packages",     display_name: "Packages",      enabled: true,  requires_key: false, key_configured: true },
  { slug: "osm",          display_name: "OpenStreetMap", enabled: true,  requires_key: false, key_configured: true },
]

// ---------------------------------------------------------------------------
// Per-test QueryClient wrapper (fresh instance per test avoids cache leaks)
// ---------------------------------------------------------------------------

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  }
  return { wrapper: Wrapper, qc }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks()
})

describe("useExternalAPIs", () => {
  it("returns adapters after successful fetch", async () => {
    vi.mocked(fetchExternalAPIs).mockResolvedValue(ADAPTERS)
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useExternalAPIs(), { wrapper })

    expect(result.current.isLoading).toBe(true)
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.data).toHaveLength(8)
    expect(result.current.data?.[0].slug).toBe("wikipedia")
    expect(result.current.isError).toBe(false)
  })

  it("exposes error when fetch fails", async () => {
    vi.mocked(fetchExternalAPIs).mockRejectedValue(new Error("upstream error"))
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useExternalAPIs(), { wrapper })

    // retry: 1 means the hook retries once before settling — allow extra time.
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 5000 })
    expect(result.current.error?.message).toBe("upstream error")
    expect(result.current.data).toBeUndefined()
  })

  it("uses the external-apis query key", async () => {
    vi.mocked(fetchExternalAPIs).mockResolvedValue(ADAPTERS)
    const { wrapper, qc } = makeWrapper()
    const { result } = renderHook(() => useExternalAPIs(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    // Query should be cached under ["external-apis"]
    const cached = qc.getQueryData<ExternalAPISummary[]>(["external-apis"])
    expect(cached).toBeDefined()
    expect(cached).toHaveLength(8)
  })
})

describe("useExternalAPIToggle", () => {
  it("calls toggleExternalAPI with correct args", async () => {
    vi.mocked(fetchExternalAPIs).mockResolvedValue(ADAPTERS)
    vi.mocked(toggleExternalAPI).mockResolvedValue({ ok: true, enabled: false })

    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useExternalAPIToggle(), { wrapper })

    await act(async () => {
      result.current.mutate({ slug: "wikipedia", enabled: false })
    })

    await waitFor(() => expect(result.current.isPending).toBe(false))
    expect(toggleExternalAPI).toHaveBeenCalledWith("wikipedia", false)
  })

  it("invalidates external-apis query on success", async () => {
    vi.mocked(fetchExternalAPIs).mockResolvedValue(ADAPTERS)
    vi.mocked(toggleExternalAPI).mockResolvedValue({ ok: true, enabled: true })

    const { wrapper, qc } = makeWrapper()

    // Spy on invalidateQueries to assert it was called with the right key.
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries")

    const { result } = renderHook(() => useExternalAPIToggle(), { wrapper })

    await act(async () => {
      result.current.mutate({ slug: "openlibrary", enabled: true })
    })

    await waitFor(() => expect(result.current.isPending).toBe(false))

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["external-apis"] }),
    )
  })

  it("exposes mutation error on failure", async () => {
    vi.mocked(toggleExternalAPI).mockRejectedValue(new Error("Redis unavailable"))
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useExternalAPIToggle(), { wrapper })

    await act(async () => {
      result.current.mutate({ slug: "wikipedia", enabled: false })
    })

    await waitFor(() => expect(result.current.isPending).toBe(false))
    expect(result.current.error?.message).toBe("Redis unavailable")
  })
})
