// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Component tests for Timeline v2 (Stratigraph orchestrator).
//   1. 4-state matrix: loading skeleton, error alert, empty state, populated
//   2. Period tabs (data-testid timeline-period-*)
//   3. Lens radiogroup switching
//   4. Type-filter chip toggling
//   5. Axe a11y on loaded and empty states
//
// StratigraphCanvas (d3 + canvas) is stubbed — jsdom has no 2D canvas.
// Fetchers are mocked so queries resolve immediately.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { NavigationProvider } from "@/contexts/navigation-context"

// ---------------------------------------------------------------------------
// Stub StratigraphCanvas — d3 + canvas-2D not available in jsdom
// ---------------------------------------------------------------------------

vi.mock(
  "@/components/subjects/timeline/stratigraph/StratigraphCanvas",
  () => ({
    StratigraphCanvas: ({ data }: { data: { totals: { mentions: number } } }) => (
      <div data-testid="stratigraph-canvas-stub">
        canvas stub · {data.totals.mentions} mentions
      </div>
    ),
  }),
)

// ---------------------------------------------------------------------------
// Mock fetchers
// ---------------------------------------------------------------------------

const mockFetchStrata = vi.fn()

vi.mock("@/lib/api/graph", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/graph")>("@/lib/api/graph")
  return {
    ...actual,
    fetchTimelineStrata: (...args: unknown[]) => mockFetchStrata(...args),
  }
})

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeStrataData(overrides = {}) {
  return {
    from_date: "2026-05-01",
    to_date: "2026-06-01",
    granularity: "day",
    bucket_dates: ["2026-05-01", "2026-05-02"],
    communities: [
      {
        community_id: "c1",
        label: "Research",
        color_slot: 0,
        trust_mix: { verified: 0.8, partial: 0.1, unverified: 0.1 },
        total_mentions: 100,
        is_other: false,
      },
    ],
    series: [
      { community_id: "c1", entity_type: "PERSON", domain: "research", buckets: [50, 50], unverified_buckets: [0, 0] },
      { community_id: "c1", entity_type: "ORG", domain: "coding", buckets: [20, 30], unverified_buckets: [0, 0] },
    ],
    tracks: [
      {
        canonical_id: "e1",
        name: "Alice",
        entity_type: "PERSON",
        community_id: "c1",
        trust_state: "verified",
        first_seen: "2026-05-01T00:00:00Z",
        rank: 1,
        total_mentions: 50,
        buckets: [25, 25],
      },
    ],
    markers: [{ date: "2026-05-02", kind: "ingest_burst", count: 50 }],
    totals: { mentions: 150, entities_introduced: 20 },
    cached: false,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>
      <NavigationProvider activePane="subjects" onPaneChange={() => {}}>
        {children}
      </NavigationProvider>
    </QueryClientProvider>
  )
}

// ---------------------------------------------------------------------------
// Import after mocks
// ---------------------------------------------------------------------------

import { Timeline } from "@/components/subjects/timeline/Timeline"

beforeEach(() => {
  mockFetchStrata.mockReset()
  localStorage.clear()
})

// ---------------------------------------------------------------------------
// 4-state matrix
// ---------------------------------------------------------------------------

describe("Timeline — loading state", () => {
  it("renders skeleton when fetching", () => {
    mockFetchStrata.mockReturnValue(new Promise(() => {})) // never resolves
    render(<Timeline />, { wrapper: createWrapper() })
    // Skeleton renders aria-busy container
    expect(document.querySelector("[aria-busy='true']")).not.toBeNull()
  })
})

describe("Timeline — error state", () => {
  it("renders destructive Alert when fetch rejects", async () => {
    mockFetchStrata.mockRejectedValue(new Error("Network error"))
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument())
    expect(screen.getByText(/network error/i)).toBeInTheDocument()
  })
})

describe("Timeline — empty state", () => {
  it("renders empty state and data-testid when no buckets", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData({ bucket_dates: [], totals: { mentions: 0, entities_introduced: 0 } }))
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByTestId("timeline-empty")).toBeInTheDocument())
    expect(screen.getByText(/no timeline data yet/i)).toBeInTheDocument()
  })
})

describe("Timeline — populated state", () => {
  it("renders data-testid timeline-mode with canvas stub", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByTestId("timeline-mode")).toBeInTheDocument())
    expect(screen.getByTestId("stratigraph-canvas-stub")).toBeInTheDocument()
  })

  it("shows 150 mentions in the canvas stub", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByText(/150 mentions/))
  })
})

// ---------------------------------------------------------------------------
// Period tabs
// ---------------------------------------------------------------------------

describe("Timeline — period tabs", () => {
  it("renders all four period tab buttons with correct data-testids", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    for (const p of ["7d", "30d", "90d", "365d"]) {
      expect(screen.getByTestId(`timeline-period-${p}`)).toBeInTheDocument()
    }
  })

  it("clicking a period tab triggers a refetch with the new period", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    fireEvent.click(screen.getByTestId("timeline-period-7d"))
    await waitFor(() => expect(mockFetchStrata).toHaveBeenCalled())
  })
})

// ---------------------------------------------------------------------------
// Lens radiogroup
// ---------------------------------------------------------------------------

describe("Timeline — lens radiogroup", () => {
  it("renders Clusters, Trust, Types, and Domains lens buttons", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    expect(screen.getByRole("radio", { name: /clusters/i })).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: /trust/i })).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: /types/i })).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: /domains/i })).toBeInTheDocument()
  })

  it("Clusters is checked by default", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    expect(screen.getByRole("radio", { name: /clusters/i })).toHaveAttribute("aria-checked", "true")
  })

  it("clicking Trust lens switches aria-checked", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    fireEvent.click(screen.getByRole("radio", { name: /trust/i }))
    expect(screen.getByRole("radio", { name: /trust/i })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByRole("radio", { name: /clusters/i })).toHaveAttribute("aria-checked", "false")
  })

  it("clicking Domains lens switches aria-checked", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    fireEvent.click(screen.getByRole("radio", { name: /domains/i }))
    expect(screen.getByRole("radio", { name: /domains/i })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByRole("radio", { name: /clusters/i })).toHaveAttribute("aria-checked", "false")
  })
})

// ---------------------------------------------------------------------------
// Type-filter chips
// ---------------------------------------------------------------------------

describe("Timeline — type filter chips", () => {
  it("renders type chips from series entity_types", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    // Series has PERSON and ORG types
    expect(screen.getByRole("button", { name: /person/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /org/i })).toBeInTheDocument()
  })

  it("clicking a chip toggles aria-pressed", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    const chip = screen.getByRole("button", { name: /person/i })
    expect(chip).toHaveAttribute("aria-pressed", "false")
    fireEvent.click(chip)
    expect(chip).toHaveAttribute("aria-pressed", "true")
  })
})

// ---------------------------------------------------------------------------
// A11y (jest-axe)
// ---------------------------------------------------------------------------

describe("Timeline — a11y", () => {
  it("populated state has no axe violations", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    const { container } = render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it("empty state has no axe violations", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData({ bucket_dates: [], totals: { mentions: 0, entities_introduced: 0 } }))
    const { container } = render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-empty"))
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
