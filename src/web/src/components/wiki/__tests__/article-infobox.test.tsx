// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { axe } from "jest-axe"
import { ArticleInfobox } from "@/components/wiki/article-infobox"
import type { WikiEntityPage } from "@/lib/types/wiki"

// MiniGraph uses MutationObserver (TrustBandBadge) and graph API
vi.stubGlobal("MutationObserver", class {
  observe() {}
  disconnect() {}
})

vi.mock("@/lib/api/graph", () => ({
  fetchNeighborhood: vi.fn().mockResolvedValue({
    focal_entity: "test-entity",
    nodes: [],
    edges: [],
    truncated: false,
    cached: false,
  }),
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makePage(overrides: Partial<WikiEntityPage> = {}): WikiEntityPage {
  return {
    slug: "test:entity",
    name: "Test Entity",
    entity_type: "OTHER",
    summary: "A test entity with a reasonable summary.",
    related_entities: [],
    source_artifacts: [],
    contradictions: [],
    external_references: [],
    last_updated_at: null,
    next_refresh_due: null,
    confidence_band: "unknown",
    ...overrides,
  }
}

function renderInfobox(page: WikiEntityPage) {
  return render(
    <ArticleInfobox
      page={page}
      onNavigateToDomain={vi.fn()}
      onNavigateToConcept={vi.fn()}
    />,
  )
}

// ---------------------------------------------------------------------------
// WK3: completeness indicator in infobox
// ---------------------------------------------------------------------------

describe("ArticleInfobox — WK3 completeness indicator", () => {
  it("renders 'Stub' badge when completeness is stub", () => {
    const page = makePage({ completeness: "stub" })
    renderInfobox(page)
    expect(screen.getByText("Stub")).toBeInTheDocument()
    expect(screen.getByLabelText(/Article completeness: Stub/i)).toBeInTheDocument()
  })

  it("renders 'Start' badge when completeness is start", () => {
    const page = makePage({ completeness: "start" })
    renderInfobox(page)
    expect(screen.getByText("Start")).toBeInTheDocument()
    expect(screen.getByLabelText(/Article completeness: Start/i)).toBeInTheDocument()
  })

  it("renders 'Full' badge when completeness is full", () => {
    const page = makePage({ completeness: "full" })
    renderInfobox(page)
    expect(screen.getByText("Full")).toBeInTheDocument()
    expect(screen.getByLabelText(/Article completeness: Full/i)).toBeInTheDocument()
  })

  it("omits the completeness row when completeness is undefined", () => {
    const page = makePage({ completeness: undefined })
    renderInfobox(page)
    expect(screen.queryByText("Stub")).not.toBeInTheDocument()
    expect(screen.queryByText("Start")).not.toBeInTheDocument()
    expect(screen.queryByText("Full")).not.toBeInTheDocument()
  })

  it("is axe-clean with completeness=full", async () => {
    const page = makePage({ completeness: "full", entity_type: "ORG" })
    const { container } = renderInfobox(page)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean with completeness=stub", async () => {
    const page = makePage({ completeness: "stub" })
    const { container } = renderInfobox(page)
    expect(await axe(container)).toHaveNoViolations()
  })
})
