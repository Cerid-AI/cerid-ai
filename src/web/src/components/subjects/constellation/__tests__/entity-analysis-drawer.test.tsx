// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// CN2 — Constellation node click opens the entity analysis drawer.
//
// Scope: drawer open/close + entity-slug wiring through SubjectsPane.
// The data-heavy children are mocked:
//   - EntityDetailView (markdown + charts + MiniGraph/sigma) → light stub
//     that just echoes its `slug` and exposes an onSelectRelated trigger.
//   - Constellation (R3F + sigma) → light stub with buttons that call
//     onNodeClick(<id>), standing in for clicking a graph node.
// A full sigma render cannot run under jsdom (WebGL2), so this verifies the
// wiring contract, not the live graph render. Live behavior is browser-verified.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import SubjectsPane from "@/components/subjects/subjects-pane"
import { NavigationProvider } from "@/contexts/navigation-context"

// Stub the rich detail view so the drawer mounts without markdown/chart/sigma.
vi.mock("@/components/wiki/entity-detail-view", () => ({
  EntityDetailView: ({
    slug,
    onSelectRelated,
  }: {
    slug: string
    onSelectRelated: (s: string) => void
  }) => (
    <div data-testid="entity-detail-stub">
      <span data-testid="detail-slug">{slug}</span>
      <button
        type="button"
        data-testid="select-related-btn"
        onClick={() => onSelectRelated("related-entity")}
      >
        related
      </button>
    </div>
  ),
}))

// Stub Constellation (R3F + sigma) — expose two "nodes" that call onNodeClick.
vi.mock("@/components/subjects/constellation/Constellation", () => ({
  default: ({ onNodeClick }: { onNodeClick?: (id: string) => void }) => (
    <div data-testid="constellation-stub">
      <button type="button" data-testid="node-alpha" onClick={() => onNodeClick?.("alpha")}>
        alpha
      </button>
      <button type="button" data-testid="node-beta" onClick={() => onNodeClick?.("beta")}>
        beta
      </button>
    </div>
  ),
}))

// Keep Atlas / icicle / wiki stubs so unrelated modes don't pull heavy deps.
vi.mock("@/components/subjects/atlas/Atlas", () => ({
  Atlas: () => <div data-testid="atlas-neighborhood-stub">Atlas</div>,
}))
vi.mock("@/components/subjects/atlas/decomposition", () => ({
  DecompositionIcicle: () => <div data-testid="decomposition-icicle-stub">Overview</div>,
}))
vi.mock("@/components/wiki/wiki-pane", () => ({
  default: () => <div data-testid="wiki-stub">Wiki</div>,
}))

function renderSubjects(initialSearch = "?mode=constellation") {
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

describe("CN2 — Constellation analysis drawer", () => {
  it("drawer is closed until a node is clicked", async () => {
    renderSubjects()
    expect(await screen.findByTestId("constellation-stub")).toBeInTheDocument()
    expect(screen.queryByTestId("entity-detail-stub")).toBeNull()
  })

  it("clicking a node opens the drawer with that entity's slug", async () => {
    renderSubjects()
    fireEvent.click(await screen.findByTestId("node-alpha"))
    expect(await screen.findByTestId("entity-detail-stub")).toBeInTheDocument()
    expect(screen.getByTestId("detail-slug")).toHaveTextContent("alpha")
  })

  it("clicking a different node re-targets the drawer to the new entity", async () => {
    renderSubjects()
    fireEvent.click(await screen.findByTestId("node-alpha"))
    expect(await screen.findByTestId("detail-slug")).toHaveTextContent("alpha")
    fireEvent.click(screen.getByTestId("node-beta"))
    await waitFor(() =>
      expect(screen.getByTestId("detail-slug")).toHaveTextContent("beta"),
    )
  })

  it("a related-entity selection re-targets the drawer in place", async () => {
    renderSubjects()
    fireEvent.click(await screen.findByTestId("node-alpha"))
    fireEvent.click(await screen.findByTestId("select-related-btn"))
    await waitFor(() =>
      expect(screen.getByTestId("detail-slug")).toHaveTextContent("related-entity"),
    )
  })

  it("Escape closes the drawer", async () => {
    renderSubjects()
    fireEvent.click(await screen.findByTestId("node-alpha"))
    const drawer = await screen.findByTestId("entity-analysis-drawer")
    fireEvent.keyDown(drawer, { key: "Escape", code: "Escape" })
    await waitFor(() =>
      expect(screen.queryByTestId("entity-detail-stub")).toBeNull(),
    )
  })

  it("the constellation stub stays mounted while the drawer opens (no remount)", async () => {
    renderSubjects()
    const before = await screen.findByTestId("constellation-stub")
    fireEvent.click(screen.getByTestId("node-alpha"))
    expect(await screen.findByTestId("entity-detail-stub")).toBeInTheDocument()
    // Same DOM node instance — opening the portalled drawer did not remount it.
    expect(screen.getByTestId("constellation-stub")).toBe(before)
  })
})
