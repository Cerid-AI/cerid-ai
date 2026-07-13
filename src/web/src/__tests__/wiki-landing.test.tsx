// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { WikiLanding } from "@/components/wiki/wiki-landing"
import type { EntitySummary } from "@/lib/types/wiki"

// ---------------------------------------------------------------------------
// Mock all data fetches + the navigation bridge
// ---------------------------------------------------------------------------

const { composeChatMock } = vi.hoisted(() => ({ composeChatMock: vi.fn() }))

vi.mock("@/lib/api/domains", () => ({
  fetchDomainCounts: vi.fn(),
}))
vi.mock("@/lib/api/wiki-browse", () => ({
  fetchWikiIndex: vi.fn(),
}))
vi.mock("@/lib/api/wiki", () => ({
  fetchWikiEntities: vi.fn(),
}))
vi.mock("@/hooks/use-wiki-entities", () => ({
  useWikiEntities: vi.fn(),
}))
vi.mock("@/contexts/navigation-context", () => ({
  useNavigation: () => ({
    activePane: "wiki",
    goTo: vi.fn(),
    composeChat: composeChatMock,
    consumeChatSeed: () => null,
    navVersion: 0,
  }),
}))

import { fetchDomainCounts } from "@/lib/api/domains"
import { fetchWikiIndex } from "@/lib/api/wiki-browse"
import { fetchWikiEntities } from "@/lib/api/wiki"
import { useWikiEntities } from "@/hooks/use-wiki-entities"

const mockedFetchDomainCounts = vi.mocked(fetchDomainCounts)
const mockedFetchWikiIndex = vi.mocked(fetchWikiIndex)
const mockedFetchWikiEntities = vi.mocked(fetchWikiEntities)
const mockedUseWikiEntities = vi.mocked(useWikiEntities)

function buildQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function wrap(ui: React.ReactNode) {
  return (
    <QueryClientProvider client={buildQueryClient()}>{ui}</QueryClientProvider>
  )
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeEntity(overrides: Partial<EntitySummary> = {}): EntitySummary {
  return {
    slug: "tesla",
    name: "Tesla",
    entity_type: "ORG",
    summary_preview: "Tesla is an electric vehicle manufacturer.",
    mention_count: 12,
    recent_activity_score: 90,
    last_updated_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    primary_domain: "coding",
    ...overrides,
  }
}

const DOMAINS = {
  domains: [
    { name: "coding", icon: "code", description: null, in_taxonomy: true, artifact_count: 5, entity_count: 12, salience: 0, sub_categories: [] },
    { name: "finance", icon: null, description: null, in_taxonomy: true, artifact_count: 2, entity_count: 4, salience: 0, sub_categories: [] },
  ],
  uncategorized_entities: 2,
  derived_at: "2026-07-10T00:00:00Z",
}

const INDEX = {
  entries: [
    { slug: "org:tesla", name: "Tesla", entity_type: "ORG", one_liner: "EV maker.", last_updated_at: "2026-07-10T00:00:00Z", activity_score: 90, has_summary: true, completeness: "full" as const },
    { slug: "org:spacex", name: "SpaceX", entity_type: "ORG", one_liner: null, last_updated_at: null, activity_score: 40, has_summary: false, completeness: "stub" as const },
    { slug: "other:rust", name: "Rust", entity_type: "OTHER", one_liner: "Systems language.", last_updated_at: null, activity_score: 10, has_summary: true, completeness: "start" as const },
  ],
  total: 3,
}

const ENTITIES = [
  makeEntity({ slug: "tesla", name: "Tesla", last_updated_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString() }),
  makeEntity({ slug: "spacex", name: "SpaceX", recent_activity_score: 70, primary_domain: "finance", last_updated_at: new Date(Date.now() - 60 * 60 * 1000).toISOString() }),
]

const EMPTY_DOMAINS = { domains: [], uncategorized_entities: 0, derived_at: null }
const EMPTY_INDEX = { entries: [], total: null }
const EMPTY_ENTITIES = { data: [], isLoading: false, isError: false, refetch: vi.fn() }

function mockSuccess() {
  mockedFetchDomainCounts.mockResolvedValue(DOMAINS)
  mockedFetchWikiIndex.mockResolvedValue(INDEX)
  mockedUseWikiEntities.mockReturnValue({
    data: ENTITIES,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as ReturnType<typeof useWikiEntities>)
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedFetchDomainCounts.mockResolvedValue(EMPTY_DOMAINS)
  mockedFetchWikiIndex.mockResolvedValue(EMPTY_INDEX)
  mockedFetchWikiEntities.mockResolvedValue([])
  mockedUseWikiEntities.mockReturnValue(EMPTY_ENTITIES as ReturnType<typeof useWikiEntities>)
})

// ---------------------------------------------------------------------------
// 4-state: loading
// ---------------------------------------------------------------------------

describe("WikiLanding — loading state", () => {
  it("shows layout-shaped skeletons while sources are loading", () => {
    mockedFetchDomainCounts.mockImplementation(() => new Promise(() => {}))
    mockedFetchWikiIndex.mockImplementation(() => new Promise(() => {}))
    mockedUseWikiEntities.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useWikiEntities>)

    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    // Stats strip, domain tiles, and both lists each render a status skeleton.
    expect(screen.getAllByRole("status").length).toBeGreaterThanOrEqual(3)
    expect(screen.getByLabelText("Loading stats")).toBeInTheDocument()
    expect(screen.getByLabelText("Loading domains")).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 4-state: error
// ---------------------------------------------------------------------------

describe("WikiLanding — error state", () => {
  it("shows destructive alerts with retry when the domain source fails", async () => {
    mockedFetchDomainCounts.mockRejectedValue(new Error("domain fetch failed"))
    mockedUseWikiEntities.mockReturnValue({
      data: ENTITIES,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useWikiEntities>)

    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0)
    }, { timeout: 5000 })
    expect(screen.getAllByRole("button", { name: "Retry" }).length).toBeGreaterThan(0)
  })

  it("a failed block does not take down the entity-backed lists (per-block degradation)", async () => {
    mockedFetchDomainCounts.mockRejectedValue(new Error("domain fetch failed"))
    mockedUseWikiEntities.mockReturnValue({
      data: ENTITIES,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useWikiEntities>)

    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("Recently updated")).toBeInTheDocument()
    }, { timeout: 5000 })
    const recent = screen.getByRole("region", { name: /Recently updated/i })
    expect(within(recent).getByText("SpaceX")).toBeInTheDocument()
  })

  it("shows the entity-list error with retry when the entities hook fails", async () => {
    mockSuccess()
    const refetch = vi.fn()
    mockedUseWikiEntities.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    } as ReturnType<typeof useWikiEntities>)

    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0)
    })
    await userEvent.click(screen.getAllByRole("button", { name: "Retry" })[0])
    expect(refetch).toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// 4-state: empty (fresh-install collapse)
// ---------------------------------------------------------------------------

describe("WikiLanding — empty state", () => {
  it("collapses to a single EmptyState when the wiki has no articles or domains", async () => {
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("No wiki articles yet")).toBeInTheDocument()
    })
    expect(screen.getByText(/Ingest some sources to grow your wiki/)).toBeInTheDocument()
    // Dashboard blocks are hidden in the collapsed state.
    expect(screen.queryByLabelText("Search the wiki")).not.toBeInTheDocument()
    expect(screen.queryByText("Browse by domain")).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 4-state: success — stats strip, domain tiles, lists, browse affordances
// ---------------------------------------------------------------------------

describe("WikiLanding — success state", () => {
  beforeEach(() => {
    mockSuccess()
  })

  it("renders the stats strip with article / domain / stub / uncategorized counts", async () => {
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    const stats = await screen.findByRole("region", { name: "Wiki stats" })
    expect(await within(stats).findByText("Articles")).toBeInTheDocument()
    expect(within(stats).getByText("3")).toBeInTheDocument() // articles
    expect(within(stats).getByText("Domains")).toBeInTheDocument()
    expect(within(stats).getByText("Stub articles")).toBeInTheDocument()
    expect(within(stats).getByText("Uncategorized")).toBeInTheDocument()
    // stubCount 1 (only the completeness="stub" entry)
    expect(within(stats).getAllByText("1").length).toBeGreaterThan(0)
    // uncategorized 2 + domains 2
    expect(within(stats).getAllByText("2").length).toBe(2)
  })

  it("renders domain tiles and clicking one browses that domain", async () => {
    const onSelectDomain = vi.fn()
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={onSelectDomain} />))
    const tile = await screen.findByLabelText(/Browse Coding — 12 articles/)
    await userEvent.click(tile)
    expect(onSelectDomain).toHaveBeenCalledWith("coding")
  })

  it("renders Recently updated newest-first with domain badge and opens the article", async () => {
    const onSelectEntity = vi.fn()
    render(wrap(<WikiLanding onSelectEntity={onSelectEntity} onSelectDomain={vi.fn()} />))
    const recent = await screen.findByRole("region", { name: /Recently updated/i })
    const items = within(recent).getAllByRole("listitem")
    // SpaceX (1h ago) sorts before Tesla (3h ago)
    expect(items[0]).toHaveTextContent("SpaceX")
    expect(items[0]).toHaveTextContent("finance") // DomainBadge
    expect(items[0]).toHaveTextContent(/Updated/) // LastUpdated primitive
    await userEvent.click(within(items[0]).getByRole("button"))
    expect(onSelectEntity).toHaveBeenCalledWith("spacex")
  })

  it("renders Most active in backend activity order", async () => {
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    const active = await screen.findByRole("region", { name: /Most active/i })
    const items = within(active).getAllByRole("listitem")
    expect(items[0]).toHaveTextContent("Tesla")
    expect(items[0]).toHaveTextContent("90")
  })

  it("A–Z index affordance calls onOpenIndex", async () => {
    const onOpenIndex = vi.fn()
    render(
      wrap(
        <WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} onOpenIndex={onOpenIndex} />,
      ),
    )
    await userEvent.click(await screen.findByRole("button", { name: /A–Z index/ }))
    expect(onOpenIndex).toHaveBeenCalled()
  })

  it("'Ask about your wiki' bridges to chat via composeChat", async () => {
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    await userEvent.click(await screen.findByRole("button", { name: /Ask about your wiki/ }))
    expect(composeChatMock).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("wiki") }),
    )
  })
})

// ---------------------------------------------------------------------------
// Hero search
// ---------------------------------------------------------------------------

describe("WikiLanding — hero search", () => {
  beforeEach(() => {
    mockSuccess()
  })

  it("queries the server-side search and opens a result", async () => {
    const onSelectEntity = vi.fn()
    mockedFetchWikiEntities.mockResolvedValue([makeEntity({ slug: "tesla", name: "Tesla" })])

    render(wrap(<WikiLanding onSelectEntity={onSelectEntity} onSelectDomain={vi.fn()} />))
    const input = await screen.findByLabelText("Search the wiki")
    await userEvent.type(input, "tesla")

    await waitFor(() => {
      expect(mockedFetchWikiEntities).toHaveBeenCalledWith(
        expect.objectContaining({ q: "tesla", includeInternal: false }),
      )
    })
    const results = await screen.findByRole("region", { name: "Search results" })
    await userEvent.click(within(results).getByText("Tesla"))
    expect(onSelectEntity).toHaveBeenCalledWith("tesla")
  })

  it("offers an ask-in-chat bridge for the current query", async () => {
    mockedFetchWikiEntities.mockResolvedValue([])

    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    const input = await screen.findByLabelText("Search the wiki")
    await userEvent.type(input, "quantum")

    const results = await screen.findByRole("region", { name: "Search results" })
    await waitFor(() => {
      expect(within(results).getByText(/No articles match/)).toBeInTheDocument()
    })
    await userEvent.click(within(results).getByRole("button", { name: /Ask about/ }))
    expect(composeChatMock).toHaveBeenCalledWith({ text: "quantum" })
  })
})

// ---------------------------------------------------------------------------
// WK2: show-internal toggle (default OFF)
// ---------------------------------------------------------------------------

describe("WikiLanding — show-internal toggle (WK2)", () => {
  it("defaults the toggle OFF — fetchers omit internal data", async () => {
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))

    const toggle = await screen.findByRole("switch", { name: /internal/i })
    expect(toggle).not.toBeChecked()

    await waitFor(() => {
      expect(mockedUseWikiEntities).toHaveBeenCalledWith(
        expect.objectContaining({ includeInternal: false }),
      )
    })
    expect(mockedFetchWikiIndex).not.toHaveBeenCalledWith(
      expect.objectContaining({ includeInternal: true }),
    )
  })

  it("turning the toggle ON re-queries with includeInternal: true", async () => {
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))

    const toggle = await screen.findByRole("switch", { name: /internal/i })
    await userEvent.click(toggle)

    expect(toggle).toBeChecked()
    await waitFor(() => {
      expect(mockedUseWikiEntities).toHaveBeenCalledWith(
        expect.objectContaining({ includeInternal: true }),
      )
    })
    await waitFor(() => {
      expect(mockedFetchWikiIndex).toHaveBeenCalledWith(
        expect.objectContaining({ includeInternal: true }),
      )
    })
  })
})

// ---------------------------------------------------------------------------
// axe accessibility
// ---------------------------------------------------------------------------

describe("WikiLanding — axe-clean", () => {
  it("is axe-clean in the empty state", async () => {
    const { container } = render(
      wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />),
    )
    await waitFor(() => screen.getByText("No wiki articles yet"))
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in the success state", async () => {
    mockSuccess()
    const { container } = render(
      wrap(
        <WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} onOpenIndex={vi.fn()} />,
      ),
    )
    await screen.findByRole("region", { name: "Wiki stats" })
    await screen.findByLabelText(/Browse Coding/)
    expect(await axe(container)).toHaveNoViolations()
  })
})
