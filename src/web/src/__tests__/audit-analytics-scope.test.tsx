// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// F372 — Settings → Analytics contradicted itself inside one viewport:
// "Knowledge Growth — 1006 artifacts ingested" beside "Total Ingests 4", and
// "Total Cost $0.0000" beside "Total estimated cost: $0.0002".
//
// The period selector at the top of the pane is the source of the confusion.
// core/agents/audit.py forwards `hours` to the activity and verification
// reports only; get_ingestion_stats() takes no hours argument at all and
// estimate_costs() is called without one, so it always reports its own 720h
// default. The pane nonetheless presented both as though the selection
// applied, and passed the *selected* hours into the cost card's monthly
// projection -- dividing a 30-day cost by a 24-hour window and inflating the
// projection thirtyfold at the default selection.
//
// These tests render the real cost and ingestion cards through AuditPane so
// the wiring, not just the card, is under test.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { AuditResponse } from "@/lib/types"

vi.mock("@/lib/api", () => ({
  fetchAudit: vi.fn(),
}))

// Recharts-backed cards have their own coverage and do not render in jsdom.
vi.mock("@/components/audit/activity-chart", () => ({
  ActivityChart: () => <div data-testid="activity-chart">Activity</div>,
}))
vi.mock("@/components/audit/accuracy-dashboard", () => ({
  AccuracyDashboard: () => <div data-testid="accuracy-dashboard">Verification</div>,
}))

import { fetchAudit } from "@/lib/api"
import { AuditPane } from "@/components/audit/audit-pane"

const mockFetchAudit = fetchAudit as ReturnType<typeof vi.fn>

function makeAudit(): AuditResponse {
  return {
    timestamp: new Date().toISOString(),
    reports_generated: ["ingestion", "costs", "conversations"],
    // estimate_costs()'s own default window — never the pane's selection.
    costs: {
      time_window_hours: 720,
      operations: { categorize_smart: 12, rerank: 145 },
      estimated_tokens: { smart: 84_000, rerank: 3_000 },
      estimated_cost_usd: { smart: 7.2, rerank: 0.1 },
    },
    ingestion: {
      total_ingests: 4,
      total_duplicates: 0,
      duplicate_rate: 0,
      recategorizations: 0,
      domain_distribution: { anneal_lessons: 4 },
      file_type_distribution: {},
      avg_chunks_per_file: 0,
    },
    conversations: {
      total_conversations: 4,
      total_turns: 4,
      models: {},
      total_cost_usd: 0.0002,
    },
  }
}

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
  mockFetchAudit.mockResolvedValue(makeAudit())
})

describe("AuditPane — cost window truth (F372)", () => {
  it("projects the month from the window the cost figure covers", async () => {
    render(<AuditPane />, { wrapper: makeWrapper() })
    // $7.30 over 720h → $7.40/month. Dividing by the pane's default 24h
    // selection instead produced $222.04.
    await waitFor(() => expect(screen.getByText("$7.40")).toBeInTheDocument())
    expect(screen.queryByText("$222.04")).not.toBeInTheDocument()
  })

  it("names the window the projection was based on", async () => {
    render(<AuditPane />, { wrapper: makeWrapper() })
    await waitFor(() =>
      expect(screen.getByText(/based on 720h window/)).toBeInTheDocument(),
    )
    expect(screen.queryByText(/based on 24h window/)).not.toBeInTheDocument()
  })

  it("says the cost report ignores the selected period", async () => {
    render(<AuditPane />, { wrapper: makeWrapper() })
    await waitFor(() =>
      expect(screen.getByText(/720h window · not the 24h selection/i)).toBeInTheDocument(),
    )
  })

  it("distinguishes the two dollar figures by how each was derived", async () => {
    render(<AuditPane />, { wrapper: makeWrapper() })
    await waitFor(() =>
      expect(screen.getByText(/from operation counts/i)).toBeInTheDocument(),
    )
    expect(screen.getByText(/from recorded token usage/i)).toBeInTheDocument()
  })
})

describe("AuditPane — ingest scope truth (F372)", () => {
  it("says the ingest count is not scoped to the selected period", async () => {
    render(<AuditPane />, { wrapper: makeWrapper() })
    await waitFor(() =>
      expect(
        screen.getByText(/recent activity log · not the 24h selection/i),
      ).toBeInTheDocument(),
    )
    // Both the ingestion and the cost card carry the disclaimer.
    expect(screen.getAllByText(/not the 24h selection/i).length).toBe(2)
  })

  it("names the ledger the ingest count is read from", async () => {
    render(<AuditPane />, { wrapper: makeWrapper() })
    await waitFor(() =>
      expect(screen.getByText(/recent activity log/i)).toBeInTheDocument(),
    )
    // The corpus-wide "1,006 artifacts ingested" it used to contradict comes
    // from Neo4j over 365 days; this card reads a bounded Redis scan.
    expect(screen.getByText("4")).toBeInTheDocument()
  })
})
