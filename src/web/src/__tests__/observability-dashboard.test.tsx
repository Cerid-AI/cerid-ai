// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

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
  fetchObservabilityMetrics: vi.fn().mockResolvedValue({
    metrics: {
      query_latency_ms: { count: 50, min: 100, max: 2000, avg: 450, p50: 400, p95: 1200, sum: 22500 },
      llm_cost_usd: { count: 50, min: 0.001, max: 0.05, avg: 0.01, p50: 0.008, p95: 0.04, sum: 0.5 },
      cache_hit_rate: { count: 100, min: 0, max: 1, avg: 0.65, p50: 0.7, p95: 0.95, sum: 65 },
      verification_accuracy: { count: 30, min: 0.5, max: 1, avg: 0.85, p50: 0.88, p95: 0.98, sum: 25.5 },
      queries_per_minute: { count: 120, min: 1, max: 10, avg: 5, p50: 4, p95: 9, sum: 600 },
      retrieval_ndcg: { count: 20, min: 0.3, max: 0.95, avg: 0.72, p50: 0.75, p95: 0.92, sum: 14.4 },
    },
    window_minutes: 60,
  }),
  fetchObservabilityHealthScore: vi.fn().mockResolvedValue({
    score: 85,
    grade: "B",
    components: {},
    window_minutes: 60,
  }),
}))

import { ObservabilityDashboard } from "@/components/monitoring/observability-dashboard"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.restoreAllMocks()
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
