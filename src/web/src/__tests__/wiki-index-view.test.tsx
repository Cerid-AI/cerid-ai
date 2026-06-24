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
    { slug: "other:python", name: "Python", entity_type: "OTHER", one_liner: "A high-level language.", last_updated_at: "2026-06-08T04:07:53Z", activity_score: 52, has_summary: true, completeness: "full" },
    { slug: "org:acme", name: "Acme", entity_type: "ORG", one_liner: null, last_updated_at: null, activity_score: 5, has_summary: false, completeness: "stub" },
    { slug: "other:rust", name: "Rust", entity_type: "OTHER", one_liner: "A systems language.", last_updated_at: null, activity_score: 10, has_summary: true, completeness: "start" },
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

  // WK3: 3-class completeness marker
  it("WK3: sets data-completeness=full on fully-complete entry buttons", async () => {
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => screen.getByText("Python"))
    const btn = screen.getByRole("button", { name: /Python/ })
    expect(btn.getAttribute("data-completeness")).toBe("full")
  })

  it("WK3: sets data-completeness=stub on stub entry buttons", async () => {
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => screen.getByText("Acme"))
    const btn = screen.getByRole("button", { name: /Acme/ })
    expect(btn.getAttribute("data-completeness")).toBe("stub")
  })

  it("WK3: sets data-completeness=start on start entry buttons", async () => {
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => screen.getByText("Rust"))
    const btn = screen.getByRole("button", { name: /Rust/ })
    expect(btn.getAttribute("data-completeness")).toBe("start")
  })

  it("WK3: shows 'Summary in progress' hint for start-class entries", async () => {
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => screen.getByText("Rust"))
    expect(screen.getByText("Summary in progress")).toBeInTheDocument()
  })

  it("WK3: no pending hint inside a full-class entry's button", async () => {
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => screen.getByText("Python"))
    const pythonBtn = screen.getByRole("button", { name: /Python/ })
    // The Python entry is "full" — its own button should contain no pending text
    expect(pythonBtn.querySelector(".italic")).toBeNull()
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

  // WK1: article-body search
  it("WK1: body-only search surfaces entity whose one_liner matches but name does not", async () => {
    const user = userEvent.setup()
    render(wrap(<WikiIndexView onSelectEntity={vi.fn()} />))
    await waitFor(() => screen.getByText("Rust"))

    const input = screen.getByRole("searchbox")
    // "systems" appears in Rust's one_liner but not in the name "Rust" or slug "other:rust"
    await user.type(input, "systems")

    await waitFor(() => {
      expect(screen.getByText("Rust")).toBeInTheDocument()
      expect(screen.queryByText("Python")).not.toBeInTheDocument()
      expect(screen.queryByText("Acme")).not.toBeInTheDocument()
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
