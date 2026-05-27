// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"

vi.mock("@/lib/api", () => ({
  fetchMaintenance: vi.fn().mockResolvedValue({
    health: { neo4j: "healthy", chroma: "healthy", redis: "healthy", bifrost: "healthy" },
    collections: [],
  }),
  fetchIngestLog: vi.fn().mockResolvedValue({ entries: [] }),
  fetchSchedulerStatus: vi.fn().mockResolvedValue({ jobs: [], running: false }),
  fetchDigest: vi.fn().mockResolvedValue({ summary: "", stats: {}, period_hours: 24 }),
  fetchObservabilityMetrics: vi.fn().mockResolvedValue({ metrics: {}, window_minutes: 60 }),
  fetchObservabilityHealthScore: vi.fn().mockResolvedValue({ score: 90, grade: "A", components: {}, window_minutes: 60 }),
  // ObservabilityDashboard (rendered inside MonitoringPane) calls fetchHealthStatus
  // for the Degradation Tier + Pipeline Routing cards. Without this mock the
  // useQuery resolves to undefined and the dashboard renders the "no data" branch
  // silently — production crashes here go unobserved by these tests.
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
}))

import { fetchMaintenance } from "@/lib/api"
import { MonitoringPane } from "@/components/monitoring/monitoring-pane"

const mockFetchMaintenance = fetchMaintenance as ReturnType<typeof vi.fn>

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  // Reset all api mocks to defaults
  mockFetchMaintenance.mockResolvedValue({
    health: { neo4j: "healthy", chroma: "healthy", redis: "healthy", bifrost: "healthy" },
    collections: [],
  })
})

// ---------------------------------------------------------------------------
// D.2: four-state matrix
// ---------------------------------------------------------------------------

describe("MonitoringPane — four-state matrix (D.2)", () => {
  it("idle/loading: shows Skeleton placeholders while fetching", () => {
    mockFetchMaintenance.mockReturnValue(new Promise(() => {})) // never resolves
    const { container } = render(<MonitoringPane />, { wrapper: makeWrapper() })
    // Skeleton components are rendered
    const skeletons = container.querySelectorAll("[class*=skeleton], [class*=animate-pulse], [role=status]")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("loaded: renders Health heading after data arrives", async () => {
    render(<MonitoringPane />, { wrapper: makeWrapper() })
    expect(await screen.findByText("Health")).toBeInTheDocument()
  })

  it("empty: renders content area when collections are empty", async () => {
    mockFetchMaintenance.mockResolvedValue({ health: {}, collections: [] })
    render(<MonitoringPane />, { wrapper: makeWrapper() })
    expect(await screen.findByText(/Live infrastructure status/)).toBeInTheDocument()
  })

  it("error: shows destructive Alert with Retry button on fetch failure", async () => {
    mockFetchMaintenance.mockRejectedValue(new Error("Connection refused"))
    render(<MonitoringPane />, { wrapper: makeWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/Failed to load system status/)).toBeInTheDocument()
    })
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Legacy smoke tests
// ---------------------------------------------------------------------------

describe("MonitoringPane", () => {
  it("renders Health heading", async () => {
    render(<MonitoringPane />, { wrapper: makeWrapper() })
    expect(await screen.findByText("Health")).toBeInTheDocument()
  })

  it("renders infrastructure status description", async () => {
    render(<MonitoringPane />, { wrapper: makeWrapper() })
    expect(await screen.findByText(/Live infrastructure status/)).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// D.3: axe-clean
// ---------------------------------------------------------------------------

describe("MonitoringPane — axe-clean (D.3)", () => {
  it("is axe-clean (D.3) in loading state", async () => {
    mockFetchMaintenance.mockReturnValue(new Promise(() => {}))
    const { container } = render(<MonitoringPane />, { wrapper: makeWrapper() })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in error state", async () => {
    mockFetchMaintenance.mockRejectedValue(new Error("fail"))
    const { container } = render(<MonitoringPane />, { wrapper: makeWrapper() })
    await waitFor(() => screen.getByText(/Failed to load/))
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in populated state", async () => {
    const { container } = render(<MonitoringPane />, { wrapper: makeWrapper() })
    await screen.findByText("Health")
    expect(await axe(container)).toHaveNoViolations()
  })
})
