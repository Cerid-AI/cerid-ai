// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import type { AuditResponse } from "@/lib/types"

vi.mock("@/lib/api", () => ({
  fetchAudit: vi.fn(),
}))

// The report sub-components are chart-heavy (recharts) and have their own
// coverage; stub them to stable nodes so these tests exercise AuditPane's
// state machine (loading / error / empty / success) rather than chart
// internals. Matches the sub-component-stubbing pattern in knowledge-pane.test.
vi.mock("@/components/audit/activity-chart", () => ({
  ActivityChart: () => <div data-testid="activity-chart">Activity</div>,
}))
vi.mock("@/components/audit/cost-breakdown", () => ({
  CostBreakdown: () => <div data-testid="cost-breakdown">Costs</div>,
}))
vi.mock("@/components/audit/query-stats", () => ({
  QueryStats: () => <div data-testid="query-stats">Queries</div>,
}))
vi.mock("@/components/audit/ingestion-stats", () => ({
  IngestionStats: () => <div data-testid="ingestion-stats">Ingestion</div>,
}))
vi.mock("@/components/audit/recent-failures", () => ({
  RecentFailures: () => <div data-testid="recent-failures">Failures</div>,
}))
vi.mock("@/components/audit/conversation-stats", () => ({
  ConversationStats: () => <div data-testid="conversation-stats">Conversations</div>,
}))
vi.mock("@/components/audit/accuracy-dashboard", () => ({
  AccuracyDashboard: () => <div data-testid="accuracy-dashboard">Verification</div>,
}))

import { fetchAudit } from "@/lib/api"
import { AuditPane } from "@/components/audit/audit-pane"

const mockFetchAudit = fetchAudit as ReturnType<typeof vi.fn>

const REPORT_LABELS = [
  "Activity",
  "Ingestion",
  "Costs",
  "Queries",
  "Conversations",
  "Verification",
]

function makeAudit(): AuditResponse {
  return {
    timestamp: new Date().toISOString(),
    reports_generated: ["activity", "ingestion", "costs", "queries", "conversations", "verification"],
    activity: {
      time_window_hours: 24,
      total_events: 12,
      event_breakdown: { query: 8, ingest: 4 },
      domain_breakdown: { research: 12 },
      hourly_timeline: {},
      recent_failures: [],
      scanned_entries: 12,
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

// ---------------------------------------------------------------------------
// D.2: four-state matrix
//
// The audit pane is a report-selection surface, so its states map to:
//   - loading: Loader2 spinner ("Loading audit data...")
//   - error  : PaneError ("Failed to load analytics") + Retry
//   - empty  : "Select at least one report to display" (no report enabled)
//   - success: the report sub-components render
// ---------------------------------------------------------------------------

describe("AuditPane — four-state matrix (D.2)", () => {
  it("idle/loading: shows the loading spinner while fetching", () => {
    mockFetchAudit.mockReturnValue(new Promise(() => {})) // never resolves
    render(<AuditPane />, { wrapper: makeWrapper() })
    expect(screen.getByText(/Loading audit data/i)).toBeInTheDocument()
  })

  it("loaded: renders report content after data arrives", async () => {
    render(<AuditPane />, { wrapper: makeWrapper() })
    expect(await screen.findByTestId("activity-chart")).toBeInTheDocument()
    expect(screen.getByTestId("cost-breakdown")).toBeInTheDocument()
  })

  it("empty: shows the selection prompt when no report is enabled", async () => {
    render(<AuditPane />, { wrapper: makeWrapper() })
    await screen.findByTestId("activity-chart")
    // Toggle every report off — the pane becomes a "nothing selected" surface.
    for (const label of REPORT_LABELS) {
      fireEvent.click(screen.getByRole("button", { name: label }))
    }
    expect(
      await screen.findByText(/Select at least one report to display/i),
    ).toBeInTheDocument()
  })

  it("error: shows destructive Alert with Retry button on fetch failure", async () => {
    mockFetchAudit.mockRejectedValue(new Error("Connection refused"))
    render(<AuditPane />, { wrapper: makeWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/Failed to load analytics/i)).toBeInTheDocument()
    })
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Smoke
// ---------------------------------------------------------------------------

describe("AuditPane", () => {
  it("renders the Analytics header", () => {
    render(<AuditPane />, { wrapper: makeWrapper() })
    expect(screen.getByRole("heading", { name: "Analytics", level: 2 })).toBeInTheDocument()
  })

  it("calls fetchAudit on mount", async () => {
    render(<AuditPane />, { wrapper: makeWrapper() })
    await waitFor(() => expect(mockFetchAudit).toHaveBeenCalled())
  })
})

// ---------------------------------------------------------------------------
// D.3: axe-clean
// ---------------------------------------------------------------------------

describe("AuditPane — axe-clean (D.3)", () => {
  it("is axe-clean (D.3) in loading state", async () => {
    mockFetchAudit.mockReturnValue(new Promise(() => {}))
    const { container } = render(<AuditPane />, { wrapper: makeWrapper() })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in empty state", async () => {
    const { container } = render(<AuditPane />, { wrapper: makeWrapper() })
    await screen.findByTestId("activity-chart")
    for (const label of REPORT_LABELS) {
      fireEvent.click(screen.getByRole("button", { name: label }))
    }
    await screen.findByText(/Select at least one report to display/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in populated state", async () => {
    const { container } = render(<AuditPane />, { wrapper: makeWrapper() })
    await screen.findByTestId("activity-chart")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in error state", async () => {
    mockFetchAudit.mockRejectedValue(new Error("fail"))
    const { container } = render(<AuditPane />, { wrapper: makeWrapper() })
    await waitFor(() => screen.getByText(/Failed to load analytics/i))
    expect(await axe(container)).toHaveNoViolations()
  })
})
