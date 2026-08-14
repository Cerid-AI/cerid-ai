// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Phase B Day 8 — Sources pane integration tests. Verifies the
// mode switcher, URL state via ?sources_mode=, and lazy-loaded
// KnowledgePane mount.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import SourcesPane from "@/components/sources/sources-pane"
import { NavigationProvider, useNavigation } from "@/contexts/navigation-context"

// Stub KnowledgePane so jsdom doesn't try to mount its sub-tree (which
// includes graph-explorer that pulls sigma).
vi.mock("@/components/kb/knowledge-pane", () => ({
  default: () => <div data-testid="kb-stub">KB Library</div>,
}))

// Stub the Activity/Connectors lazy children for the axe-clean suite below:
// these have their own axe coverage (e.g. sources-connectors.test.tsx), so
// this pane-level suite only needs to assert the PANE shell around them is
// axe-clean past the Suspense fallback, not re-exercise their internals.
vi.mock("@/components/sources/activity-stream", () => ({
  SourcesActivityStream: () => <div data-testid="activity-stub">Activity Stream</div>,
}))
vi.mock("@/components/sources/sources-connectors", () => ({
  SourcesConnectors: () => <div data-testid="connectors-stub">Connectors</div>,
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
    // Connectors panel lazy-loads (stubbed below); prove it mounted.
    expect(await screen.findByTestId("connectors-stub")).toBeInTheDocument()
  })

  it("switching mode writes ?sources_mode= and renders that panel", async () => {
    renderSources()
    fireEvent.click(screen.getByRole("tab", { name: /activity/i }))
    expect(new URLSearchParams(window.location.search).get("sources_mode")).toBe("activity")
    // Activity panel lazy-loads (stubbed below); prove it mounted.
    expect(await screen.findByTestId("activity-stub")).toBeInTheDocument()
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

  it("same-pane goTo(sources, { sourcesMode }) switches the sub-tab (deep-link mechanism)", async () => {
    // The mount initializer reads ?sources_mode= exactly once; the navVersion
    // subscription is what makes a goTo work while the pane is already active.
    function GoToActivity() {
      const { goTo } = useNavigation()
      return (
        <button type="button" onClick={() => goTo("sources", { sourcesMode: "activity" })}>
          go-activity
        </button>
      )
    }
    window.history.replaceState({}, "", "/")
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <NavigationProvider activePane="sources" onPaneChange={() => {}}>
          <GoToActivity />
          <SourcesPane />
        </NavigationProvider>
      </QueryClientProvider>,
    )
    expect(screen.getByRole("tab", { name: /library/i })).toHaveAttribute("aria-selected", "true")
    fireEvent.click(screen.getByText("go-activity"))
    expect(screen.getByRole("tab", { name: /activity/i })).toHaveAttribute("aria-selected", "true")
    expect(await screen.findByTestId("activity-stub")).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// axe-clean — one assertion per visually-distinct mode (this pane is a mode
// switcher, not a fetch/loading/error/empty pane; the four modes are its
// distinct states).
// ---------------------------------------------------------------------------

describe("SourcesPane — axe-clean", () => {
  it("is axe-clean in Library mode (default)", async () => {
    const { container } = renderSources()
    await screen.findByTestId("kb-stub")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in Activity mode", async () => {
    const { container } = renderSources()
    fireEvent.click(screen.getByRole("tab", { name: /activity/i }))
    // Wait for the settled (stubbed) child, not the Suspense fallback —
    // otherwise axe would only ever evaluate the generic loading spinner.
    await screen.findByTestId("activity-stub")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in Connectors mode", async () => {
    const { container } = renderSources("?sources_mode=connectors")
    // Wait for the settled (stubbed) child, not the Suspense fallback —
    // otherwise axe would only ever evaluate the generic loading spinner.
    await screen.findByTestId("connectors-stub")
    expect(await axe(container)).toHaveNoViolations()
  })
})
