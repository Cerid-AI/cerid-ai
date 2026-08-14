// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import type { CommunitySummary, CommunityFull } from "@/lib/types/community"

// ---------------------------------------------------------------------------
// Mock the hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-communities", () => ({
  useCommunities: vi.fn(),
  useCommunity: vi.fn(),
}))

import { useCommunities, useCommunity } from "@/hooks/use-communities"
import { GraphExplorer } from "@/components/kb/graph-explorer"

const mockUseCommunities = useCommunities as ReturnType<typeof vi.fn>
const mockUseCommunity = useCommunity as ReturnType<typeof vi.fn>

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeCommunitySummary(overrides: Partial<CommunitySummary> = {}): CommunitySummary {
  return {
    community_id: "0:7",
    level: 0,
    name: null,
    summary: "Machine learning research community centred on transformers and large models.",
    member_count: 25,
    last_summarized_at: "2026-05-10T02:00:00+00:00",
    ...overrides,
  }
}

function makeCommunityFull(overrides: Partial<CommunityFull> = {}): CommunityFull {
  return {
    community_id: "0:7",
    level: 0,
    name: null,
    summary: "Machine learning research community centred on transformers and large models.",
    member_count: 25,
    last_summarized_at: "2026-05-10T02:00:00+00:00",
    members: [
      { canonical_id: "person:yann-lecun", name: "Yann LeCun", entity_type: "PERSON" },
      { canonical_id: "org:meta-ai", name: "Meta AI", entity_type: "ORG" },
    ],
    members_total: 25,
    members_truncated: true,
    related_communities: [{ community_id: "0:4", co_mention_count: 38 }],
    ...overrides,
  }
}

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  // Default: no detail loaded
  mockUseCommunity.mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    isNotFound: false,
  })
})

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("GraphExplorer — loading state", () => {
  it("renders skeleton placeholders while loading", () => {
    mockUseCommunities.mockReturnValue({ data: undefined, isLoading: true, isError: false })
    const { container } = render(<GraphExplorer />, { wrapper: createWrapper() })
    const skeletons = container.querySelectorAll("[class*=animate-pulse]")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("has aria-busy during load", () => {
    mockUseCommunities.mockReturnValue({ data: undefined, isLoading: true, isError: false })
    const { container } = render(<GraphExplorer />, { wrapper: createWrapper() })
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

describe("GraphExplorer — error state", () => {
  it("renders error alert on fetch failure", () => {
    mockUseCommunities.mockReturnValue({ data: undefined, isLoading: false, isError: true })
    render(<GraphExplorer />, { wrapper: createWrapper() })
    expect(screen.getByRole("alert")).toBeTruthy()
    expect(screen.getByText(/Failed to load communities/)).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

describe("GraphExplorer — empty state", () => {
  it("renders empty state when no communities", () => {
    mockUseCommunities.mockReturnValue({ data: [], isLoading: false, isError: false })
    render(<GraphExplorer />, { wrapper: createWrapper() })
    expect(screen.getByText("No communities yet")).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Community list rendering
// ---------------------------------------------------------------------------

describe("GraphExplorer — community list", () => {
  it("renders a card for each community", () => {
    const communities = [
      makeCommunitySummary({ community_id: "0:7" }),
      makeCommunitySummary({ community_id: "0:3", member_count: 12 }),
    ]
    mockUseCommunities.mockReturnValue({ data: communities, isLoading: false, isError: false })
    render(<GraphExplorer />, { wrapper: createWrapper() })
    // Two card buttons rendered
    const cards = screen.getAllByRole("button", { name: /Community/i })
    // There's at least 2 (the card buttons) — the "Ask about" button appears later
    expect(cards.length).toBeGreaterThanOrEqual(2)
  })

  it("shows member count badge on each card", () => {
    mockUseCommunities.mockReturnValue({
      data: [makeCommunitySummary({ member_count: 42 })],
      isLoading: false,
      isError: false,
    })
    render(<GraphExplorer />, { wrapper: createWrapper() })
    expect(screen.getByText("42")).toBeTruthy()
  })

  it("truncates long summaries in the list card", () => {
    const longSummary = "A".repeat(100)
    mockUseCommunities.mockReturnValue({
      data: [makeCommunitySummary({ summary: longSummary })],
      isLoading: false,
      isError: false,
    })
    render(<GraphExplorer />, { wrapper: createWrapper() })
    // Card shows ≤72 chars + ellipsis
    const truncated = screen.getByText(/A{1,72}…/)
    expect(truncated).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Selecting a community loads the detail
// ---------------------------------------------------------------------------

describe("GraphExplorer — community selection", () => {
  it("shows detail panel after clicking a community card", async () => {
    const user = userEvent.setup()
    const communities = [makeCommunitySummary()]
    mockUseCommunities.mockReturnValue({ data: communities, isLoading: false, isError: false })
    mockUseCommunity.mockReturnValue({
      data: makeCommunityFull(),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })

    render(<GraphExplorer />, { wrapper: createWrapper() })

    const card = screen.getByRole("button", { name: /Community 0:7/ })
    await user.click(card)

    // Detail panel heading
    expect(screen.getByRole("heading", { level: 1, name: /Community 0:7/ })).toBeTruthy()
    // Synthesis section heading appears in detail (not in list card)
    expect(screen.getByRole("heading", { level: 2, name: /Synthesis/i })).toBeTruthy()
  })

  it("activates community card with keyboard Enter", async () => {
    const user = userEvent.setup()
    const communities = [makeCommunitySummary()]
    mockUseCommunities.mockReturnValue({ data: communities, isLoading: false, isError: false })
    mockUseCommunity.mockReturnValue({
      data: makeCommunityFull(),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })

    render(<GraphExplorer />, { wrapper: createWrapper() })

    const card = screen.getByRole("button", { name: /Community 0:7/ })
    card.focus()
    await user.keyboard("{Enter}")

    expect(screen.getByRole("heading", { level: 1, name: /Community 0:7/ })).toBeTruthy()
  })

  it("activates community card with keyboard Space", async () => {
    const user = userEvent.setup()
    const communities = [makeCommunitySummary()]
    mockUseCommunities.mockReturnValue({ data: communities, isLoading: false, isError: false })
    mockUseCommunity.mockReturnValue({
      data: makeCommunityFull(),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })

    render(<GraphExplorer />, { wrapper: createWrapper() })

    const card = screen.getByRole("button", { name: /Community 0:7/ })
    card.focus()
    await user.keyboard(" ")

    expect(screen.getByRole("heading", { level: 1, name: /Community 0:7/ })).toBeTruthy()
  })

  it("shows loading skeleton while detail is loading", async () => {
    const user = userEvent.setup()
    mockUseCommunities.mockReturnValue({
      data: [makeCommunitySummary()],
      isLoading: false,
      isError: false,
    })
    mockUseCommunity.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      isNotFound: false,
    })

    const { container } = render(<GraphExplorer />, { wrapper: createWrapper() })

    await user.click(screen.getByRole("button", { name: /Community 0:7/ }))

    const skeletons = container.querySelectorAll("[class*=animate-pulse]")
    expect(skeletons.length).toBeGreaterThan(0)
  })
})

// ---------------------------------------------------------------------------
// Ask about CTA
// ---------------------------------------------------------------------------

describe("GraphExplorer — Ask about CTA", () => {
  it("renders Ask about this community button", async () => {
    const user = userEvent.setup()
    mockUseCommunities.mockReturnValue({
      data: [makeCommunitySummary()],
      isLoading: false,
      isError: false,
    })
    mockUseCommunity.mockReturnValue({
      data: makeCommunityFull(),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })

    render(<GraphExplorer />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: /Community 0:7/ }))

    expect(screen.getByRole("button", { name: /Ask about this community/i })).toBeTruthy()
  })

  it("calls onAskAbout with the community when CTA is clicked", async () => {
    const user = userEvent.setup()
    const onAskAbout = vi.fn()
    const full = makeCommunityFull()

    mockUseCommunities.mockReturnValue({
      data: [makeCommunitySummary()],
      isLoading: false,
      isError: false,
    })
    mockUseCommunity.mockReturnValue({
      data: full,
      isLoading: false,
      isError: false,
      isNotFound: false,
    })

    render(<GraphExplorer onAskAbout={onAskAbout} />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: /Community 0:7/ }))
    await user.click(screen.getByRole("button", { name: /Ask about this community/i }))

    expect(onAskAbout).toHaveBeenCalledOnce()
    expect(onAskAbout).toHaveBeenCalledWith(full)
  })
})

// ---------------------------------------------------------------------------
// Entity pills
// ---------------------------------------------------------------------------

describe("GraphExplorer — entity pills", () => {
  it("expands entity list when Entities section is toggled", async () => {
    const user = userEvent.setup()
    mockUseCommunities.mockReturnValue({
      data: [makeCommunitySummary()],
      isLoading: false,
      isError: false,
    })
    mockUseCommunity.mockReturnValue({
      data: makeCommunityFull(),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })

    render(<GraphExplorer />, { wrapper: createWrapper() })

    // Select community
    await user.click(screen.getByRole("button", { name: /Community 0:7/ }))

    // Entities collapsible
    const entitiesToggle = screen.getByRole("button", { name: /Entities/ })
    await user.click(entitiesToggle)

    // Entity pills visible
    expect(screen.getByRole("button", { name: /Yann LeCun/ })).toBeTruthy()
    expect(screen.getByRole("button", { name: /Meta AI/ })).toBeTruthy()
  })

  it("calls onEntityClick when entity pill is clicked", async () => {
    const user = userEvent.setup()
    const onEntityClick = vi.fn()

    mockUseCommunities.mockReturnValue({
      data: [makeCommunitySummary()],
      isLoading: false,
      isError: false,
    })
    mockUseCommunity.mockReturnValue({
      data: makeCommunityFull(),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })

    render(<GraphExplorer onEntityClick={onEntityClick} />, { wrapper: createWrapper() })

    // Select community
    await user.click(screen.getByRole("button", { name: /Community 0:7/ }))

    // Expand entity list
    await user.click(screen.getByRole("button", { name: /Entities/ }))

    // Click entity pill
    await user.click(screen.getByRole("button", { name: /Yann LeCun/ }))

    expect(onEntityClick).toHaveBeenCalledWith("person:yann-lecun")
  })
})

// ---------------------------------------------------------------------------
// axe-clean
// ---------------------------------------------------------------------------

describe("GraphExplorer — axe-clean", () => {
  it("empty state is axe-clean", async () => {
    mockUseCommunities.mockReturnValue({ data: [], isLoading: false, isError: false })
    const { container } = render(<GraphExplorer />, { wrapper: createWrapper() })
    await waitFor(async () => {
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  })

  it("community list (no selection) is axe-clean", async () => {
    mockUseCommunities.mockReturnValue({
      data: [makeCommunitySummary()],
      isLoading: false,
      isError: false,
    })
    const { container } = render(<GraphExplorer />, { wrapper: createWrapper() })
    await waitFor(async () => {
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  })

  it("community with detail selected is axe-clean", async () => {
    const user = userEvent.setup()
    mockUseCommunities.mockReturnValue({
      data: [makeCommunitySummary()],
      isLoading: false,
      isError: false,
    })
    mockUseCommunity.mockReturnValue({
      data: makeCommunityFull(),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })

    const { container } = render(<GraphExplorer />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: /Community 0:7/ }))

    await waitFor(async () => {
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  })

  it("loading state is axe-clean", async () => {
    mockUseCommunities.mockReturnValue({ data: undefined, isLoading: true, isError: false })
    const { container } = render(<GraphExplorer />, { wrapper: createWrapper() })
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it("error state is axe-clean", async () => {
    mockUseCommunities.mockReturnValue({ data: undefined, isLoading: false, isError: true })
    const { container } = render(<GraphExplorer />, { wrapper: createWrapper() })
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
