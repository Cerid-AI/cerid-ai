// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { WikiIndexView } from "@/components/wiki/wiki-index-view"

vi.mock("@/lib/api/wiki-browse", () => ({
  fetchWikiIndex: vi.fn(),
}))

import { fetchWikiIndex } from "@/lib/api/wiki-browse"
const mockedFetchWikiIndex = vi.mocked(fetchWikiIndex)

function buildClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={buildClient()}>{ui}</QueryClientProvider>
}

const SAMPLE_ENTRIES: Awaited<ReturnType<typeof fetchWikiIndex>> = {
  entries: [
    { slug: "other:python", name: "Python", entity_type: "OTHER", one_liner: "A high-level language.", last_updated_at: "2026-06-08T04:07:53Z", activity_score: 52, has_summary: true },
    { slug: "org:acme", name: "Acme", entity_type: "ORG", one_liner: null, last_updated_at: null, activity_score: 5, has_summary: false },
    { slug: "other:rust", name: "Rust", entity_type: "OTHER", one_liner: "A systems language.", last_updated_at: null, activity_score: 10, has_summary: true },
  ],
  total: null,
}

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// 4-state: loading
// ---------------------------------------------------------------------------

describe("WikiIndexView — loading", () => {
  it("renders loading skeletons", () => {
    mockedFetchWikiIndex.mockImplementationOnce(() => new Promise(() => {}))
    const { container } = render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    expect(container.querySelector("[role='status']")).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// 4-state: error
// ---------------------------------------------------------------------------

describe("WikiIndexView — error", () => {
  it("shows alert with retry on fetch failure", async () => {
    mockedFetchWikiIndex.mockRejectedValue(new Error("index fetch failed"))
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument()
      expect(screen.getByText("Retry")).toBeInTheDocument()
    }, { timeout: 5000 })
  })
})

// ---------------------------------------------------------------------------
// 4-state: empty
// ---------------------------------------------------------------------------

describe("WikiIndexView — empty", () => {
  it("shows empty state when no entries", async () => {
    mockedFetchWikiIndex.mockResolvedValue({ entries: [], total: null })
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("No entities yet")).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// 4-state: success
// ---------------------------------------------------------------------------

describe("WikiIndexView — success", () => {
  beforeEach(() => {
    mockedFetchWikiIndex.mockResolvedValue(SAMPLE_ENTRIES)
  })

  it("renders entity names grouped by first letter", async () => {
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("Python")).toBeInTheDocument()
      expect(screen.getByText("Acme")).toBeInTheDocument()
      expect(screen.getByText("Rust")).toBeInTheDocument()
    })
    // A section heading and P/R section headings
    expect(screen.getByText("A")).toBeInTheDocument()
    expect(screen.getByText("P")).toBeInTheDocument()
    expect(screen.getByText("R")).toBeInTheDocument()
  })

  it("applies stub styling for has_summary: false entries", async () => {
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => screen.getByText("Acme"))
    const acmeEl = screen.getByText("Acme")
    expect(acmeEl.className).toContain("text-muted-foreground")
  })

  it("calls onSelectEntity when a row is clicked", async () => {
    const onSelectEntity = vi.fn()
    render(wrap(<WikiIndexView onSelectEntity={onSelectEntity} />))
    await waitFor(() => screen.getByText("Python"))
    // Find the button wrapping "Python"
    const btn = screen.getByRole("button", { name: /Python/ })
    await userEvent.click(btn)
    expect(onSelectEntity).toHaveBeenCalledWith("other:python")
  })

  it("shows 'N of M' when totals indicate server truncation (amendment #7)", async () => {
    mockedFetchWikiIndex.mockResolvedValue({
      entries: SAMPLE_ENTRIES.entries,
      total: 100, // server has 100 but only 3 returned
    })
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText(/Showing 3 of 100/)).toBeInTheDocument()
    })
  })

  it("shows total count when no truncation", async () => {
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => {
      expect(screen.getByText("3 entities")).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// axe
// ---------------------------------------------------------------------------

describe("WikiIndexView — axe-clean", () => {
  it("is axe-clean on success state", async () => {
    mockedFetchWikiIndex.mockResolvedValue(SAMPLE_ENTRIES)
    const { container } = render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => screen.getByText("Python"))
    expect(await axe(container)).toHaveNoViolations()
  })
})
