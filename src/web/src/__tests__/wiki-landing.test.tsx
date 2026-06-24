// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { WikiLanding } from "@/components/wiki/wiki-landing"

// ---------------------------------------------------------------------------
// Mock all data fetches
// ---------------------------------------------------------------------------

vi.mock("@/lib/api/domains", () => ({
  fetchDomainCounts: vi.fn(),
}))
vi.mock("@/lib/api/wiki-browse", () => ({
  fetchWikiLog: vi.fn(),
  fetchWikiIndex: vi.fn(),
}))
vi.mock("@/hooks/use-wiki-entities", () => ({
  useWikiEntities: vi.fn(),
}))

import { fetchDomainCounts } from "@/lib/api/domains"
import { fetchWikiLog, fetchWikiIndex } from "@/lib/api/wiki-browse"
import { useWikiEntities } from "@/hooks/use-wiki-entities"

const mockedFetchDomainCounts = vi.mocked(fetchDomainCounts)
const mockedFetchWikiLog = vi.mocked(fetchWikiLog)
const mockedFetchWikiIndex = vi.mocked(fetchWikiIndex)
const mockedUseWikiEntities = vi.mocked(useWikiEntities)

function buildQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function wrap(ui: React.ReactNode) {
  return (
    <QueryClientProvider client={buildQueryClient()}>{ui}</QueryClientProvider>
  )
}

const EMPTY_DOMAINS = { domains: [], uncategorized_entities: 0, derived_at: null }
const EMPTY_LOG: Awaited<ReturnType<typeof fetchWikiLog>> = []
const EMPTY_INDEX: Awaited<ReturnType<typeof fetchWikiIndex>> = { entries: [], total: null }
const EMPTY_ENTITIES = { data: [], isLoading: false, isError: false, refetch: vi.fn() }

beforeEach(() => {
  vi.clearAllMocks()
  mockedFetchDomainCounts.mockResolvedValue(EMPTY_DOMAINS)
  mockedFetchWikiLog.mockResolvedValue(EMPTY_LOG)
  mockedFetchWikiIndex.mockResolvedValue(EMPTY_INDEX)
  mockedUseWikiEntities.mockReturnValue(EMPTY_ENTITIES as ReturnType<typeof useWikiEntities>)
})

// ---------------------------------------------------------------------------
// 4-state: loading
// ---------------------------------------------------------------------------

describe("WikiLanding — loading state", () => {
  it("shows skeletons while domain data is loading", () => {
    mockedFetchDomainCounts.mockImplementationOnce(() => new Promise(() => {}))
    const { container } = render(
      wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />),
    )
    expect(container.querySelector("[role='status']")).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// 4-state: empty
// ---------------------------------------------------------------------------

describe("WikiLanding — empty state", () => {
  it("shows EmptyState when no domains loaded", async () => {
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("No domains yet")).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// 4-state: success + block-by-block independence
// ---------------------------------------------------------------------------

describe("WikiLanding — success state", () => {
  beforeEach(() => {
    mockedFetchDomainCounts.mockResolvedValue({
      domains: [
        { name: "coding", icon: "code", description: null, in_taxonomy: true, artifact_count: 5, entity_count: 12, salience: 0, sub_categories: [] },
      ],
      uncategorized_entities: 0,
      derived_at: "2026-06-10T00:00:00Z",
    })
  })

  it("renders domain cards from /graph/domains", async () => {
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("Coding")).toBeInTheDocument()
    })
  })

  it("calls onSelectDomain when a domain card is clicked", async () => {
    const onSelectDomain = vi.fn()
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={onSelectDomain} />))
    await waitFor(() => screen.getByText("Coding"))
    await userEvent.click(screen.getByLabelText(/Browse Coding/))
    expect(onSelectDomain).toHaveBeenCalledWith("coding")
  })

  it("renders Recent changes section heading", async () => {
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("Recent changes")).toBeInTheDocument()
    })
  })

  it("renders Most active section heading", async () => {
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("Most active this month")).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// Amendment #4: block-by-block degradation
// ---------------------------------------------------------------------------

describe("WikiLanding — per-block degradation (amendment #4)", () => {
  it("domain block error does not prevent recent-changes block from rendering heading", async () => {
    mockedFetchDomainCounts.mockRejectedValue(new Error("domain fetch failed"))
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("Recent changes")).toBeInTheDocument()
    }, { timeout: 5000 })
    // Domain block shows error, not empty page
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument()
    }, { timeout: 5000 })
  })

  it("log block error shows retry and leaves other blocks unaffected", async () => {
    mockedFetchWikiLog.mockRejectedValue(new Error("log fetch failed"))
    render(wrap(<WikiLanding onSelectEntity={vi.fn()} onSelectDomain={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0)
    }, { timeout: 5000 })
    // Domain block still renders (empty state, not error)
    expect(screen.getByText("Browse by domain")).toBeInTheDocument()
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

    // Most-active block queries with includeInternal: false by default.
    await waitFor(() => {
      expect(mockedUseWikiEntities).toHaveBeenCalledWith(
        expect.objectContaining({ includeInternal: false }),
      )
    })
    // Index browse query never asks for internal data while toggle is off.
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
    await waitFor(() => screen.getByText("No domains yet"))
    expect(await axe(container)).toHaveNoViolations()
  })
})
