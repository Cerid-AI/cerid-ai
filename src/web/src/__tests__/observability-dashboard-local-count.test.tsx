// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F364 — Diagnostics counted only the literal string "ollama" as local, so a
 * Quenchforge deployment read "0/8 local" with every stage coloured as a paid
 * cloud call, one screen below a status bar reading "8/8 local" off the same
 * payload.
 *
 * F371 — and the tier badge printed the bare word "Healthy" beside a system
 * health grade of F, reading as a whole-system verdict it cannot make.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const QUENCHFORGE_HEALTH = {
  status: "healthy",
  services: { chromadb: "connected", redis: "connected", neo4j: "connected" },
  degradation_tier: "full",
  can_retrieve: true,
  can_verify: true,
  can_generate: true,
  pipeline_providers: {
    claim_extraction: "quenchforge",
    query_decomposition: "quenchforge",
    topic_extraction: "quenchforge",
    memory_resolution: "quenchforge",
    verification_simple: "quenchforge",
    verification_complex: "quenchforge",
    reranking: "onnx",
    chat_generation: "quenchforge",
  },
  inference_routing: {
    rerank: {
      provider: "quenchforge",
      model: "bge-reranker-v2-m3",
      serving: "onnx",
      degraded: true,
      degraded_detail: "Circuit 'quenchforge-rerank' is open",
      fallback_count: 44,
    },
  },
}

vi.mock("@/lib/api", () => ({
  fetchHealthStatus: vi.fn(),
  fetchObservabilityMetrics: vi.fn(),
  fetchObservabilityHealthScore: vi.fn(),
}))

import { ObservabilityDashboard } from "@/components/monitoring/observability-dashboard"
import {
  fetchHealthStatus,
  fetchObservabilityMetrics,
  fetchObservabilityHealthScore,
} from "@/lib/api"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchHealthStatus).mockResolvedValue(QUENCHFORGE_HEALTH as never)
  vi.mocked(fetchObservabilityMetrics).mockResolvedValue({
    metrics: {
      query_latency_ms: { count: 50, min: 100, max: 20000, avg: 4500, p50: 4000, p95: 12066, p99: 18000 },
    },
    window_minutes: 60,
    timestamp: "2026-09-02T00:00:00.000Z",
  } as never)
  vi.mocked(fetchObservabilityHealthScore).mockResolvedValue({
    score: 8,
    grade: "F",
    factors: { latency: { p95_ms: 12066, score: 0 }, verification: { status: "no_data" } },
    window_minutes: 60,
  } as never)
})

describe("ObservabilityDashboard — pipeline routing truth", () => {
  it("counts quenchforge stages as local, matching the status bar", async () => {
    render(<ObservabilityDashboard />, { wrapper })
    expect(await screen.findByTestId("pipeline-local-count")).toHaveTextContent("8/8 local")
  })

  it("does not colour a quenchforge stage as a cloud call", async () => {
    render(<ObservabilityDashboard />, { wrapper })
    const stage = await screen.findByTestId("pipeline-stage-chat_generation")
    expect(stage.className).not.toMatch(/text-blue-500/)
  })

  it("marks a stage answered by a degraded fallback rather than calling it healthy", async () => {
    render(<ObservabilityDashboard />, { wrapper })
    const stage = await screen.findByTestId("pipeline-stage-reranking")
    expect(stage.className).toMatch(/amber/)
  })

  it("scopes the degradation tier label instead of printing a bare 'Healthy'", async () => {
    render(<ObservabilityDashboard />, { wrapper })
    const badge = await screen.findByTestId("degradation-tier-badge")
    // "Healthy" beside a system-health grade of F is one of three
    // contradictory verdicts on this screen. It names its scope now.
    expect(badge.textContent).not.toBe("Healthy")
    expect(badge).toHaveTextContent(/feature/i)
  })
})
