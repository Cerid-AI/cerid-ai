// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { KnowledgeStatsHero } from "@/components/sources/knowledge-stats-hero"

// Mock the API module — the hero is a pure consumer of these.
vi.mock("@/lib/api/knowledge-stats", () => ({
  fetchKnowledgeStats: vi.fn().mockResolvedValue({
    nodes: { artifacts: 1247, entities: 4892, memories: 312, sources: 12 },
    edges: {
      mentions: 23104, relates_to: 8712, wikilinks: 1893,
      from_source: 1247, has_contradiction: 23,
    },
    chunks: 18472,
    diversity: { source_kinds: 7, domains: 9 },
    growth: {
      artifacts_24h: 12, artifacts_7d: 312,
      first_artifact_at: "2026-04-13T00:00:00Z", corpus_age_days: 41,
    },
    captured_at: "2026-05-24T00:00:00Z",
  }),
  fetchKnowledgeStatsHistory: vi.fn().mockResolvedValue({
    days: 7,
    snapshots: [
      { date: "2026-05-17", nodes: { artifacts: 1200, entities: 4700, memories: 290, sources: 11 }, edges: { mentions: 22500, relates_to: 8500, wikilinks: 1850, from_source: 1200, has_contradiction: 22 }, chunks: 18000, diversity: { source_kinds: 6, domains: 8 }, growth: { artifacts_24h: 8, artifacts_7d: 280, first_artifact_at: null, corpus_age_days: 34 }, captured_at: "2026-05-17T00:00:00Z" },
      { date: "2026-05-20", nodes: { artifacts: 1220, entities: 4800, memories: 300, sources: 11 }, edges: { mentions: 22800, relates_to: 8600, wikilinks: 1870, from_source: 1220, has_contradiction: 22 }, chunks: 18200, diversity: { source_kinds: 6, domains: 8 }, growth: { artifacts_24h: 10, artifacts_7d: 295, first_artifact_at: null, corpus_age_days: 37 }, captured_at: "2026-05-20T00:00:00Z" },
      { date: "2026-05-23", nodes: { artifacts: 1240, entities: 4880, memories: 310, sources: 12 }, edges: { mentions: 23050, relates_to: 8700, wikilinks: 1890, from_source: 1240, has_contradiction: 23 }, chunks: 18400, diversity: { source_kinds: 7, domains: 9 }, growth: { artifacts_24h: 11, artifacts_7d: 305, first_artifact_at: null, corpus_age_days: 40 }, captured_at: "2026-05-23T00:00:00Z" },
    ],
  }),
}))

function renderHero(props = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <KnowledgeStatsHero {...props} />
    </QueryClientProvider>,
  )
}

describe("KnowledgeStatsHero", () => {
  afterEach(() => vi.clearAllMocks())

  it("renders all five metrics with formatted values", async () => {
    renderHero()
    await waitFor(() => {
      expect(screen.getByText("1,247")).toBeInTheDocument()  // artifacts
      expect(screen.getByText("18,472")).toBeInTheDocument() // chunks
      expect(screen.getByText("4,892")).toBeInTheDocument()  // entities
      expect(screen.getByText("7")).toBeInTheDocument()      // diversity
    })
  })

  it("computes total edges as the sum of all edge kinds", async () => {
    renderHero()
    // 23104 + 8712 + 1893 + 1247 + 23 = 34,979
    await waitFor(() => {
      expect(screen.getByText("34,979")).toBeInTheDocument()
    })
  })

  it("shows the diversity bar with 22 segments", async () => {
    const { container } = renderHero()
    await waitFor(() => {
      // 22 total segments (11 Core + 11 Pro) per kinds.py
      const segments = container.querySelectorAll(".flex.h-1\\.5 > div")
      expect(segments.length).toBe(22)
    })
  })

  it("renders the 7d / 30d window toggle", async () => {
    renderHero()
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /7-day sparkline window/i }))
        .toBeInTheDocument()
      expect(screen.getByRole("button", { name: /30-day sparkline window/i }))
        .toBeInTheDocument()
    })
  })

  it("fires onArtifactsClick when the artifacts card is clicked", async () => {
    const onArtifactsClick = vi.fn()
    renderHero({ onArtifactsClick })
    await waitFor(() => screen.getByText("1,247"))
    const user = userEvent.setup()
    await user.click(screen.getByText("1,247"))
    expect(onArtifactsClick).toHaveBeenCalledOnce()
  })

  it("renders sparklines under each metric", async () => {
    const { container } = renderHero()
    await waitFor(() => {
      // 5 metric cards each get a sparkline SVG
      const sparklines = container.querySelectorAll("svg.cerid-sparkline-pulse")
      expect(sparklines.length).toBeGreaterThanOrEqual(5)
    })
  })
})
