// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Phase A Day 13 — Subjects pane integration tests.
// Verifies mode switcher, URL persistence, and placeholder modes
// without mounting the WebGL-dependent Atlas component (sigma's
// WebGL2RenderingContext reference breaks jsdom). Atlas is lazy-
// loaded inside SubjectsPane via lazy()+Suspense for Wiki and
// inline-imported for Atlas mode; we cover Constellation, Timeline,
// and Wiki modes here. The Atlas-mode rendering path is exercised
// by chrome-devtools-mcp against the perf harness.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import SubjectsPane from "@/components/subjects/subjects-pane"
import { NavigationProvider } from "@/contexts/navigation-context"

// Stub Atlas to avoid pulling sigma into the jsdom test bundle.
vi.mock("@/components/subjects/atlas/Atlas", () => ({
  Atlas: () => <div data-testid="atlas-stub">Atlas mode</div>,
}))

// Stub Wiki for the same reason (it lazy-imports markdown deps).
vi.mock("@/components/wiki/wiki-pane", () => ({
  default: () => <div data-testid="wiki-stub">Wiki mode</div>,
}))

function renderSubjects(initialSearch = "") {
  window.history.replaceState({}, "", `/${initialSearch}`)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <NavigationProvider activePane="subjects" onPaneChange={() => {}}>
        <SubjectsPane />
      </NavigationProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  window.history.replaceState({}, "", "/")
})

describe("SubjectsPane — mode switcher", () => {
  it("renders all four mode tabs in the segmented control", () => {
    renderSubjects()
    expect(screen.getByRole("tab", { name: /atlas/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /constellation/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /timeline/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /wiki/i })).toBeInTheDocument()
  })

  it("defaults to Atlas mode when no ?mode= present", () => {
    renderSubjects()
    expect(screen.getByRole("tab", { name: /atlas/i })).toHaveAttribute("aria-selected", "true")
  })

  it("starts on the mode named in ?mode=", async () => {
    renderSubjects("?mode=wiki")
    expect(screen.getByRole("tab", { name: /wiki/i })).toHaveAttribute("aria-selected", "true")
    // Wiki pane is lazy-loaded → Suspense fallback first, then stub
    expect(await screen.findByTestId("wiki-stub")).toBeInTheDocument()
  })

  it("Constellation tab is enabled (Phase B Day 1)", () => {
    renderSubjects()
    const btn = screen.getByRole("tab", { name: /constellation/i })
    expect(btn).not.toBeDisabled()
  })

  it("Timeline tab is enabled (Phase M Day 3)", () => {
    renderSubjects()
    const btn = screen.getByRole("tab", { name: /timeline/i })
    expect(btn).not.toBeDisabled()
  })

  it("switching to wiki mode writes ?mode=wiki and renders Wiki", async () => {
    renderSubjects()
    fireEvent.click(screen.getByRole("tab", { name: /wiki/i }))
    expect(new URLSearchParams(window.location.search).get("mode")).toBe("wiki")
    expect(await screen.findByTestId("wiki-stub")).toBeInTheDocument()
  })

  it("switching back to atlas clears ?mode= (atlas is default)", () => {
    renderSubjects("?mode=wiki")
    fireEvent.click(screen.getByRole("tab", { name: /atlas/i }))
    expect(new URLSearchParams(window.location.search).get("mode")).toBeNull()
  })
})

describe("SubjectsPane — placeholder modes", () => {
  it("Atlas mode without focal entity shows the search-prompt CTA", () => {
    renderSubjects()
    expect(screen.getByText(/pick a starting point/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /open search palette/i })).toBeInTheDocument()
  })

  it("Atlas mode with ?entity= renders the Atlas component", () => {
    renderSubjects("?entity=alex")
    expect(screen.getByTestId("atlas-stub")).toBeInTheDocument()
  })
})

describe("SubjectsPane — search palette trigger", () => {
  it("⌘K-labeled button opens the palette dialog", () => {
    renderSubjects()
    const trigger = screen.getByRole("button", { name: /search subjects/i })
    fireEvent.click(trigger)
    expect(screen.getByRole("dialog", { name: /search subjects/i })).toBeInTheDocument()
  })
})
