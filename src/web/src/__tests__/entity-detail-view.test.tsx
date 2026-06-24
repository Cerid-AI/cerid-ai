// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import type { WikiEntityPage } from "@/lib/types/wiki"

// MutationObserver stub — TrustBandBadge registers a theme watcher that
// calls MutationObserver.observe on mount; we stub it to avoid jsdom noise.
vi.stubGlobal("MutationObserver", class {
  observe() {}
  disconnect() {}
})

// ---------------------------------------------------------------------------
// Mock the hooks so we can control returned data
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-wiki-entities", () => ({
  useWikiEntities: vi.fn(),
  useWikiEntity: vi.fn(),
}))

// Mock fetchNeighborhood so MiniGraph's sr-only neighbor fetch doesn't error.
vi.mock("@/lib/api/graph", () => ({
  fetchNeighborhood: vi.fn().mockResolvedValue({ focal_entity: "elon-musk", nodes: [], edges: [], truncated: false, cached: false }),
  fetchTimeline: vi.fn().mockResolvedValue({ entity: "elon-musk", from_date: "", to_date: "", granularity: "day", buckets: [], total_mentions: 0, total_entities_introduced: 0, cached: false }),
}))

// Mock wiki API clients so mutation tests don't hit the network.
const mockRefreshEntity = vi.fn().mockResolvedValue(undefined)
const mockUpdateEntitySummary = vi.fn()
vi.mock("@/lib/api/wiki", () => ({
  fetchWikiEntity: vi.fn(),
  fetchWikiEntities: vi.fn(),
  refreshEntity: (...args: unknown[]) => mockRefreshEntity(...args),
  updateEntitySummary: (...args: unknown[]) => mockUpdateEntitySummary(...args),
}))

import { useWikiEntity } from "@/hooks/use-wiki-entities"
import { EntityDetailView } from "@/components/wiki/entity-detail-view"

const mockUseWikiEntity = useWikiEntity as ReturnType<typeof vi.fn>

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

// 1 hour in the future — not overdue (default fixture). NOTE: deliberately
// not `new Date()` because by the time the test asserts, "now" has advanced
// past the fixture value and refreshOverdue() flips true. V-P1.8 now hides
// the confidence band when overdue, so the band-badge test needs a clearly
// future next_refresh_due.
const FUTURE_ISO = new Date(Date.now() + 60 * 60 * 1000).toISOString()
const PAST_ISO = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString() // 2h ago

function makeEntityPage(overrides: Partial<WikiEntityPage> = {}): WikiEntityPage {
  return {
    slug: "elon-musk",
    name: "Elon Musk",
    entity_type: "PERSON",
    summary: "**Elon Musk** is a technology entrepreneur.",
    related_entities: [
      { slug: "tesla", name: "Tesla", co_mention_strength: 42, entity_type: "ORG", display_title: null, has_summary: true, one_liner: "Electric vehicle company." },
      { slug: "spacex", name: "SpaceX", co_mention_strength: 38, entity_type: "ORG", display_title: null, has_summary: true, one_liner: "Aerospace company." },
    ],
    source_artifacts: [
      {
        artifact_id: "art-001",
        title: "Forbes Profile",
        filename: "forbes-profile.md",
        domain: "research",
        source_type: "file",
        confidence: 0.9,
        updated_at: PAST_ISO,
      },
    ],
    contradictions: [
      {
        finding_id: "finding-001",
        claim_a_id: "claim-a",
        claim_b_id: "claim-b",
        claim_a_text: "Elon Musk founded Tesla.",
        claim_b_text: "Elon Musk did not found Tesla.",
        entity_slug: "elon-musk",
        severity: "high",
        detected_at: PAST_ISO,
        query_ctx_id: null,
        source_artifacts: [],
      },
    ],
    external_references: [],
    last_updated_at: PAST_ISO,
    next_refresh_due: FUTURE_ISO, // not overdue
    confidence_band: "high",
    ...overrides,
  }
}

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

function createWrapperWithClient() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
  return { qc, wrapper }
}

beforeEach(() => {
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("EntityDetailView — loading state", () => {
  it("renders skeleton placeholders while loading", () => {
    mockUseWikiEntity.mockReturnValue({ data: undefined, isLoading: true, isError: false, isNotFound: false })
    const { container } = render(
      <EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />,
      { wrapper: createWrapper() },
    )
    // Skeletons have animate-pulse
    const skeletons = container.querySelectorAll("[class*=animate-pulse]")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("skeleton has aria-busy=true", () => {
    mockUseWikiEntity.mockReturnValue({ data: undefined, isLoading: true, isError: false, isNotFound: false })
    const { container } = render(
      <EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />,
      { wrapper: createWrapper() },
    )
    const busy = container.querySelector('[aria-busy="true"]')
    expect(busy).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

describe("EntityDetailView — error state", () => {
  it("renders error alert on fetch failure", () => {
    mockUseWikiEntity.mockReturnValue({ data: undefined, isLoading: false, isError: true, isNotFound: false })
    render(
      <EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />,
      { wrapper: createWrapper() },
    )
    expect(screen.getByRole("alert")).toBeTruthy()
    expect(screen.getByText(/Failed to load article/)).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Not-found state
// ---------------------------------------------------------------------------

describe("EntityDetailView — not-found state", () => {
  it("renders empty state when entity is null (404)", () => {
    mockUseWikiEntity.mockReturnValue({ data: null, isLoading: false, isError: false, isNotFound: true })
    render(
      <EntityDetailView slug="does-not-exist" onSelectRelated={vi.fn()} />,
      { wrapper: createWrapper() },
    )
    expect(screen.getByText("Entity not found")).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Settled state — full page
// ---------------------------------------------------------------------------

describe("EntityDetailView — settled state", () => {
  it("renders entity name in header", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByRole("heading", { level: 1, name: "Elon Musk" })).toBeTruthy()
  })

  it("renders trust band badge for entity", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    // TrustBandBadge renders aria-label starting with "Trust: verified" for confidence_band="high"
    const badge = document.querySelector('[aria-label^="Trust: verified"]')
    expect(badge).not.toBeNull()
  })

  it("renders markdown summary content", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    // ReactMarkdown renders **Elon Musk** as <strong>
    const boldEl = document.querySelector("strong")
    expect(boldEl?.textContent).toBe("Elon Musk")
    expect(screen.getByText(/technology entrepreneur/)).toBeTruthy()
  })

  it("renders related entity buttons", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByRole("button", { name: /View entity: Tesla/ })).toBeTruthy()
    expect(screen.getByRole("button", { name: /View entity: SpaceX/ })).toBeTruthy()
  })

  it("calls onSelectRelated when a related entity button is clicked", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={onSelect} />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: /View entity: Tesla/ }))
    expect(onSelect).toHaveBeenCalledWith("tesla")
  })

  it("renders source artifact title", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByText("Forbes Profile")).toBeTruthy()
  })

  it("renders source artifact as interactive button", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    const sourceBtn = screen.getByRole("button", { name: /View source: Forbes Profile/ })
    expect(sourceBtn).toBeTruthy()
  })

  it("renders contradictions section when contradictions exist", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getAllByText(/Contradictions/).length).toBeGreaterThan(0)
    expect(screen.getByText("Elon Musk founded Tesla.")).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Contradictions section hidden when empty
// ---------------------------------------------------------------------------

describe("EntityDetailView — contradictions hidden when empty", () => {
  it("does NOT render contradictions section when contradictions array is empty", () => {
    mockUseWikiEntity.mockReturnValue({
      data: makeEntityPage({ contradictions: [] }),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.queryByText(/Contradictions/)).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Updating pill when next_refresh_due is in the past (overdue)
// ---------------------------------------------------------------------------

describe("EntityDetailView — refresh status pill", () => {
  it("shows activity pill when refresh_status is 'running'", () => {
    mockUseWikiEntity.mockReturnValue({
      data: makeEntityPage({ refresh_status: "running" }),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByLabelText("Updating from new evidence")).toBeTruthy()
  })

  it("shows quiet hint when refresh_status is 'due'", () => {
    mockUseWikiEntity.mockReturnValue({
      data: makeEntityPage({ refresh_status: "due" }),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByLabelText("Refresh scheduled")).toBeTruthy()
  })

  it("shows no pill when refresh_status is 'idle'", () => {
    mockUseWikiEntity.mockReturnValue({
      data: makeEntityPage({ refresh_status: "idle" }),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.queryByLabelText("Updating from new evidence")).toBeNull()
    expect(screen.queryByLabelText("Refresh scheduled")).toBeNull()
  })

  it("shows no pill when refresh_status is absent (treated as idle)", () => {
    const { refresh_status: _omit, ...pageWithoutStatus } = makeEntityPage() as WikiEntityPage & { refresh_status?: unknown }
    void _omit
    mockUseWikiEntity.mockReturnValue({
      data: pageWithoutStatus,
      isLoading: false,
      isError: false,
      isNotFound: false,
    })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.queryByLabelText("Updating from new evidence")).toBeNull()
    expect(screen.queryByLabelText("Refresh scheduled")).toBeNull()
  })

  it("always shows TrustBandBadge regardless of refresh status", () => {
    mockUseWikiEntity.mockReturnValue({
      data: makeEntityPage({ refresh_status: "running" }),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    const badge = document.querySelector('[aria-label^="Trust:"]')
    expect(badge).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Markdown content renders correctly
// ---------------------------------------------------------------------------

describe("EntityDetailView — markdown rendering", () => {
  it("renders bold markdown text", () => {
    mockUseWikiEntity.mockReturnValue({
      data: makeEntityPage({ summary: "**Bold text** is here." }),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    const bold = document.querySelector("strong")
    expect(bold).not.toBeNull()
    expect(bold?.textContent).toBe("Bold text")
  })

  it("renders no Summary section when summary is null", () => {
    mockUseWikiEntity.mockReturnValue({
      data: makeEntityPage({ summary: null }),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.queryByText("Summary")).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// axe-clean
// ---------------------------------------------------------------------------

describe("EntityDetailView — axe-clean", () => {
  it("settled state is axe-clean", async () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    const { container } = render(
      <EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />,
      { wrapper: createWrapper() },
    )
    await waitFor(async () => {
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  })

  it("loading state is axe-clean", async () => {
    mockUseWikiEntity.mockReturnValue({ data: undefined, isLoading: true, isError: false, isNotFound: false })
    const { container } = render(
      <EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />,
      { wrapper: createWrapper() },
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it("error state is axe-clean", async () => {
    mockUseWikiEntity.mockReturnValue({ data: undefined, isLoading: false, isError: true, isNotFound: false })
    const { container } = render(
      <EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />,
      { wrapper: createWrapper() },
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it("backlinks outer wrapper is a div, not a labeled section landmark (no axe landmark-unique violation)", () => {
    // Regression guard: the outer #wiki-section-backlinks must be a <div> so
    // it does not duplicate the <section aria-labelledby="wiki-what-links-here-heading">
    // that WhatLinksHere renders as its own root.
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    const { container } = render(
      <EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />,
      { wrapper: createWrapper() },
    )
    const wrapper = container.querySelector("#wiki-section-backlinks")
    expect(wrapper).not.toBeNull()
    expect(wrapper?.tagName.toLowerCase()).toBe("div")
    expect(wrapper?.hasAttribute("aria-labelledby")).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// WK4: Manual Refresh button
// ---------------------------------------------------------------------------

describe("EntityDetailView — WK4 Refresh button", () => {
  beforeEach(() => {
    mockRefreshEntity.mockResolvedValue(undefined)
  })

  it("renders a Refresh button in the settled state", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByRole("button", { name: /Refresh/i })).toBeTruthy()
  })

  it("calls refreshEntity with the entity slug when Refresh is clicked", async () => {
    const user = userEvent.setup()
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: /Refresh/i }))
    await waitFor(() => {
      expect(mockRefreshEntity).toHaveBeenCalledWith("elon-musk")
    })
  })

  it("disables the Refresh button while the mutation is pending", async () => {
    let resolveRefresh!: () => void
    mockRefreshEntity.mockReturnValue(new Promise<void>((res) => { resolveRefresh = res }))
    const user = userEvent.setup()
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    const btn = screen.getByRole("button", { name: /Refresh/i })
    await user.click(btn)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Refresh queued/i })).toBeTruthy()
    })
    resolveRefresh()
  })
})

// ---------------------------------------------------------------------------
// WK4: Inline editable summary
// ---------------------------------------------------------------------------

describe("EntityDetailView — WK4 editable summary", () => {
  beforeEach(() => {
    mockUpdateEntitySummary.mockResolvedValue(makeEntityPage({ summary: "Updated summary." }))
  })

  it("renders an Edit summary button in the settled state with a summary", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByRole("button", { name: /Edit summary/i })).toBeTruthy()
  })

  it("clicking Edit reveals a textarea seeded with the current summary", async () => {
    const user = userEvent.setup()
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: /Edit summary/i }))
    const textarea = screen.getByRole("textbox", { name: /Edit summary/i })
    expect(textarea).toBeTruthy()
    expect((textarea as HTMLTextAreaElement).value).toContain("Elon Musk")
  })

  it("clicking Cancel hides the textarea and restores the original summary", async () => {
    const user = userEvent.setup()
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: /Edit summary/i }))
    expect(screen.getByRole("textbox", { name: /Edit summary/i })).toBeTruthy()
    await user.click(screen.getByRole("button", { name: /Cancel/i }))
    expect(screen.queryByRole("textbox", { name: /Edit summary/i })).toBeNull()
    expect(screen.getByText(/technology entrepreneur/)).toBeTruthy()
  })

  it("clicking Save calls updateEntitySummary with the edited text", async () => {
    const user = userEvent.setup()
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: /Edit summary/i }))
    const textarea = screen.getByRole("textbox", { name: /Edit summary/i }) as HTMLTextAreaElement
    await user.clear(textarea)
    await user.type(textarea, "New summary text.")
    await user.click(screen.getByRole("button", { name: /Save/i }))
    await waitFor(() => {
      expect(mockUpdateEntitySummary).toHaveBeenCalledWith("elon-musk", "New summary text.")
    })
  })

  it("Save exits edit mode after success", async () => {
    const user = userEvent.setup()
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: /Edit summary/i }))
    await user.click(screen.getByRole("button", { name: /Save/i }))
    await waitFor(() => {
      expect(screen.queryByRole("textbox", { name: /Edit summary/i })).toBeNull()
    })
  })

  it("Save success invalidates the entity query", async () => {
    const user = userEvent.setup()
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    const { qc, wrapper } = createWrapperWithClient()
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries")
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper })
    await user.click(screen.getByRole("button", { name: /Edit summary/i }))
    await user.click(screen.getByRole("button", { name: /Save/i }))
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: ["wiki-entity", "elon-musk"] }),
      )
    })
  })

  it("WK4 settled+edit state is axe-clean", async () => {
    const user = userEvent.setup()
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    const { container } = render(
      <EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />,
      { wrapper: createWrapper() },
    )
    await user.click(screen.getByRole("button", { name: /Edit summary/i }))
    await waitFor(async () => {
      const results = await axe(container)
      expect(results).toHaveNoViolations()
    })
  })
})
