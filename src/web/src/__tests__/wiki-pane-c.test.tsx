// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// Mocks — must be set up before any import of the component
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-wiki-entities", () => ({
  useWikiEntities: vi.fn(),
}))
vi.mock("@/lib/api/domains", () => ({
  fetchDomainCounts: vi.fn(),
}))
vi.mock("@/lib/api/wiki-browse", () => ({
  fetchWikiIndex: vi.fn(),
  fetchWikiLog: vi.fn(),
  fetchWikiConcept: vi.fn(),
}))
// EntityDetailView is a heavyweight component — stub it
vi.mock("@/components/wiki/entity-detail-view", () => ({
  EntityDetailView: ({ slug }: { slug: string }) => (
    <div data-testid="entity-detail-view">{slug}</div>
  ),
}))

import WikiPane from "@/components/wiki/wiki-pane"
import { useWikiEntities } from "@/hooks/use-wiki-entities"
import { fetchDomainCounts } from "@/lib/api/domains"
import { fetchWikiLog, fetchWikiIndex, fetchWikiConcept } from "@/lib/api/wiki-browse"

const mockedUseWikiEntities = vi.mocked(useWikiEntities)
const mockedFetchDomainCounts = vi.mocked(fetchDomainCounts)
const mockedFetchWikiLog = vi.mocked(fetchWikiLog)
const mockedFetchWikiIndex = vi.mocked(fetchWikiIndex)
const mockedFetchWikiConcept = vi.mocked(fetchWikiConcept)

const EMPTY_ENTITY_HOOK = { data: [], isLoading: false, isError: false, refetch: vi.fn() }
const EMPTY_DOMAINS = { domains: [], uncategorized_entities: 0, derived_at: null }

function buildClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={buildClient()}>{ui}</QueryClientProvider>
}

beforeEach(() => {
  vi.clearAllMocks()
  // clear localStorage state so each test starts fresh
  localStorage.clear()
  mockedUseWikiEntities.mockReturnValue(EMPTY_ENTITY_HOOK as ReturnType<typeof useWikiEntities>)
  mockedFetchDomainCounts.mockResolvedValue(EMPTY_DOMAINS)
  mockedFetchWikiLog.mockResolvedValue([])
  mockedFetchWikiIndex.mockResolvedValue({ entries: [], total: null })
  mockedFetchWikiConcept.mockResolvedValue(null)
})

// ---------------------------------------------------------------------------
// Landing shown at root
// ---------------------------------------------------------------------------

describe("WikiPane — landing view at root", () => {
  it("renders the WikiLanding when no entity is selected", async () => {
    render(wrap(<WikiPane />))
    await waitFor(() => {
      // Fresh-install mocks (everything empty) collapse the dashboard to
      // the single landing EmptyState.
      expect(screen.getByText("No wiki articles yet")).toBeInTheDocument()
    })
  })

  it("shows 'Wiki' as the pane header title at root", () => {
    render(wrap(<WikiPane />))
    expect(screen.getByRole("heading", { name: "Wiki" })).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Breadcrumb wiring
// ---------------------------------------------------------------------------

describe("WikiPane — breadcrumb", () => {
  it("shows breadcrumb nav when entity is selected", async () => {
    mockedUseWikiEntities.mockReturnValue({
      ...EMPTY_ENTITY_HOOK,
      data: [
        { slug: "other:python", name: "Python", entity_type: "OTHER", summary_preview: null, mention_count: 5, recent_activity_score: 10, last_updated_at: null, primary_domain: "coding" },
      ],
    } as ReturnType<typeof useWikiEntities>)

    render(wrap(<WikiPane />))
    // Click via aria-label on the entity list item (uses the aria-label from entity-list-item.tsx)
    await waitFor(() => screen.getByLabelText("Python"))
    await userEvent.click(screen.getByLabelText("Python"))

    await waitFor(() => {
      expect(screen.getByRole("navigation", { name: "Wiki breadcrumb" })).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// Rail collapsible
// ---------------------------------------------------------------------------

describe("WikiPane — collapsible rail", () => {
  it("renders rail toggle button", () => {
    render(wrap(<WikiPane />))
    expect(screen.getByLabelText(/navigation rail/i)).toBeInTheDocument()
  })

  it("toggles rail when toggle button is clicked", async () => {
    render(wrap(<WikiPane />))
    const toggle = screen.getByLabelText(/Collapse navigation rail/i)
    await userEvent.click(toggle)
    // After collapse, button label changes
    expect(screen.getByLabelText(/Expand navigation rail/i)).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Separator removed
// ---------------------------------------------------------------------------

describe("WikiPane — no trailing Separator", () => {
  it("does not render a vestigial trailing separator after the main grid", () => {
    const { container } = render(wrap(<WikiPane />))
    // The Separator component renders data-slot="separator"
    const separators = container.querySelectorAll("[data-slot='separator']")
    // No trailing separator should be present at the pane root level
    expect(separators.length).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// axe
// ---------------------------------------------------------------------------

describe("WikiPane — axe-clean", () => {
  it("is axe-clean at the landing root state", async () => {
    const { container } = render(wrap(<WikiPane />))
    await waitFor(() => screen.getByText("No wiki articles yet"))
    expect(await axe(container)).toHaveNoViolations()
  })
})
