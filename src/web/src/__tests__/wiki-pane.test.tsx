// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import type { EntitySummary, WikiEntityPage } from "@/lib/types/wiki"

// MutationObserver stub — TrustBandBadge (rendered inside EntityDetailView)
// registers a theme watcher; we stub it to avoid jsdom noise.
vi.stubGlobal("MutationObserver", class {
  observe() {}
  disconnect() {}
})

// Mock graph API so MiniGraph's sr-only neighbor fetch and MentionSparkline
// don't throw in tests that render EntityDetailView.
vi.mock("@/lib/api/graph", () => ({
  fetchNeighborhood: vi.fn().mockResolvedValue({ focal_entity: "tesla", nodes: [], edges: [], truncated: false, cached: false }),
  fetchTimeline: vi.fn().mockResolvedValue({ entity: "tesla", from_date: "", to_date: "", granularity: "day", buckets: [], total_mentions: 0, total_entities_introduced: 0, cached: false }),
}))

// ---------------------------------------------------------------------------
// Mock the hooks so we can control returned data
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-wiki-entities", () => ({
  useWikiEntities: vi.fn(),
  useWikiEntity: vi.fn(),
}))

import { useWikiEntities, useWikiEntity } from "@/hooks/use-wiki-entities"

const mockUseWikiEntities = useWikiEntities as ReturnType<typeof vi.fn>
const mockUseWikiEntity = useWikiEntity as ReturnType<typeof vi.fn>

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeEntitySummary(overrides: Partial<EntitySummary> = {}): EntitySummary {
  return {
    slug: "tesla",
    name: "Tesla",
    entity_type: "ORG",
    summary_preview: "Tesla is an electric vehicle manufacturer.",
    mention_count: 12,
    recent_activity_score: 90,
    last_updated_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    primary_domain: null,
    ...overrides,
  }
}

function makeEntityPage(slug: string): WikiEntityPage {
  return {
    slug,
    name: slug.charAt(0).toUpperCase() + slug.slice(1),
    entity_type: "ORG",
    summary: `Summary for ${slug}.`,
    related_entities: [],
    source_artifacts: [],
    contradictions: [],
    external_references: [],
    last_updated_at: new Date().toISOString(),
    next_refresh_due: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
    confidence_band: "medium",
  }
}

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

// Lazy-imported via dynamic import in App.tsx; import directly for tests
let WikiPane: React.ComponentType

beforeEach(async () => {
  vi.restoreAllMocks()
  // Default: no entity selected, so detail hook returns null
  mockUseWikiEntity.mockReturnValue({ data: null, isLoading: false, isError: false, isNotFound: true })
  // Dynamically import to reset module state
  const mod = await import("@/components/wiki/wiki-pane")
  WikiPane = mod.default
})

// ---------------------------------------------------------------------------
// Entity list from mocked useWikiEntities
// ---------------------------------------------------------------------------

describe("WikiPane — entity list", () => {
  it("renders entity names from useWikiEntities", async () => {
    mockUseWikiEntities.mockReturnValue({
      data: [
        makeEntitySummary({ slug: "tesla", name: "Tesla" }),
        makeEntitySummary({ slug: "spacex", name: "SpaceX" }),
      ],
      isLoading: false,
      isError: false,
    })
    render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getAllByText("Tesla").length).toBeGreaterThan(0)
      expect(screen.getAllByText("SpaceX").length).toBeGreaterThan(0)
    })
  })

  it("renders entity count in header", async () => {
    mockUseWikiEntities.mockReturnValue({
      data: [makeEntitySummary(), makeEntitySummary({ slug: "spacex", name: "SpaceX" })],
      isLoading: false,
      isError: false,
    })
    render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText("2 entities")).toBeTruthy()
    })
  })

  it("renders skeleton while loading", () => {
    mockUseWikiEntities.mockReturnValue({ data: undefined, isLoading: true, isError: false })
    const { container } = render(<WikiPane />, { wrapper: createWrapper() })
    const skeletons = container.querySelectorAll("[class*=animate-pulse]")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("renders error alert on fetch failure", async () => {
    mockUseWikiEntities.mockReturnValue({ data: undefined, isLoading: false, isError: true })
    render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getAllByText(/Failed to load entities/).length).toBeGreaterThan(0)
    })
  })

  it("renders empty state when entity list is empty", async () => {
    mockUseWikiEntities.mockReturnValue({ data: [], isLoading: false, isError: false })
    render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getAllByText(/No entities yet|No pages yet/i).length).toBeGreaterThan(0)
    })
  })
})

// ---------------------------------------------------------------------------
// Empty state when no entity selected
// ---------------------------------------------------------------------------

describe("WikiPane — empty detail state", () => {
  it("shows the wiki landing when nothing is selected", async () => {
    mockUseWikiEntities.mockReturnValue({ data: [makeEntitySummary()], isLoading: false, isError: false })
    render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/Recently updated/i)).toBeTruthy()
    })
  })
})

// ---------------------------------------------------------------------------
// Selecting an entity loads the detail view
// ---------------------------------------------------------------------------

describe("WikiPane — entity selection", () => {
  it("loads entity detail view after selecting from list", async () => {
    const user = userEvent.setup()
    const entities = [makeEntitySummary({ slug: "tesla", name: "Tesla" })]
    mockUseWikiEntities.mockReturnValue({ data: entities, isLoading: false, isError: false })
    // After selection, the detail hook should return a page
    mockUseWikiEntity.mockReturnValue({
      data: makeEntityPage("tesla"),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })

    render(<WikiPane />, { wrapper: createWrapper() })

    // Click the Tesla list item
    await waitFor(() => screen.getAllByText("Tesla")[0])
    await user.click(screen.getAllByText("Tesla")[0])

    await waitFor(() => {
      // The detail header renders the entity name (from makeEntityPage)
      expect(screen.getByRole("heading", { level: 1, name: "Tesla" })).toBeTruthy()
    })
  })
})

// ---------------------------------------------------------------------------
// Keyboard navigation between list items
// ---------------------------------------------------------------------------

describe("WikiPane — keyboard navigation", () => {
  it("list items are reachable via Tab", async () => {
    const user = userEvent.setup()
    const entities = [
      makeEntitySummary({ slug: "tesla", name: "Tesla" }),
      makeEntitySummary({ slug: "spacex", name: "SpaceX" }),
    ]
    mockUseWikiEntities.mockReturnValue({ data: entities, isLoading: false, isError: false })
    render(<WikiPane />, { wrapper: createWrapper() })

    await waitFor(() => screen.getAllByText("Tesla")[0])

    // Tab to the first list item button
    await user.tab()
    const active1 = document.activeElement
    expect(active1?.textContent).toContain("Tesla")

    // Tab to the second list item button
    await user.tab()
    const active2 = document.activeElement
    expect(active2?.textContent).toContain("SpaceX")
  })

  it("Enter key selects a list item", async () => {
    const user = userEvent.setup()
    const entities = [makeEntitySummary({ slug: "tesla", name: "Tesla" })]
    mockUseWikiEntities.mockReturnValue({ data: entities, isLoading: false, isError: false })
    mockUseWikiEntity.mockReturnValue({
      data: makeEntityPage("tesla"),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })

    render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(() => screen.getAllByText("Tesla")[0])

    // Tab to item and press Enter
    await user.tab()
    await user.keyboard("{Enter}")

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: "Tesla" })).toBeTruthy()
    })
  })
})

// ---------------------------------------------------------------------------
// D.2: four-state matrix
// ---------------------------------------------------------------------------

describe("WikiPane — four-state matrix (D.2)", () => {
  it("idle/loading: shows Skeleton placeholders while loading", () => {
    mockUseWikiEntities.mockReturnValue({ data: undefined, isLoading: true, isError: false })
    const { container } = render(<WikiPane />, { wrapper: createWrapper() })
    const skeletons = container.querySelectorAll("[class*=animate-pulse]")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("loaded: renders entity list when data arrives", async () => {
    mockUseWikiEntities.mockReturnValue({
      data: [makeEntitySummary({ slug: "tesla", name: "Tesla" })],
      isLoading: false,
      isError: false,
    })
    render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getAllByText("Tesla").length).toBeGreaterThan(0))
  })

  it("empty: shows empty state when entity list is empty", async () => {
    mockUseWikiEntities.mockReturnValue({ data: [], isLoading: false, isError: false })
    render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getAllByText(/No entities yet|No pages yet/i).length).toBeGreaterThan(0))
  })

  it("error: shows destructive Alert on fetch failure", async () => {
    mockUseWikiEntities.mockReturnValue({ data: undefined, isLoading: false, isError: true })
    render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getAllByText(/Failed to load entities/).length).toBeGreaterThan(0))
  })
})

// ---------------------------------------------------------------------------
// D.3: axe-clean
// ---------------------------------------------------------------------------

describe("WikiPane — axe-clean (D.3)", () => {
  it("is axe-clean (D.3) in empty state", async () => {
    mockUseWikiEntities.mockReturnValue({ data: [], isLoading: false, isError: false })
    const { container } = render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(async () => {
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  })

  it("is axe-clean (D.3) in populated state", async () => {
    mockUseWikiEntities.mockReturnValue({
      data: [makeEntitySummary({ slug: "tesla", name: "Tesla" })],
      isLoading: false,
      isError: false,
    })
    const { container } = render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(async () => {
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  })
})

// ---------------------------------------------------------------------------
// axe-clean (legacy describe — kept for backwards compat)
// ---------------------------------------------------------------------------

describe("WikiPane — axe-clean", () => {
  it("empty state is axe-clean", async () => {
    mockUseWikiEntities.mockReturnValue({ data: [], isLoading: false, isError: false })
    const { container } = render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(async () => {
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  })

  it("populated list with no selection is axe-clean", async () => {
    mockUseWikiEntities.mockReturnValue({
      data: [makeEntitySummary({ slug: "tesla", name: "Tesla" })],
      isLoading: false,
      isError: false,
    })
    const { container } = render(<WikiPane />, { wrapper: createWrapper() })
    await waitFor(async () => {
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  })
})
