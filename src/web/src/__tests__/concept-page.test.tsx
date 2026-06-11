// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ConceptPage } from "@/components/wiki/concept-page"

vi.mock("@/lib/api/wiki-browse", () => ({
  fetchWikiConcept: vi.fn(),
}))

import { fetchWikiConcept } from "@/lib/api/wiki-browse"
const mockedFetchWikiConcept = vi.mocked(fetchWikiConcept)

function buildClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={buildClient()}>{ui}</QueryClientProvider>
}

const PYTHON_CONCEPT: Awaited<ReturnType<typeof fetchWikiConcept>> = {
  slug: "concept:0:2625",
  name: "Python",
  summary: "A community focused on Python tooling.",
  member_count: 71,
  level: 0,
  last_updated_at: "2026-06-10T00:00:00Z",
  members: [
    { slug: "other:python", name: "Python", entity_type: "OTHER" },
    { slug: "org:cpython", name: "CPython", entity_type: "ORG" },
  ],
}

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// 4-state: loading
// ---------------------------------------------------------------------------

describe("ConceptPage — loading", () => {
  it("renders a loading skeleton", () => {
    mockedFetchWikiConcept.mockImplementationOnce(() => new Promise(() => {}))
    const { container } = render(wrap(<ConceptPage conceptId="concept:0:2625" onSelectEntity={vi.fn()} />))
    expect(container.querySelector("[role='status']")).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// 4-state: error
// ---------------------------------------------------------------------------

describe("ConceptPage — error", () => {
  it("shows destructive alert with retry on fetch failure", async () => {
    mockedFetchWikiConcept.mockRejectedValue(new Error("network error"))
    render(wrap(<ConceptPage conceptId="concept:0:2625" onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument()
      expect(screen.getByText("Retry")).toBeInTheDocument()
    }, { timeout: 5000 })
  })
})

// ---------------------------------------------------------------------------
// 4-state: not found
// ---------------------------------------------------------------------------

describe("ConceptPage — not found", () => {
  it("shows empty state when concept returns null", async () => {
    mockedFetchWikiConcept.mockResolvedValue(null)
    render(wrap(<ConceptPage conceptId="concept:0:9999" onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("Concept not found")).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// 4-state: success
// ---------------------------------------------------------------------------

describe("ConceptPage — success", () => {
  beforeEach(() => {
    mockedFetchWikiConcept.mockResolvedValue(PYTHON_CONCEPT)
  })

  it("renders the concept name as a heading", async () => {
    render(wrap(<ConceptPage conceptId="concept:0:2625" onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: "Python" })).toBeInTheDocument()
    })
  })

  it("renders the summary prose", async () => {
    render(wrap(<ConceptPage conceptId="concept:0:2625" onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("A community focused on Python tooling.")).toBeInTheDocument()
    })
  })

  it("renders member list with entity types", async () => {
    render(wrap(<ConceptPage conceptId="concept:0:2625" onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      // "Python" appears as both the concept title (h1) and as a member — use getAllByText
      expect(screen.getAllByText("Python").length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText("CPython")).toBeInTheDocument()
    })
  })

  it("calls onSelectEntity when a member is clicked", async () => {
    const onSelectEntity = vi.fn()
    render(wrap(<ConceptPage conceptId="concept:0:2625" onSelectEntity={onSelectEntity} />))
    await waitFor(() => screen.getByText("CPython"))
    await userEvent.click(screen.getByRole("button", { name: "CPython" }))
    expect(onSelectEntity).toHaveBeenCalledWith("org:cpython")
  })

  it("shows placeholder-detection: Concept 0:2625 → 'Community 0:2625'", async () => {
    mockedFetchWikiConcept.mockResolvedValue({
      ...PYTHON_CONCEPT,
      name: "Concept 0:2625",
    })
    render(wrap(<ConceptPage conceptId="concept:0:2625" onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("Community 0:2625")).toBeInTheDocument()
    })
  })

  it("shows 'not yet generated' prose when summary is null", async () => {
    mockedFetchWikiConcept.mockResolvedValue({ ...PYTHON_CONCEPT, summary: null })
    render(wrap(<ConceptPage conceptId="concept:0:2625" onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      expect(
        screen.getByText(/Community summary not yet generated/),
      ).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// axe
// ---------------------------------------------------------------------------

describe("ConceptPage — axe-clean", () => {
  it("is axe-clean on success state", async () => {
    mockedFetchWikiConcept.mockResolvedValue(PYTHON_CONCEPT)
    const { container } = render(
      wrap(<ConceptPage conceptId="concept:0:2625" onSelectEntity={vi.fn()} />),
    )
    await waitFor(() => screen.getByRole("heading", { level: 1 }))
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean on error state", async () => {
    mockedFetchWikiConcept.mockRejectedValue(new Error("error"))
    const { container } = render(
      wrap(<ConceptPage conceptId="concept:0:2625" onSelectEntity={vi.fn()} />),
    )
    await waitFor(() => screen.getByRole("alert"), { timeout: 5000 })
    expect(await axe(container)).toHaveNoViolations()
  })
})
