// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import type { WikiEntityPage } from "@/lib/types/wiki"

// ---------------------------------------------------------------------------
// Mock the hooks so we can control returned data
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-wiki-entities", () => ({
  useWikiEntities: vi.fn(),
  useWikiEntity: vi.fn(),
}))

import { useWikiEntity } from "@/hooks/use-wiki-entities"
import { EntityDetailView } from "@/components/wiki/entity-detail-view"

const mockUseWikiEntity = useWikiEntity as ReturnType<typeof vi.fn>

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const NOW_ISO = new Date().toISOString()
const PAST_ISO = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString() // 2h ago
const OVERDUE_ISO = new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString() // 25h ago — overdue

function makeEntityPage(overrides: Partial<WikiEntityPage> = {}): WikiEntityPage {
  return {
    slug: "elon-musk",
    name: "Elon Musk",
    entity_type: "PERSON",
    summary: "**Elon Musk** is a technology entrepreneur.",
    related_entities: [
      { slug: "tesla", name: "Tesla", co_mention_strength: 42 },
      { slug: "spacex", name: "SpaceX", co_mention_strength: 38 },
    ],
    source_artifacts: [
      {
        artifact_id: "art-001",
        title: "Forbes Profile",
        chunk_hash: "abc123def456",
        domain: "research",
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
    next_refresh_due: NOW_ISO, // not overdue
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
    expect(screen.getByText(/Failed to load entity page/)).toBeTruthy()
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

  it("renders confidence band badge", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    // ConfidenceBandBadge renders aria-label="Confidence: high"
    const badge = document.querySelector('[aria-label="Confidence: high"]')
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

  it("renders chunk hash chip (first 8 chars)", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByText("abc123de")).toBeTruthy()
  })

  it("renders contradictions section when contradictions exist", () => {
    mockUseWikiEntity.mockReturnValue({ data: makeEntityPage(), isLoading: false, isError: false, isNotFound: false })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByText(/Contradictions/)).toBeTruthy()
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

describe("EntityDetailView — updating pill", () => {
  it("shows 'Updating from new evidence' pill when next_refresh_due is in the past", () => {
    mockUseWikiEntity.mockReturnValue({
      data: makeEntityPage({ next_refresh_due: OVERDUE_ISO }),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByLabelText("Updating from new evidence")).toBeTruthy()
  })

  it("does NOT show updating pill when next_refresh_due is in the future", () => {
    const futureIso = new Date(Date.now() + 60 * 60 * 1000).toISOString()
    mockUseWikiEntity.mockReturnValue({
      data: makeEntityPage({ next_refresh_due: futureIso }),
      isLoading: false,
      isError: false,
      isNotFound: false,
    })
    render(<EntityDetailView slug="elon-musk" onSelectRelated={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.queryByLabelText("Updating from new evidence")).toBeNull()
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
})
