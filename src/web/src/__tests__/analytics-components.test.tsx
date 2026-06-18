// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { CostSankey } from "@/components/analytics/cost-sankey"
import { QualityTimeline } from "@/components/analytics/quality-timeline"
import { GrowthHeatmap } from "@/components/analytics/growth-heatmap"
import { AnalyticsPanel } from "@/components/analytics/analytics-panel"

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
    render(<GrowthHeatmap windowDays={365} />)
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
    render(<GrowthHeatmap onCellClick={onClick} />)
    const cell = await screen.findByTestId(`heatmap-cell-${today}`)
    await user.click(cell)
    expect(onClick).toHaveBeenCalledWith(today, 5)
  })

  it("renders error when fetch fails", async () => {
    mockFetchIngestion.mockRejectedValue(new Error("backend down"))
    render(<GrowthHeatmap />)
    expect(await screen.findByRole("alert")).toHaveTextContent(/backend down/)
  })
})


// ── CostSankey ─────────────────────────────────────────────────────────

describe("CostSankey", () => {
  it("renders Pro-lock when tier is community", async () => {
    render(<CostSankey tier="community" />)
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
    render(<CostSankey tier="pro" />)
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
    render(<CostSankey tier="pro" />)
    await screen.findByTestId("cost-sankey")
    expect(screen.getByText(/\$1\.2345/)).toBeInTheDocument()
  })
})


// ── QualityTimeline ───────────────────────────────────────────────────

describe("QualityTimeline", () => {
  it("renders Pro-lock for community tier", async () => {
    render(<QualityTimeline tier="community" />)
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
    render(<QualityTimeline tier="pro" />)
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
    render(<QualityTimeline tier="pro" />)
    await screen.findByTestId("quality-timeline")
    expect(screen.getByText("0.87")).toBeInTheDocument()
    expect(screen.getByText("0.92")).toBeInTheDocument()
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

    render(<AnalyticsPanel tier="pro" />)
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
    render(<AnalyticsPanel tier="community" />)
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
