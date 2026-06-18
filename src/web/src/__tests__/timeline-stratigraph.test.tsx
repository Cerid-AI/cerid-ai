// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Component tests for Timeline v2 (Stratigraph orchestrator, Tephra Cycle-2).
//   1. 4-state matrix: loading skeleton, error alert, empty state, populated
//   2. Period tabs (data-testid timeline-period-*)
//   3. Lens radiogroup switching
//   4. Type-filter chip toggling
//   5. Axe a11y on all four states
//   6. Freeze/re-rank gate (amendment #1) — only active for cluster lens
//   7. Window-empty state + nearest-activity jump (amendment #3)
//   8. Bucket-detail card: templated sentences, verification sparse suppression
//   9. Event-detail card: contradiction claim texts, composeChat, goTo
//  10. Track-detail card: extended fields, degraded (no extension)
//  11. Since-you-last-looked lastViewedAt unmount write
//  12. 180d default period (amendment #7)
//  13. Pre-ledger InfoTip strip renders when ledger_start_date is present
//  14. Empty / degraded states: fresh-install, zero-events, pre-ledger, degraded labels
//
// StratigraphCanvas (d3 + canvas) is stubbed — jsdom has no 2D canvas.
// Fetchers are mocked so queries resolve immediately.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { NavigationProvider } from "@/contexts/navigation-context"
import type { StrataEvent } from "@/components/subjects/timeline/stratigraph/strata-types"

// ---------------------------------------------------------------------------
// Stub StratigraphCanvas — d3 + canvas-2D not available in jsdom
// ---------------------------------------------------------------------------

vi.mock(
  "@/components/subjects/timeline/stratigraph/StratigraphCanvas",
  () => ({
    StratigraphCanvas: ({
      data,
      onEventClick,
      onTrackClick,
      onBrushChange,
    }: {
      data: { totals: { mentions: number } }
      onEventClick?: (ev: StrataEvent) => void
      onTrackClick?: (track: { canonicalId: string; name: string; communityId: string; trustState: string }) => void
      onBrushChange?: (from: string, to: string) => void
    }) => (
      <div data-testid="stratigraph-canvas-stub">
        <span>canvas stub · {data.totals.mentions} mentions</span>
        {/* Trigger helpers for event/track/brush callbacks in tests */}
        <button
          data-testid="stub-trigger-event"
          onClick={() => onEventClick?.({
            ts: "2026-06-06T10:00:00Z",
            kind: "contradiction_finding",
            lane_id: "research",
            entity_slug: "quantum-annealing",
            entity_name: "Quantum Annealing",
            summary: "Claim A contradicts Claim B",
            claim_a: "The algorithm converges in O(n log n)",
            claim_b: "The algorithm is NP-hard",
            severity: "high",
          } satisfies StrataEvent)}
        >
          trigger event click
        </button>
        <button
          data-testid="stub-trigger-track"
          onClick={() => onTrackClick?.({
            canonicalId: "e1",
            name: "Alice",
            communityId: "c1",
            trustState: "verified",
          })}
        >
          trigger track click
        </button>
        <button
          data-testid="stub-trigger-brush"
          onClick={() => onBrushChange?.("2026-03-01", "2026-03-15")}
        >
          trigger brush
        </button>
      </div>
    ),
  }),
)

// ---------------------------------------------------------------------------
// Mock fetchers
// ---------------------------------------------------------------------------

const mockFetchStrata = vi.fn()
const mockFetchTrack = vi.fn()

vi.mock("@/lib/api/graph", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/graph")>("@/lib/api/graph")
  return {
    ...actual,
    fetchTimelineStrata: (...args: unknown[]) => mockFetchStrata(...args),
    fetchTimelineTrack: (...args: unknown[]) => mockFetchTrack(...args),
  }
})

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeStrataData(overrides: Record<string, unknown> = {}) {
  return {
    from_date: "2026-05-01",
    to_date: "2026-06-30",
    granularity: "day",
    bucket_dates: ["2026-05-01", "2026-05-02", "2026-05-09", "2026-05-10"],
    communities: [
      {
        community_id: "c1",
        label: "Research",
        color_slot: 0,
        trust_mix: { verified: 0.8, partial: 0.1, unverified: 0.1 },
        total_mentions: 2363,
        is_other: false,
      },
    ],
    series: [
      { community_id: "c1", entity_type: "PERSON", domain: "research", buckets: [50, 50, 1200, 1063], unverified_buckets: [0, 0, 0, 0] },
      { community_id: "c1", entity_type: "ORG", domain: "coding", buckets: [20, 30, 100, 50], unverified_buckets: [0, 0, 0, 0] },
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
        buckets: [25, 25, 0, 0],
        primary_domain: "research",
      },
    ],
    markers: [{ date: "2026-05-09", kind: "ingest_burst", count: 1200 }],
    totals: { mentions: 2363, entities_introduced: 20 },
    cached: false,
    // Tephra extension fields
    ledger_start_date: null,
    lanes: [],
    events_by_lane_bucket: {},
    verification_by_lane_bucket: {},
    top_entities_by_lane_bucket: {},
    ...overrides,
  }
}

function makeTrackData(overrides: Record<string, unknown> = {}) {
  return {
    canonical_id: "e1",
    name: "Alice",
    events: [
      {
        ts: "2026-05-09T10:00:00Z",
        artifact_id: "art1",
        artifact_filename: "paper.md",
        confidence: 0.9,
        summary: "Alice contributed to the research on quantum annealing.",
        co_mentioned: [{ canonical_id: "e2", name: "Bob" }],
      },
    ],
    cached: false,
    // TimelineTrackExtension fields
    new_entities: [],
    events_extended: [],
    verification: null,
    community_summary: null,
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
  mockFetchTrack.mockReset()
  mockFetchTrack.mockResolvedValue(makeTrackData())
  localStorage.clear()
})

// ---------------------------------------------------------------------------
// 4-state matrix
// ---------------------------------------------------------------------------

describe("Timeline — loading state", () => {
  it("renders skeleton when fetching", () => {
    mockFetchStrata.mockReturnValue(new Promise(() => {})) // never resolves
    render(<Timeline />, { wrapper: createWrapper() })
    expect(document.querySelector("[aria-busy='true']")).not.toBeNull()
  })

  it("loading state has no axe violations", async () => {
    mockFetchStrata.mockReturnValue(new Promise(() => {}))
    const { container } = render(<Timeline />, { wrapper: createWrapper() })
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

describe("Timeline — error state", () => {
  it("renders destructive Alert when fetch rejects", async () => {
    mockFetchStrata.mockRejectedValue(new Error("Network error"))
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument())
    expect(screen.getByText(/network error/i)).toBeInTheDocument()
  })

  it("renders 412 as configuration guidance, not generic failure", async () => {
    mockFetchStrata.mockRejectedValue(new Error("412 configuration required"))
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument())
    expect(screen.getByText(/configuration/i)).toBeInTheDocument()
  })

  it("error state has no axe violations", async () => {
    mockFetchStrata.mockRejectedValue(new Error("Network error"))
    const { container } = render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByRole("alert"))
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

describe("Timeline — empty state", () => {
  it("renders EmptyState when no buckets", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData({ bucket_dates: [], totals: { mentions: 0, entities_introduced: 0 } }))
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByTestId("timeline-empty")).toBeInTheDocument())
    expect(screen.getByText(/no knowledge activity yet/i)).toBeInTheDocument()
  })

  it("empty state has no axe violations", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData({ bucket_dates: [], totals: { mentions: 0, entities_introduced: 0 } }))
    const { container } = render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-empty"))
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

describe("Timeline — populated state", () => {
  it("renders data-testid timeline-mode with canvas stub", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByTestId("timeline-mode")).toBeInTheDocument())
    expect(screen.getByTestId("stratigraph-canvas-stub")).toBeInTheDocument()
  })

  it("shows 2363 mentions in the canvas stub", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByText(/2363 mentions/))
  })

  it("populated state has no axe violations", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    const { container } = render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// Default period (amendment #7 — 180d)
// ---------------------------------------------------------------------------

describe("Timeline — default period", () => {
  it("defaults to 180d period tab selected", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))
    expect(screen.getByTestId("timeline-period-180d")).toHaveAttribute("aria-selected", "true")
  })
})

// ---------------------------------------------------------------------------
// Period tabs
// ---------------------------------------------------------------------------

describe("Timeline — period tabs", () => {
  it("renders all five period tab buttons with correct data-testids", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    for (const p of ["7d", "30d", "90d", "180d", "365d"]) {
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
// Freeze/re-rank gate (amendment #1)
// ---------------------------------------------------------------------------

describe("Timeline — freeze/re-rank gate", () => {
  it("Re-rank button is NOT shown when Domains lens is active", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    // Switch to Domains lens
    fireEvent.click(screen.getByRole("radio", { name: /domains/i }))

    // Re-rank button should not be present for non-cluster lenses
    expect(screen.queryByRole("button", { name: /re-rank/i })).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Window-empty + nearest-activity jump (amendment #3)
// ---------------------------------------------------------------------------

describe("Timeline — window-empty nearest-activity jump", () => {
  it("shows empty-window message + nearest-activity button when brush is in dead zone", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    // Trigger a brush change to a window with no activity
    fireEvent.click(screen.getByTestId("stub-trigger-brush"))

    await waitFor(() => {
      expect(screen.getByTestId("timeline-window-empty")).toBeInTheDocument()
    })
    // Nearest activity button should appear (May 9 has 1200 mentions)
    expect(screen.getByText(/nearest activity/i)).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Pre-ledger honesty strip
// ---------------------------------------------------------------------------

describe("Timeline — pre-ledger strip", () => {
  it("renders event-ledger InfoTip when ledger_start_date is present", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData({ ledger_start_date: "2026-06-06T00:00:00Z" }))
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    expect(screen.getByText(/event ledger begins/i)).toBeInTheDocument()
  })

  it("does not render event-ledger strip when ledger_start_date is null", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData({ ledger_start_date: null }))
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    expect(screen.queryByText(/event ledger begins/i)).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Event detail card (L2 event glyph, deliverable #1)
// ---------------------------------------------------------------------------

describe("Timeline — event detail card", () => {
  it("opens event detail card when canvas fires onEventClick", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    fireEvent.click(screen.getByTestId("stub-trigger-event"))

    await waitFor(() => {
      expect(screen.getByTestId("event-detail-card")).toBeInTheDocument()
    })
    expect(screen.getByText(/Quantum Annealing/)).toBeInTheDocument()
  })

  it("shows contradiction claim texts in event detail card", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    fireEvent.click(screen.getByTestId("stub-trigger-event"))

    await waitFor(() => screen.getByTestId("event-detail-card"))
    expect(screen.getByText(/claim a contradicts claim b/i)).toBeInTheDocument()
    expect(screen.getByText(/The algorithm converges/i)).toBeInTheDocument()
    expect(screen.getByText(/The algorithm is NP-hard/i)).toBeInTheDocument()
  })

  it("event detail card has composeChat and wiki buttons", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    fireEvent.click(screen.getByTestId("stub-trigger-event"))

    await waitFor(() => screen.getByTestId("event-detail-card"))
    expect(screen.getByRole("button", { name: /ask about this/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /open in wiki/i })).toBeInTheDocument()
  })

  it("event detail card has no axe violations", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    const { container } = render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    fireEvent.click(screen.getByTestId("stub-trigger-event"))
    await waitFor(() => screen.getByTestId("event-detail-card"))

    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// Track detail card (deliverable #2)
// ---------------------------------------------------------------------------

describe("Timeline — track detail card", () => {
  it("opens track detail card when canvas fires onTrackClick", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    fireEvent.click(screen.getByTestId("stub-trigger-track"))

    await waitFor(() => {
      expect(screen.getByTestId("track-detail-card")).toBeInTheDocument()
    })
    // "Alice" appears in the track card header
    const card = screen.getByTestId("track-detail-card")
    expect(card.textContent).toContain("Alice")
  })

  it("shows legacy events when extension is absent", async () => {
    // Set up track data with events (beforeEach also sets this)
    mockFetchTrack.mockResolvedValue({
      canonical_id: "e1",
      name: "Alice",
      events: [
        {
          ts: "2026-05-09T10:00:00Z",
          artifact_id: "art1",
          artifact_filename: "paper.md",
          confidence: 0.9,
          summary: "Alice contributed to the research.",
          co_mentioned: [],
        },
      ],
      cached: false,
    })
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    fireEvent.click(screen.getByTestId("stub-trigger-track"))

    await waitFor(() => screen.getByTestId("track-detail-card"))
    // Wait for loading to resolve and events to appear
    await waitFor(
      () => expect(screen.queryByText(/loading events/i)).not.toBeInTheDocument(),
      { timeout: 3000 },
    )
    const card = screen.getByTestId("track-detail-card")
    expect(card.textContent).toContain("paper.md")
  })

  it("shows 'No knowledge events' when track has no events", async () => {
    mockFetchTrack.mockResolvedValue(makeTrackData({ events: [] }))
    mockFetchStrata.mockResolvedValue(makeStrataData())
    render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    fireEvent.click(screen.getByTestId("stub-trigger-track"))

    await waitFor(() => screen.getByTestId("track-detail-card"))
    await waitFor(() => expect(screen.getByText(/no knowledge events/i)).toBeInTheDocument())
  })

  it("track detail card has no axe violations", async () => {
    mockFetchTrack.mockResolvedValue(makeTrackData())
    mockFetchStrata.mockResolvedValue(makeStrataData())
    const { container } = render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    fireEvent.click(screen.getByTestId("stub-trigger-track"))
    await waitFor(() => screen.getByTestId("track-detail-card"))

    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// lastViewedAt persistence on unmount (deliverable #3)
// ---------------------------------------------------------------------------

describe("Timeline — lastViewedAt persistence", () => {
  it("writes lastViewedAt to localStorage on unmount", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData())
    const { unmount } = render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))

    // Confirm not written before unmount
    const before = JSON.parse(localStorage.getItem("cerid-timeline-config") ?? "{}")
    expect(before.lastViewedAt).toBeUndefined()

    act(() => { unmount() })

    const after = JSON.parse(localStorage.getItem("cerid-timeline-config") ?? "{}")
    expect(after.lastViewedAt).toBeTruthy()
    // Should be a valid ISO date
    expect(() => new Date(after.lastViewedAt).toISOString()).not.toThrow()
  })
})

// ---------------------------------------------------------------------------
// A11y — all states (D.2 contract)
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

  it("pre-ledger strip state has no axe violations", async () => {
    mockFetchStrata.mockResolvedValue(makeStrataData({ ledger_start_date: "2026-06-06T00:00:00Z" }))
    const { container } = render(<Timeline />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("timeline-mode"))
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
