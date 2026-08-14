// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Subjects pane integration tests — Cycle 4 Strata edition.
// Covers mode switcher, URL persistence, atlas sub-mode routing,
// and Cycle 4 click-contract behavior.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import SubjectsPane from "@/components/subjects/subjects-pane"
import { NavigationProvider } from "@/contexts/navigation-context"

const mockListAtlasViews = vi.fn()

vi.mock("@/lib/api/atlas-views", () => ({
  listAtlasViews: (...a: unknown[]) => mockListAtlasViews(...a),
}))

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

// Stub GraphExplorer (RA-11) — avoids the real useCommunities fetch cascade.
vi.mock("@/components/kb/graph-explorer", () => ({
  GraphExplorer: () => <div data-testid="communities-stub">Communities mode</div>,
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
  mockListAtlasViews.mockReset()
  mockListAtlasViews.mockResolvedValue([])
})

// ---------------------------------------------------------------------------
// Mode switcher
// ---------------------------------------------------------------------------

describe("SubjectsPane — mode switcher", () => {
  it("renders all five mode tabs in the segmented control", () => {
    renderSubjects()
    expect(screen.getByRole("tab", { name: /atlas/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /constellation/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /timeline/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /wiki/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /communities/i })).toBeInTheDocument()
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

  // RA-11: GraphExplorer (Leiden community explorer, Phase R.2) was shipped
  // but reachable only from its own test — restored here as a Subjects mode.
  it("switching to communities mode writes ?mode=communities and mounts GraphExplorer", async () => {
    renderSubjects()
    fireEvent.click(screen.getByRole("tab", { name: /communities/i }))
    expect(new URLSearchParams(window.location.search).get("mode")).toBe("communities")
    expect(await screen.findByTestId("communities-stub")).toBeInTheDocument()
  })

  it("starts on communities mode when ?mode=communities", async () => {
    renderSubjects("?mode=communities")
    expect(screen.getByRole("tab", { name: /communities/i })).toHaveAttribute("aria-selected", "true")
    expect(await screen.findByTestId("communities-stub")).toBeInTheDocument()
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

// ---------------------------------------------------------------------------
// Saved-views badge (WB-21) — unknown count must never render as "0".
// ---------------------------------------------------------------------------

describe("SubjectsPane — saved-views badge", () => {
  it("shows the resolved count once the views query succeeds", async () => {
    mockListAtlasViews.mockResolvedValue([
      { view_id: "v1", name: "a" },
      { view_id: "v2", name: "b" },
    ])
    renderSubjects()
    const button = await screen.findByRole("button", { name: "Saved views (2)" })
    expect(button).toBeInTheDocument()
    expect(screen.getByText("2")).toBeInTheDocument()
  })

  it("suppresses the numeric badge and aria-label count while the views query is pending", () => {
    mockListAtlasViews.mockReturnValue(new Promise(() => {})) // never resolves
    renderSubjects()
    expect(screen.getByRole("button", { name: "Saved views" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Saved views \(/ })).not.toBeInTheDocument()
  })

  it("suppresses the numeric badge and aria-label count when the views query fails, rather than reporting 0", async () => {
    mockListAtlasViews.mockRejectedValue(new Error("fail"))
    renderSubjects()
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Saved views" })).toBeInTheDocument(),
    )
    expect(screen.queryByRole("button", { name: /Saved views \(0\)/ })).not.toBeInTheDocument()
    expect(screen.queryByText("0")).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// axe-clean — one assertion per visually-distinct mode/sub-mode this pane
// exercises elsewhere in this suite (mode switcher, not a fetch/loading/
// error/empty pane). Constellation/Timeline are excluded — they mount real
// three.js/heavy sub-trees not stubbed in this suite.
// ---------------------------------------------------------------------------

describe("SubjectsPane — axe-clean", () => {
  it("is axe-clean in Atlas overview mode (default)", async () => {
    const { container } = renderSubjects()
    expect(screen.getByTestId("decomposition-icicle-stub")).toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in Atlas neighborhood mode (?entity=)", async () => {
    const { container } = renderSubjects("?entity=alex-smith")
    expect(screen.getByTestId("atlas-neighborhood-stub")).toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in Wiki mode", async () => {
    const { container } = renderSubjects("?mode=wiki")
    await screen.findByTestId("wiki-stub")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in Communities mode", async () => {
    const { container } = renderSubjects("?mode=communities")
    await screen.findByTestId("communities-stub")
    expect(await axe(container)).toHaveNoViolations()
  })
})
