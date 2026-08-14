// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// SourcesHudTicker consumes the shared per-connector sync state (sf-1):
// during a live sync the "/min" stat must show the window rate from
// /ingestion/sync-state — not the 24h average that reads ~0.0 mid-ingest
// (UX-22) — plus a per-connector syncing/stalled chip with N/M.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

vi.mock("@/lib/api/knowledge-stats", () => ({
  fetchKnowledgeStats: vi.fn(),
}))
vi.mock("@/lib/api/sources", () => ({
  listSources: vi.fn(),
}))
vi.mock("@/lib/api/sync-state", async (importOriginal) => {
  // Mock only the fetch; keep the real anySyncActive/liveRatePerMin logic
  // under test.
  const actual = await importOriginal<typeof import("@/lib/api/sync-state")>()
  return { ...actual, fetchSyncStates: vi.fn() }
})

import { fetchKnowledgeStats } from "@/lib/api/knowledge-stats"
import { listSources } from "@/lib/api/sources"
import { fetchSyncStates, type ConnectorSyncState } from "@/lib/api/sync-state"
import { SourcesHudTicker } from "@/components/sources/sources-hud-ticker"

const mockStats = fetchKnowledgeStats as ReturnType<typeof vi.fn>
const mockSources = listSources as ReturnType<typeof vi.fn>
const mockSyncStates = fetchSyncStates as ReturnType<typeof vi.fn>

function stats(artifacts24h: number) {
  return {
    nodes: { artifacts: 1234, entities: 0, memories: 0, sources: 3 },
    edges: { mentions: 0, relates_to: 0, wikilinks: 0, from_source: 0, has_contradiction: 0 },
    chunks: 0,
    diversity: { source_kinds: 4, domains: 2 },
    growth: { artifacts_24h: artifacts24h, artifacts_7d: 0, first_artifact_at: null, corpus_age_days: 1 },
    captured_at: new Date().toISOString(),
  }
}

function syncState(overrides: Partial<ConnectorSyncState> = {}): ConnectorSyncState {
  return {
    connector: "apple_mail",
    state: "syncing",
    phase: "syncing",
    total: 500,
    scanned: 500,
    posted: 120,
    failed: 0,
    ingested_total: 400,
    deduped_total: 0,
    errored_total: 0,
    window_ingested: 118,
    rate_per_min: 12,
    eta_seconds: 1900,
    window_started_at: new Date().toISOString(),
    last_ingest_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    last_error: null,
    ...overrides,
  }
}

function renderTicker() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <SourcesHudTicker />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockSources.mockResolvedValue([])
})

describe("SourcesHudTicker — shared sync state (UX-22)", () => {
  it("shows the live window rate during an active sync, not the ~0 24h average", async () => {
    mockStats.mockResolvedValue(stats(10)) // 24h average would render 0.0/min
    mockSyncStates.mockResolvedValue([syncState({ rate_per_min: 12 })])
    renderTicker()
    expect(await screen.findByText("12.0")).toBeInTheDocument()
    expect(screen.queryByText("0.0")).not.toBeInTheDocument()
  })

  it("renders a per-connector syncing chip with N/M", async () => {
    mockStats.mockResolvedValue(stats(0))
    mockSyncStates.mockResolvedValue([syncState()])
    renderTicker()
    const chip = await screen.findByTestId("hud-sync-apple_mail")
    expect(chip.textContent).toMatch(/syncing apple mail/)
    expect(chip.textContent).toMatch(/120\/500/)
  })

  it("marks a silent syncing client as stalled", async () => {
    mockStats.mockResolvedValue(stats(0))
    mockSyncStates.mockResolvedValue([syncState({ state: "stalled", rate_per_min: null })])
    renderTicker()
    const chip = await screen.findByTestId("hud-sync-apple_mail")
    expect(chip.textContent).toMatch(/stalled apple mail/)
  })

  it("falls back to the 24h-average rate when no sync is active", async () => {
    mockStats.mockResolvedValue(stats(1440)) // 1440 artifacts/24h = 1.0/min
    mockSyncStates.mockResolvedValue([syncState({ state: "idle", phase: "idle" })])
    renderTicker()
    expect(await screen.findByText("1.0")).toBeInTheDocument()
    expect(screen.queryByTestId("hud-sync-apple_mail")).not.toBeInTheDocument()
  })
})
