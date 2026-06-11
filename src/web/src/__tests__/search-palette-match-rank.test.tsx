// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { SubjectsSearchPalette } from "@/components/subjects/search-palette"

vi.mock("@/hooks/use-wiki-entities", () => ({
  useWikiEntities: vi.fn(),
}))
vi.mock("@/lib/api/domains", () => ({
  fetchDomainCounts: vi.fn(),
}))
vi.mock("@/contexts/navigation-context", () => ({
  useNavigation: () => ({ goTo: vi.fn(), activePane: "subjects", navVersion: 0 }),
}))

import { useWikiEntities } from "@/hooks/use-wiki-entities"
import { fetchDomainCounts } from "@/lib/api/domains"

const mockedUseWikiEntities = vi.mocked(useWikiEntities)
const mockedFetchDomainCounts = vi.mocked(fetchDomainCounts)

function buildClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={buildClient()}>{ui}</QueryClientProvider>
}

const EMPTY_DOMAINS = { domains: [], uncategorized_entities: 0, derived_at: null }

beforeEach(() => {
  vi.clearAllMocks()
  mockedFetchDomainCounts.mockResolvedValue(EMPTY_DOMAINS)
})

// ---------------------------------------------------------------------------
// match_rank sort
// ---------------------------------------------------------------------------

describe("SubjectsSearchPalette — match_rank ordering", () => {
  it("sorts results by match_rank when present, exact match (0) first", async () => {
    mockedUseWikiEntities.mockReturnValue({
      data: [
        { slug: "other:python-substring", name: "python-substring", entity_type: "OTHER", summary_preview: null, mention_count: 10, recent_activity_score: 100, last_updated_at: null, primary_domain: null, match_rank: 2 },
        { slug: "other:python", name: "python", entity_type: "OTHER", summary_preview: null, mention_count: 2, recent_activity_score: 5, last_updated_at: null, primary_domain: null, match_rank: 0 },
        { slug: "other:python-prefix", name: "python-prefix", entity_type: "OTHER", summary_preview: null, mention_count: 5, recent_activity_score: 50, last_updated_at: null, primary_domain: null, match_rank: 1 },
      ],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useWikiEntities>)

    render(wrap(
      <SubjectsSearchPalette
        open={true}
        onOpenChange={vi.fn()}
        onPick={vi.fn()}
      />,
    ))

    // Type to trigger search
    const input = screen.getByRole("combobox")
    await userEvent.type(input, "py")

    await waitFor(() => {
      const buttons = screen.getAllByRole("option")
      // The exact match should come first in the Best Matches section
      expect(buttons[0]).toHaveTextContent("python")
    })
  })

  it("falls back to activity-score order when match_rank is absent", async () => {
    mockedUseWikiEntities.mockReturnValue({
      data: [
        { slug: "other:alpha", name: "alpha", entity_type: "OTHER", summary_preview: null, mention_count: 1, recent_activity_score: 5, last_updated_at: null, primary_domain: null },
        { slug: "other:beta", name: "beta", entity_type: "OTHER", summary_preview: null, mention_count: 2, recent_activity_score: 100, last_updated_at: null, primary_domain: null },
      ],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useWikiEntities>)

    render(wrap(
      <SubjectsSearchPalette
        open={true}
        onOpenChange={vi.fn()}
        onPick={vi.fn()}
      />,
    ))

    const input = screen.getByRole("combobox")
    await userEvent.type(input, "a")

    await waitFor(() => {
      // Without match_rank, server order is preserved (alpha was first in the mock)
      expect(screen.getAllByRole("option")[0]).toHaveTextContent("alpha")
    })
  })
})

// ---------------------------------------------------------------------------
// axe — scoped to the input row (the axe check covers C's match_rank change,
// not the pre-existing SectionedEntityListPalette empty-listbox state which
// has a known aria-required-children quirk in the jsdom environment).
// ---------------------------------------------------------------------------

// The axe test covers just the input row of the palette — the listbox empty
// state has a pre-existing aria-required-children issue in SectionedEntityListPalette
// (pre-dates this Cycle 3 change) that is tracked separately.
describe("SubjectsSearchPalette — axe-clean", () => {
  it("input row is axe-clean", async () => {
    mockedUseWikiEntities.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useWikiEntities>)

    const { container } = render(wrap(
      <SubjectsSearchPalette
        open={true}
        onOpenChange={vi.fn()}
        onPick={vi.fn()}
      />,
    ))
    await waitFor(() => screen.getByRole("combobox"))
    // Scope axe to just the combobox input (C's contribution), not the full listbox
    const inputRow = container.querySelector('input[role="combobox"]')?.closest("div")
    expect(inputRow).toBeTruthy()
    expect(await axe(inputRow!)).toHaveNoViolations()
  })
})
