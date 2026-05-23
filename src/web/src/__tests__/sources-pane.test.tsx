// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Phase B Day 8 — Sources pane integration tests. Verifies the
// mode switcher, URL state via ?sources_mode=, and lazy-loaded
// KnowledgePane mount.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import SourcesPane from "@/components/sources/sources-pane"

// Stub KnowledgePane so jsdom doesn't try to mount its sub-tree (which
// includes graph-explorer that pulls sigma).
vi.mock("@/components/kb/knowledge-pane", () => ({
  default: () => <div data-testid="kb-stub">KB Library</div>,
}))

function renderSources(initialSearch = "") {
  window.history.replaceState({}, "", `/${initialSearch}`)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <SourcesPane />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  window.history.replaceState({}, "", "/")
})

describe("SourcesPane — mode switcher", () => {
  it("renders all four mode tabs", () => {
    renderSources()
    expect(screen.getByRole("tab", { name: /library/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /activity/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /meetings/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /connectors/i })).toBeInTheDocument()
  })

  it("defaults to Library mode", () => {
    renderSources()
    expect(screen.getByRole("tab", { name: /library/i })).toHaveAttribute("aria-selected", "true")
  })

  it("starts on the mode named in ?sources_mode=", async () => {
    renderSources("?sources_mode=connectors")
    expect(screen.getByRole("tab", { name: /connectors/i })).toHaveAttribute("aria-selected", "true")
    // Connectors panel lazy-loads; assert on the Suspense fallback OR
    // the loaded content. Both prove the panel mounted.
    expect(
      await screen.findByText(/Loading connectors|No connectors configured|source/i),
    ).toBeInTheDocument()
  })

  it("switching mode writes ?sources_mode= and renders that panel", async () => {
    renderSources()
    fireEvent.click(screen.getByRole("tab", { name: /activity/i }))
    expect(new URLSearchParams(window.location.search).get("sources_mode")).toBe("activity")
    // Activity panel lazy-loads; assert on the Suspense fallback or
    // loaded "no activity yet" empty-state copy.
    expect(
      await screen.findByText(/Loading activity|No activity yet|Active/i),
    ).toBeInTheDocument()
  })

  it("switching back to library clears ?sources_mode= (library is default)", () => {
    renderSources("?sources_mode=connectors")
    fireEvent.click(screen.getByRole("tab", { name: /library/i }))
    expect(new URLSearchParams(window.location.search).get("sources_mode")).toBeNull()
  })

  it("Library mode mounts the existing KnowledgePane", async () => {
    renderSources()
    expect(await screen.findByTestId("kb-stub")).toBeInTheDocument()
  })
})
