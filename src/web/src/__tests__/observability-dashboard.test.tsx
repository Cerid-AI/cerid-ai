// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import type { AggregatedMetricsResponse } from "@/lib/types"

const { defaultMetricsResponse } = vi.hoisted(() => ({
  defaultMetricsResponse: {
    metrics: {
      query_latency_ms: { count: 50, min: 100, max: 2000, avg: 450, p50: 400, p95: 1200, p99: 1800 },
      llm_cost_usd: { count: 50, min: 0.001, max: 0.05, avg: 0.01, p50: 0.008, p95: 0.04, p99: 0.048 },
      cache_hit_rate: { count: 100, min: 0, max: 1, avg: 0.65, p50: 0.7, p95: 0.95, p99: 0.99 },
      verification_accuracy: { count: 30, min: 0.5, max: 1, avg: 0.85, p50: 0.88, p95: 0.98, p99: 0.99 },
      queries_per_minute: { count: 120, min: 1, max: 10, avg: 5, p50: 4, p95: 9, p99: 9.8 },
      retrieval_ndcg: { count: 20, min: 0.3, max: 0.95, avg: 0.72, p50: 0.75, p95: 0.92, p99: 0.94 },
    },
    window_minutes: 60,
    timestamp: "2026-07-07T00:00:00.000Z",
  },
}))

// Real-world payloads sometimes omit `metrics` (drives the empty state) even
// though the type declares it required — the component already tolerates
// this via optional chaining (`metricsData?.metrics`).
const emptyMetricsResponse = { window_minutes: 60 } as AggregatedMetricsResponse

vi.mock("@/lib/api", () => ({
  fetchHealthStatus: vi.fn().mockResolvedValue({
    status: "healthy",
    services: { chromadb: "connected", redis: "connected", neo4j: "connected" },
    degradation_tier: "full",
    can_retrieve: true,
    can_verify: true,
    can_generate: true,
    pipeline_providers: {
      claim_extraction: "ollama",
      query_decomposition: "ollama",
      topic_extraction: "ollama",
      memory_resolution: "ollama",
      verification_simple: "ollama",
      verification_complex: "bifrost",
      reranking: "ollama",
      chat_generation: "bifrost",
    },
  }),
  fetchObservabilityMetrics: vi.fn().mockResolvedValue(defaultMetricsResponse),
  fetchObservabilityHealthScore: vi.fn().mockResolvedValue({
    score: 85,
    grade: "B",
    components: {},
    window_minutes: 60,
  }),
}))

import { ObservabilityDashboard } from "@/components/monitoring/observability-dashboard"
import { fetchObservabilityMetrics } from "@/lib/api"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.mocked(fetchObservabilityMetrics).mockResolvedValue(defaultMetricsResponse)
})

describe("ObservabilityDashboard", () => {
  it("renders Observability heading", () => {
    render(<ObservabilityDashboard />, { wrapper })
    expect(screen.getByText("Observability")).toBeInTheDocument()
  })

  it("renders metric card titles", async () => {
    render(<ObservabilityDashboard />, { wrapper })
    expect(await screen.findByText("Query Latency (p50)")).toBeInTheDocument()
    expect(screen.getByText("LLM Cost")).toBeInTheDocument()
    expect(screen.getByText("Cache Hit Rate")).toBeInTheDocument()
  })

  it("renders time window buttons", () => {
    render(<ObservabilityDashboard />, { wrapper })
    expect(screen.getByText("1h")).toBeInTheDocument()
    expect(screen.getByText("24h")).toBeInTheDocument()
    expect(screen.getByText("7d")).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Metrics section — honest states (Task 3.3)
// ---------------------------------------------------------------------------

describe("ObservabilityDashboard — metrics section states", () => {
  it("error: shows destructive Alert with Retry on metrics fetch failure, and retry re-fetches", async () => {
    vi.mocked(fetchObservabilityMetrics).mockRejectedValue(new Error("boom"))
    render(<ObservabilityDashboard />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText(/Metrics backend unreachable/i)).toBeInTheDocument()
    })
    const retryButton = screen.getByRole("button", { name: /retry/i })
    expect(retryButton).toBeInTheDocument()

    const callsBeforeRetry = vi.mocked(fetchObservabilityMetrics).mock.calls.length
    fireEvent.click(retryButton)

    await waitFor(() => {
      expect(fetchObservabilityMetrics).toHaveBeenCalledTimes(callsBeforeRetry + 1)
    })
  })

  it("error: does not masquerade as the empty state", async () => {
    vi.mocked(fetchObservabilityMetrics).mockRejectedValue(new Error("boom"))
    render(<ObservabilityDashboard />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText(/Metrics backend unreachable/i)).toBeInTheDocument()
    })
    expect(screen.queryByText(/No metrics data available yet/i)).not.toBeInTheDocument()
  })

  it("empty: shows the empty state when metrics data has no metrics key", async () => {
    vi.mocked(fetchObservabilityMetrics).mockResolvedValue(emptyMetricsResponse)
    render(<ObservabilityDashboard />, { wrapper })

    expect(await screen.findByText(/No metrics data available yet/i)).toBeInTheDocument()
  })

  it("loading: renders Skeleton placeholders, not hand-rolled animate-pulse divs", () => {
    vi.mocked(fetchObservabilityMetrics).mockReturnValue(new Promise(() => {}))
    const { container } = render(<ObservabilityDashboard />, { wrapper })

    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0)
  })
})

// ---------------------------------------------------------------------------
// axe-clean across all four states
// ---------------------------------------------------------------------------

describe("ObservabilityDashboard — axe-clean", () => {
  it("is axe-clean in loading state", async () => {
    vi.mocked(fetchObservabilityMetrics).mockReturnValue(new Promise(() => {}))
    const { container } = render(<ObservabilityDashboard />, { wrapper })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in empty state", async () => {
    vi.mocked(fetchObservabilityMetrics).mockResolvedValue(emptyMetricsResponse)
    const { container } = render(<ObservabilityDashboard />, { wrapper })
    await screen.findByText(/No metrics data available yet/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in error state", async () => {
    vi.mocked(fetchObservabilityMetrics).mockRejectedValue(new Error("boom"))
    const { container } = render(<ObservabilityDashboard />, { wrapper })
    await waitFor(() => screen.getByText(/Metrics backend unreachable/i))
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in success state", async () => {
    const { container } = render(<ObservabilityDashboard />, { wrapper })
    await screen.findByText("Query Latency (p50)")
    expect(await axe(container)).toHaveNoViolations()
  })
})
