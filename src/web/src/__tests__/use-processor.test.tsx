// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"

// ---------------------------------------------------------------------------
// Mock the API layer — prevent real network calls
// ---------------------------------------------------------------------------

vi.mock("@/lib/api/processor", () => ({
  fetchProcessorStatus: vi.fn(),
  fetchProcessorRecent: vi.fn(),
  pauseProcessor: vi.fn(),
  resumeProcessor: vi.fn(),
}))

import {
  fetchProcessorStatus,
  fetchProcessorRecent,
  pauseProcessor,
  resumeProcessor,
} from "@/lib/api/processor"

const mockFetchStatus = fetchProcessorStatus as ReturnType<typeof vi.fn>
const mockFetchRecent = fetchProcessorRecent as ReturnType<typeof vi.fn>
const mockPause = pauseProcessor as ReturnType<typeof vi.fn>
const mockResume = resumeProcessor as ReturnType<typeof vi.fn>

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

import type { ProcessorStatus, JobRecord } from "@/lib/types/processor"

const statusFixture: ProcessorStatus = {
  queue_sizes: { high: 2, medium: 1, low: 0 },
  paused: false,
  jobs_completed_24h: 8,
  cost_usd_7d: 0.12,
  throttled_ticks_1h: 1,
}

function makeJob(id: string): JobRecord {
  return {
    id,
    job_type: "wiki.refresh_entity",
    state: "completed",
    priority: "medium",
    payload: {},
    enqueued_at: new Date(Date.now() - 120_000).toISOString(),
    retry_count: 0,
    started_at: new Date(Date.now() - 90_000).toISOString(),
    completed_at: new Date(Date.now() - 60_000).toISOString(),
    estimated_tokens_in: 800,
    estimated_tokens_out: 150,
    actual_tokens_in: 790,
    actual_tokens_out: 143,
    requires_llm: true,
    model: "claude-3-haiku",
    error_message: null,
  }
}

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function createWrapper(queryClient?: QueryClient) {
  const qc =
    queryClient ??
    new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0, staleTime: 0 },
        mutations: { retry: false },
      },
    })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// useProcessorStatus
// ---------------------------------------------------------------------------

describe("useProcessorStatus", () => {
  it("returns data when fetch resolves", async () => {
    mockFetchStatus.mockResolvedValue(statusFixture)
    const { useProcessorStatus } = await import("@/hooks/use-processor")

    const { result } = renderHook(() => useProcessorStatus(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
      expect(result.current.data).toEqual(statusFixture)
      expect(result.current.isError).toBe(false)
    })
  })

  it("surfaces error when fetch rejects", async () => {
    mockFetchStatus.mockRejectedValue(new Error("network error"))
    const { useProcessorStatus } = await import("@/hooks/use-processor")

    const { result } = renderHook(() => useProcessorStatus(), {
      wrapper: createWrapper(
        new QueryClient({
          defaultOptions: {
            queries: { retry: 0, gcTime: 0, staleTime: 0 },
          },
        }),
      ),
    })

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    }, { timeout: 3000 })
  })

  it("is initially in loading state", async () => {
    mockFetchStatus.mockReturnValue(new Promise(() => {})) // never resolves
    const { useProcessorStatus } = await import("@/hooks/use-processor")

    const { result } = renderHook(() => useProcessorStatus(), {
      wrapper: createWrapper(),
    })

    expect(result.current.isLoading).toBe(true)
    expect(result.current.data).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// useProcessorRecent
// ---------------------------------------------------------------------------

describe("useProcessorRecent", () => {
  it("returns job list when fetch resolves", async () => {
    const jobs = [makeJob("j1"), makeJob("j2")]
    mockFetchRecent.mockResolvedValue(jobs)
    const { useProcessorRecent } = await import("@/hooks/use-processor")

    const { result } = renderHook(() => useProcessorRecent(20), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
      expect(result.current.data).toHaveLength(2)
    })
    expect(mockFetchRecent).toHaveBeenCalledWith(20)
  })

  it("surfaces error on rejection", async () => {
    mockFetchRecent.mockRejectedValue(new Error("fetch failed"))
    const { useProcessorRecent } = await import("@/hooks/use-processor")

    const { result } = renderHook(() => useProcessorRecent(20), {
      wrapper: createWrapper(
        new QueryClient({
          defaultOptions: { queries: { retry: 0, gcTime: 0, staleTime: 0 } },
        }),
      ),
    })

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    }, { timeout: 3000 })
  })
})

// ---------------------------------------------------------------------------
// useProcessorMutations — pause
// ---------------------------------------------------------------------------

describe("useProcessorMutations — pause", () => {
  it("calls pauseProcessor and invalidates status query on success", async () => {
    mockPause.mockResolvedValue({ paused: true })
    mockFetchStatus.mockResolvedValue(statusFixture)

    const qc = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0, staleTime: 0 },
        mutations: { retry: false },
      },
    })
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries")

    const { useProcessorMutations } = await import("@/hooks/use-processor")

    const { result } = renderHook(() => useProcessorMutations(), {
      wrapper: createWrapper(qc),
    })

    await result.current.pause()

    expect(mockPause).toHaveBeenCalledOnce()
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: ["processor", "status"] }),
      )
    })
  })

  it("sets isPending while mutation is in flight", async () => {
    let resolve: (v: { paused: boolean }) => void = () => {}
    mockPause.mockReturnValue(new Promise((r) => { resolve = r }))
    const { useProcessorMutations } = await import("@/hooks/use-processor")

    const { result } = renderHook(() => useProcessorMutations(), {
      wrapper: createWrapper(),
    })

    expect(result.current.isPending).toBe(false)
    void result.current.pause()

    await waitFor(() => {
      expect(result.current.isPending).toBe(true)
    })

    resolve({ paused: true })

    await waitFor(() => {
      expect(result.current.isPending).toBe(false)
    })
  })
})

// ---------------------------------------------------------------------------
// useProcessorMutations — resume
// ---------------------------------------------------------------------------

describe("useProcessorMutations — resume", () => {
  it("calls resumeProcessor and invalidates status query on success", async () => {
    mockResume.mockResolvedValue({ paused: false })

    const qc = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0, staleTime: 0 },
        mutations: { retry: false },
      },
    })
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries")

    const { useProcessorMutations } = await import("@/hooks/use-processor")

    const { result } = renderHook(() => useProcessorMutations(), {
      wrapper: createWrapper(qc),
    })

    await result.current.resume()

    expect(mockResume).toHaveBeenCalledOnce()
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: ["processor", "status"] }),
      )
    })
  })
})
