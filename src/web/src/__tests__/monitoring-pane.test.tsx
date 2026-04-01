// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

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
}))

import { MonitoringPane } from "@/components/monitoring/monitoring-pane"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("MonitoringPane", () => {
  it("renders Health heading", async () => {
    render(<MonitoringPane />, { wrapper })
    expect(await screen.findByText("Health")).toBeInTheDocument()
  })

  it("renders infrastructure status description", async () => {
    render(<MonitoringPane />, { wrapper })
    expect(await screen.findByText(/Live infrastructure status/)).toBeInTheDocument()
  })

  it("shows loading state initially", () => {
    render(<MonitoringPane />, { wrapper })
    expect(screen.getByText(/Loading system status/)).toBeInTheDocument()
  })
})
