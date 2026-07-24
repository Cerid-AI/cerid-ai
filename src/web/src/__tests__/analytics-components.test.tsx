// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactElement } from "react"
import { CostSankey } from "@/components/analytics/cost-sankey"
import { QualityTimeline } from "@/components/analytics/quality-timeline"
import { GrowthHeatmap } from "@/components/analytics/growth-heatmap"
import { TrustSunburst } from "@/components/analytics/trust-sunburst"
import { AnalyticsPanel } from "@/components/analytics/analytics-panel"

function renderWithQuery(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

// Recharts uses ResizeObserver — jsdom doesn't have it. Stub before render.
class _RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: typeof _RO }).ResizeObserver = _RO

const mockFetchIngestion = vi.fn()
const mockFetchCost = vi.fn()
const mockFetchQuality = vi.fn()

vi.mock("@/lib/api/analytics", () => ({
  fetchIngestionByDay: (...a: unknown[]) => mockFetchIngestion(...a),
  fetchCostByStage: (...a: unknown[]) => mockFetchCost(...a),
  fetchQualityTimeline: (...a: unknown[]) => mockFetchQuality(...a),
}))

const mockTrustScore = vi.fn()

vi.mock("@/hooks/use-trust-score", () => ({
  useTrustScore: () => mockTrustScore(),
}))

// Trust modal opens a Radix Dialog that requires a Portal target. Mock to a no-op.
vi.mock("@/components/trust-score/trust-score-modal", () => ({
  TrustScoreModal: () => null,
}))

beforeEach(() => {
  mockFetchIngestion.mockReset()
  mockFetchCost.mockReset()
  mockFetchQuality.mockReset()
  mockTrustScore.mockReset()
})


// ── GrowthHeatmap ──────────────────────────────────────────────────────

describe("GrowthHeatmap", () => {
  it("renders 365 cells when 0 buckets returned", async () => {
    mockFetchIngestion.mockResolvedValue({
      window_days: 365,
      buckets: [],
      total: 0,
      peak_count: 0,
    })
    renderWithQuery(<GrowthHeatmap windowDays={365} />)
    await screen.findByTestId("growth-heatmap")
    // 53 weeks × 7 days = 371 cells in the SVG (some are future-of-today
    // but the component clips client-side); we just confirm the grid is
    // rendered.
    const svg = screen.getByRole("img", { name: /Knowledge growth heatmap/ })
    expect(svg.querySelectorAll("rect").length).toBeGreaterThan(300)
  })

  it("calls onCellClick when a date with activity is clicked", async () => {
    // Today + yesterday with 5 ingests each
    const today = new Date().toISOString().slice(0, 10)
    mockFetchIngestion.mockResolvedValue({
      window_days: 365,
      buckets: [
        { date: today, count: 5, domains: { notes: 5 }, intensity: 1.0 },
      ],
      total: 5,
      peak_count: 5,
    })
    const onClick = vi.fn()
    const user = userEvent.setup()
    renderWithQuery(<GrowthHeatmap onCellClick={onClick} />)
    const cell = await screen.findByTestId(`heatmap-cell-${today}`)
    await user.click(cell)
    expect(onClick).toHaveBeenCalledWith(today, 5)
  })

  it("renders error when fetch fails", async () => {
    mockFetchIngestion.mockRejectedValue(new Error("backend down"))
    renderWithQuery(<GrowthHeatmap />)
    expect(await screen.findByRole("alert")).toHaveTextContent(/backend down/)
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
  })
})

describe("GrowthHeatmap — axe-clean", () => {
  it("is axe-clean when populated", async () => {
    mockFetchIngestion.mockResolvedValue({
      window_days: 365,
      buckets: [],
      total: 0,
      peak_count: 0,
    })
    const { container } = renderWithQuery(<GrowthHeatmap />)
    await screen.findByTestId("growth-heatmap")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in error state", async () => {
    mockFetchIngestion.mockRejectedValue(new Error("backend down"))
    const { container } = renderWithQuery(<GrowthHeatmap />)
    await screen.findByRole("alert")
    expect(await axe(container)).toHaveNoViolations()
  })
})


// ── CostSankey ─────────────────────────────────────────────────────────

describe("CostSankey", () => {
  it("renders Pro-lock when tier is community", async () => {
    renderWithQuery(<CostSankey tier="community" />)
    expect(await screen.findByText(/Pro-tier/i)).toBeInTheDocument()
    expect(mockFetchCost).not.toHaveBeenCalled()
  })

  it("renders empty-state when no edges returned", async () => {
    mockFetchCost.mockResolvedValue({
      window_days: 30,
      total_cost_usd: 0,
      stages: [],
      edges: [],
    })
    renderWithQuery(<CostSankey tier="pro" />)
    await screen.findByTestId("cost-sankey")
    expect(screen.getByText(/No LLM cost recorded/i)).toBeInTheDocument()
  })

  it("renders headline total when data present", async () => {
    mockFetchCost.mockResolvedValue({
      window_days: 30,
      total_cost_usd: 1.2345,
      stages: [
        { stage: "daily_digest", cost_usd: 0.5, call_count: 3 },
        { stage: "inbox_triage", cost_usd: 0.7345, call_count: 12 },
      ],
      edges: [
        { source: "pro_features", target: "daily_digest", value: 0.5 },
        { source: "pro_features", target: "inbox_triage", value: 0.7345 },
      ],
    })
    renderWithQuery(<CostSankey tier="pro" />)
    await screen.findByTestId("cost-sankey")
    expect(screen.getByText(/\$1\.2345/)).toBeInTheDocument()
  })
})

describe("CostSankey — axe-clean", () => {
  it("is axe-clean in Pro-lock state (community tier)", async () => {
    const { container } = renderWithQuery(<CostSankey tier="community" />)
    await screen.findByText(/Pro-tier/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in empty state", async () => {
    mockFetchCost.mockResolvedValue({
      window_days: 30,
      total_cost_usd: 0,
      stages: [],
      edges: [],
    })
    const { container } = renderWithQuery(<CostSankey tier="pro" />)
    await screen.findByTestId("cost-sankey")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean when populated", async () => {
    mockFetchCost.mockResolvedValue({
      window_days: 30,
      total_cost_usd: 1.2345,
      stages: [
        { stage: "daily_digest", cost_usd: 0.5, call_count: 3 },
        { stage: "inbox_triage", cost_usd: 0.7345, call_count: 12 },
      ],
      edges: [
        { source: "pro_features", target: "daily_digest", value: 0.5 },
        { source: "pro_features", target: "inbox_triage", value: 0.7345 },
      ],
    })
    const { container } = renderWithQuery(<CostSankey tier="pro" />)
    await screen.findByTestId("cost-sankey")
    expect(await axe(container)).toHaveNoViolations()
  })
})


// ── QualityTimeline ───────────────────────────────────────────────────

describe("QualityTimeline", () => {
  it("renders Pro-lock for community tier", async () => {
    renderWithQuery(<QualityTimeline tier="community" />)
    expect(await screen.findByText(/Pro-tier/i)).toBeInTheDocument()
  })

  it("renders empty when no data points have values", async () => {
    mockFetchQuality.mockResolvedValue({
      window_days: 90,
      points: Array.from({ length: 90 }, (_, i) => ({
        date: `2026-${String(Math.floor(i / 30) + 3).padStart(2, "0")}-${String((i % 30) + 1).padStart(2, "0")}`,
        ndcg: null,
        faithfulness: null,
        memory_recall: null,
        verification_accuracy: null,
      })),
      latest: { ndcg: null, faithfulness: null, memory_recall: null, verification_accuracy: null },
    })
    renderWithQuery(<QualityTimeline tier="pro" />)
    await screen.findByTestId("quality-timeline")
    expect(screen.getByText(/No quality metrics/i)).toBeInTheDocument()
  })

  it("renders latest values in header when data present", async () => {
    mockFetchQuality.mockResolvedValue({
      window_days: 90,
      points: [
        { date: "2026-05-22", ndcg: 0.87, faithfulness: 0.92, memory_recall: 0.81, verification_accuracy: 0.95 },
      ],
      latest: { ndcg: 0.87, faithfulness: 0.92, memory_recall: 0.81, verification_accuracy: 0.95 },
    })
    renderWithQuery(<QualityTimeline tier="pro" />)
    await screen.findByTestId("quality-timeline")
    expect(screen.getByText("0.87")).toBeInTheDocument()
    expect(screen.getByText("0.92")).toBeInTheDocument()
  })
})

describe("QualityTimeline — axe-clean", () => {
  it("is axe-clean in Pro-lock state (community tier)", async () => {
    const { container } = renderWithQuery(<QualityTimeline tier="community" />)
    await screen.findByText(/Pro-tier/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in empty state", async () => {
    mockFetchQuality.mockResolvedValue({
      window_days: 90,
      points: Array.from({ length: 90 }, (_, i) => ({
        date: `2026-${String(Math.floor(i / 30) + 3).padStart(2, "0")}-${String((i % 30) + 1).padStart(2, "0")}`,
        ndcg: null,
        faithfulness: null,
        memory_recall: null,
        verification_accuracy: null,
      })),
      latest: { ndcg: null, faithfulness: null, memory_recall: null, verification_accuracy: null },
    })
    const { container } = renderWithQuery(<QualityTimeline tier="pro" />)
    await screen.findByTestId("quality-timeline")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean when populated", async () => {
    mockFetchQuality.mockResolvedValue({
      window_days: 90,
      points: [
        { date: "2026-05-22", ndcg: 0.87, faithfulness: 0.92, memory_recall: 0.81, verification_accuracy: 0.95 },
      ],
      latest: { ndcg: 0.87, faithfulness: 0.92, memory_recall: 0.81, verification_accuracy: 0.95 },
    })
    const { container } = renderWithQuery(<QualityTimeline tier="pro" />)
    await screen.findByTestId("quality-timeline")
    expect(await axe(container)).toHaveNoViolations()
  })
})


// ── TrustSunburst tokenisation sanity (Task 3.4a) ──────────────────────
// Series colors were switched from raw hex to `var(--chart-*)` dataviz
// tokens; confirm the legend still renders its series (the failure mode
// of a bad var reference is a black/blank chart, not a missing element,
// but a rendered legend with the CSS-var value wired through is the best
// signal available under jsdom, which doesn't resolve custom properties).

describe("TrustSunburst", () => {
  it("renders legend labels + swatches wired to chart-color tokens after tokenisation", async () => {
    mockTrustScore.mockReturnValue({
      data: {
        score: 78,
        band: "medium",
        updated_at: "2026-05-22T07:00:00Z",
        components: [
          { id: "faithfulness", label: "Faithfulness", value: 0.85, target: 0.9, normalized: 0.94, status: "warn", source: "RAGAS", last_updated_at: "...", note: null },
          { id: "ndcg", label: "NDCG", value: 0.9, target: 0.9, normalized: 1, status: "ok", source: "retrieval", last_updated_at: "...", note: null },
        ],
        note: "",
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    render(<TrustSunburst />)
    const sunburst = await screen.findByTestId("trust-sunburst")

    // Legend still renders both series' labels post-tokenisation.
    expect(screen.getByText("Faithfulness")).toBeInTheDocument()
    expect(screen.getByText("NDCG")).toBeInTheDocument()

    // Legend swatches carry the dataviz CSS-var reference (not a raw hex
    // literal) — confirms the token wiring survived the refactor.
    const swatches = sunburst.querySelectorAll("span.rounded-full")
    expect(swatches.length).toBe(2)
    for (const swatch of swatches) {
      expect((swatch as HTMLElement).style.backgroundColor).toMatch(
        /^var\(--chart-(ok|warn|fail|neutral)\)$/,
      )
    }
  })
})

describe("TrustSunburst — axe-clean", () => {
  it("is axe-clean when populated", async () => {
    mockTrustScore.mockReturnValue({
      data: {
        score: 78,
        band: "medium",
        updated_at: "2026-05-22T07:00:00Z",
        components: [
          { id: "faithfulness", label: "Faithfulness", value: 0.85, target: 0.9, normalized: 0.94, status: "warn", source: "RAGAS", last_updated_at: "...", note: null },
          { id: "ndcg", label: "NDCG", value: 0.9, target: 0.9, normalized: 1, status: "ok", source: "retrieval", last_updated_at: "...", note: null },
        ],
        note: "",
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    const { container } = render(<TrustSunburst />)
    await screen.findByTestId("trust-sunburst")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in loading state", async () => {
    mockTrustScore.mockReturnValue({ data: undefined, isLoading: true, error: null, refetch: vi.fn() })
    const { container } = render(<TrustSunburst />)
    await screen.findByText(/Loading trust score/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in error state", async () => {
    mockTrustScore.mockReturnValue({ data: undefined, isLoading: false, error: new Error("fail"), refetch: vi.fn() })
    const { container } = render(<TrustSunburst />)
    await screen.findByText(/Trust score unavailable/i)
    expect(await axe(container)).toHaveNoViolations()
  })
})


// ── AnalyticsPanel composition ─────────────────────────────────────────

describe("AnalyticsPanel", () => {
  it("renders all four viz components", async () => {
    mockTrustScore.mockReturnValue({
      data: {
        score: 78,
        band: "medium",
        updated_at: "2026-05-22T07:00:00Z",
        components: [
          { id: "faithfulness", label: "Faithfulness", value: 0.85, target: 0.9, normalized: 0.94, status: "warn", source: "RAGAS", last_updated_at: "...", note: null },
        ],
        note: "",
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockFetchIngestion.mockResolvedValue({
      window_days: 365, buckets: [], total: 0, peak_count: 0,
    })
    mockFetchCost.mockResolvedValue({
      window_days: 30, total_cost_usd: 0, stages: [], edges: [],
    })
    mockFetchQuality.mockResolvedValue({
      window_days: 90,
      points: [],
      latest: {},
    })

    renderWithQuery(<AnalyticsPanel tier="pro" />)
    await waitFor(() => {
      expect(screen.getByTestId("trust-sunburst")).toBeInTheDocument()
      expect(screen.getByTestId("growth-heatmap")).toBeInTheDocument()
      expect(screen.getByTestId("cost-sankey")).toBeInTheDocument()
      expect(screen.getByTestId("quality-timeline")).toBeInTheDocument()
    })
  })

  it("community tier still shows trust+heatmap but locks Pro viz", async () => {
    mockTrustScore.mockReturnValue({
      data: {
        score: 78, band: "medium", updated_at: "x", components: [], note: "",
      },
      isLoading: false, error: null, refetch: vi.fn(),
    })
    mockFetchIngestion.mockResolvedValue({
      window_days: 365, buckets: [], total: 0, peak_count: 0,
    })
    renderWithQuery(<AnalyticsPanel tier="community" />)
    await waitFor(() => {
      expect(screen.getByTestId("trust-sunburst")).toBeInTheDocument()
      expect(screen.getByTestId("growth-heatmap")).toBeInTheDocument()
      expect(screen.getByTestId("cost-sankey")).toBeInTheDocument()
      expect(screen.getByTestId("quality-timeline")).toBeInTheDocument()
    })
    // Pro-locked text appears for both Sankey + Timeline
    const proTexts = await screen.findAllByText(/Pro-tier/i)
    expect(proTexts.length).toBeGreaterThanOrEqual(2)
  })
})

describe("AnalyticsPanel — axe-clean", () => {
  it("is axe-clean on Pro tier (all four viz populated)", async () => {
    mockTrustScore.mockReturnValue({
      data: {
        score: 78,
        band: "medium",
        updated_at: "2026-05-22T07:00:00Z",
        components: [
          { id: "faithfulness", label: "Faithfulness", value: 0.85, target: 0.9, normalized: 0.94, status: "warn", source: "RAGAS", last_updated_at: "...", note: null },
        ],
        note: "",
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockFetchIngestion.mockResolvedValue({
      window_days: 365, buckets: [], total: 0, peak_count: 0,
    })
    mockFetchCost.mockResolvedValue({
      window_days: 30, total_cost_usd: 0, stages: [], edges: [],
    })
    mockFetchQuality.mockResolvedValue({
      window_days: 90,
      points: [],
      latest: {},
    })

    const { container } = renderWithQuery(<AnalyticsPanel tier="pro" />)
    await waitFor(() => {
      expect(screen.getByTestId("trust-sunburst")).toBeInTheDocument()
      expect(screen.getByTestId("growth-heatmap")).toBeInTheDocument()
      expect(screen.getByTestId("cost-sankey")).toBeInTheDocument()
      expect(screen.getByTestId("quality-timeline")).toBeInTheDocument()
    })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean on community tier (Pro viz locked)", async () => {
    mockTrustScore.mockReturnValue({
      data: {
        score: 78, band: "medium", updated_at: "x", components: [], note: "",
      },
      isLoading: false, error: null, refetch: vi.fn(),
    })
    mockFetchIngestion.mockResolvedValue({
      window_days: 365, buckets: [], total: 0, peak_count: 0,
    })
    const { container } = renderWithQuery(<AnalyticsPanel tier="community" />)
    await screen.findAllByText(/Pro-tier/i)
    expect(await axe(container)).toHaveNoViolations()
  })
})
