// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Subjects pane integration tests — Cycle 4 Strata edition.
// Covers mode switcher, URL persistence, atlas sub-mode routing,
// and Cycle 4 click-contract behavior.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import SubjectsPane from "@/components/subjects/subjects-pane"
import { NavigationProvider } from "@/contexts/navigation-context"

// Stub Atlas (sigma) to avoid WebGL in jsdom.
vi.mock("@/components/subjects/atlas/Atlas", () => ({
  Atlas: ({ onBackToOverview }: { onBackToOverview?: () => void }) => (
    <div data-testid="atlas-neighborhood-stub">
      Atlas neighborhood mode
      {onBackToOverview && (
        <button type="button" data-testid="back-to-overview-btn" onClick={onBackToOverview}>
          Back to overview
        </button>
      )}
    </div>
  ),
}))

// Stub DecompositionIcicle — avoids the full TanStack Query cascade.
vi.mock("@/components/subjects/atlas/decomposition", () => ({
  DecompositionIcicle: ({ onOpenNeighborhood }: { onOpenNeighborhood?: (id: string) => void }) => (
    <div data-testid="decomposition-icicle-stub">
      Decomposition overview
      {onOpenNeighborhood && (
        <button
          type="button"
          data-testid="open-neighborhood-btn"
          onClick={() => onOpenNeighborhood("ent-xyz")}
        >
          Open neighborhood
        </button>
      )}
    </div>
  ),
}))

// Stub Wiki for the same reason (lazy imports markdown deps).
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

// ---------------------------------------------------------------------------
// Mode switcher
// ---------------------------------------------------------------------------

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
    expect(await screen.findByTestId("wiki-stub")).toBeInTheDocument()
  })

  it("Constellation tab is enabled", () => {
    renderSubjects()
    expect(screen.getByRole("tab", { name: /constellation/i })).not.toBeDisabled()
  })

  it("Timeline tab is enabled", () => {
    renderSubjects()
    expect(screen.getByRole("tab", { name: /timeline/i })).not.toBeDisabled()
  })

  it("switching to wiki mode writes ?mode=wiki", async () => {
    renderSubjects()
    fireEvent.click(screen.getByRole("tab", { name: /wiki/i }))
    expect(new URLSearchParams(window.location.search).get("mode")).toBe("wiki")
    expect(await screen.findByTestId("wiki-stub")).toBeInTheDocument()
  })

  it("switching back to atlas clears ?mode=", () => {
    renderSubjects("?mode=wiki")
    fireEvent.click(screen.getByRole("tab", { name: /atlas/i }))
    expect(new URLSearchParams(window.location.search).get("mode")).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Atlas sub-mode routing (Cycle 4 STRATA)
// ---------------------------------------------------------------------------

describe("SubjectsPane — Atlas sub-mode routing", () => {
  it("Atlas mode WITHOUT ?entity= shows the decomposition icicle overview", () => {
    renderSubjects()
    expect(screen.getByTestId("decomposition-icicle-stub")).toBeInTheDocument()
    expect(screen.queryByTestId("atlas-neighborhood-stub")).toBeNull()
  })

  it("Atlas mode WITH ?entity= shows Neighborhood mode directly (E-17 contract)", () => {
    renderSubjects("?entity=alex-smith")
    expect(screen.getByTestId("atlas-neighborhood-stub")).toBeInTheDocument()
    expect(screen.queryByTestId("decomposition-icicle-stub")).toBeNull()
  })

  it("clicking 'Open neighborhood' in icicle switches to Neighborhood mode", () => {
    renderSubjects()
    // Overview is showing
    expect(screen.getByTestId("decomposition-icicle-stub")).toBeInTheDocument()
    // Click the open-neighborhood button (stub exposes it)
    fireEvent.click(screen.getByTestId("open-neighborhood-btn"))
    // Should switch to neighborhood
    expect(screen.getByTestId("atlas-neighborhood-stub")).toBeInTheDocument()
    expect(screen.queryByTestId("decomposition-icicle-stub")).toBeNull()
  })

  it("clicking 'Back to overview' from Neighborhood returns to decomposition", () => {
    renderSubjects("?entity=alex-smith")
    // Neighborhood is showing
    expect(screen.getByTestId("atlas-neighborhood-stub")).toBeInTheDocument()
    // Click back
    fireEvent.click(screen.getByTestId("back-to-overview-btn"))
    // Should switch back to overview
    expect(screen.getByTestId("decomposition-icicle-stub")).toBeInTheDocument()
    expect(screen.queryByTestId("atlas-neighborhood-stub")).toBeNull()
  })

  it("does not write ?mode=atlas to URL (atlas is default)", () => {
    renderSubjects()
    expect(new URLSearchParams(window.location.search).get("mode")).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Search palette
// ---------------------------------------------------------------------------

describe("SubjectsPane — search palette trigger", () => {
  it("⌘K-labeled button opens the palette dialog", () => {
    renderSubjects()
    fireEvent.click(screen.getByRole("button", { name: /search subjects/i }))
    expect(screen.getByRole("dialog", { name: /search subjects/i })).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Click contract (Cycle 4)
// ---------------------------------------------------------------------------

describe("SubjectsPane — Cycle 4 click contract", () => {
  it("Atlas neighborhood is shown without mode switch when ?entity= in URL", () => {
    renderSubjects("?entity=some-entity")
    // Must be in Neighborhood mode immediately
    expect(screen.getByTestId("atlas-neighborhood-stub")).toBeInTheDocument()
    // Mode tab must still be atlas
    expect(screen.getByRole("tab", { name: /atlas/i })).toHaveAttribute("aria-selected", "true")
  })

  it("?entity= writes to URL correctly", () => {
    renderSubjects("?entity=my-entity")
    expect(new URLSearchParams(window.location.search).get("entity")).toBe("my-entity")
  })
})
