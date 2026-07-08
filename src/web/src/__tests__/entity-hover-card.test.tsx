// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Tests for Task 4.3: unified hover intent delay + thicker entity hover cards.
//   1. HOVER_INTENT_DELAY_MS is 300ms (unified value).
//   2. Real EntityCard from Atlas.tsx: trust guard + legend text + neighbors.
//   3. Card HTML is axe-clean (jest-axe) on the real component.

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { axe } from "jest-axe"

// ---------------------------------------------------------------------------
// Mock sigma + related WebGL-heavy modules before any Atlas import.
// sigma.js calls WebGL2RenderingContext at module-load time which is
// not available in jsdom. vi.mock calls are hoisted before imports.
// ---------------------------------------------------------------------------

vi.mock("sigma", () => {
  class MockSigma {
    kill = vi.fn()
    refresh = vi.fn()
    on = vi.fn()
    off = vi.fn()
    setSetting = vi.fn()
    getCamera = vi.fn(() => ({ animate: vi.fn(), getState: vi.fn(() => ({ x: 0, y: 0, ratio: 1, angle: 0 })) }))
    getContainer = vi.fn(() => document.createElement("div"))
    getNodeDisplayData = vi.fn(() => null)
    graphToViewport = vi.fn(({ x, y }: { x: number; y: number }) => ({ x, y }))
    getMouseCaptor = vi.fn(() => ({ on: vi.fn(), off: vi.fn() }))
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    constructor(_graph: unknown, _container: unknown, _settings?: unknown) {}
  }
  return { default: MockSigma }
})

vi.mock("@sigma/node-border", () => ({
  createNodeBorderProgram: vi.fn(() => class {}),
}))

vi.mock("@sigma/edge-curve", () => ({
  default: class {},
}))

vi.mock("sigma/rendering", () => ({
  NodeCircleProgram: class {},
  createNodeCompoundProgram: vi.fn(() => class {}),
}))

vi.mock("@/lib/graph/atlas-programs", () => ({
  ATLAS_V2_NODE_PROGRAM_CLASSES: {},
  ATLAS_V2_DEFAULT_NODE_TYPE: "bordered",
  ATLAS_V2_EDGE_PROGRAM_CLASSES: {},
  ATLAS_V2_DEFAULT_EDGE_TYPE: "curved",
}))

vi.mock("@/lib/graph/graphology-adapter", () => ({
  adaptNeighborhood: vi.fn(() => ({ order: 0, hasNode: vi.fn(() => false) })),
  recolorGraph: vi.fn(),
}))

vi.mock("@/lib/graph/apply-layout", () => ({
  applyLayout: vi.fn(() => Promise.resolve()),
}))

vi.mock("@/lib/api/graph", () => ({
  fetchNeighborhood: vi.fn(),
}))

vi.mock("@/lib/graph/draw-node-hover", () => ({
  makeDrawNodeHover: vi.fn(() => vi.fn()),
}))

vi.mock("@/lib/graph/lenses", () => ({
  composeLensesWithTokens: vi.fn(() => ({ nodeReducer: null, edgeReducer: null })),
  LENS_ORDER: [],
}))

vi.mock("@/lib/graph/identity", () => ({
  resolveMapTokens: vi.fn(() => ({})),
  applyParallelEdgeCurvature: vi.fn(),
}))

vi.mock("@/components/subjects/atlas/use-atlas-keyboard", () => ({
  useAtlasKeyboard: vi.fn(() => ({ selectedNodeId: null, setSelectedNodeId: vi.fn(), onKeyDown: vi.fn() })),
}))

vi.mock("@/components/subjects/atlas/atlas-a11y-tree", () => ({
  AtlasA11yTree: vi.fn(() => null),
}))

vi.mock("@/components/subjects/atlas/atlas-context-menu", () => ({
  AtlasContextMenu: vi.fn(() => null),
}))

vi.mock("@/components/subjects/atlas/atlas-saved-views", () => ({
  AtlasSavedViews: vi.fn(() => null),
}))

vi.mock("@/lib/graph/cycle4-contracts", () => ({
  NEIGHBORHOOD_HOPS_MAX_PROMOTED: 2,
}))

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...actual,
    useQuery: vi.fn(() => ({ data: undefined, isLoading: false, isFetching: false, isError: false, error: null })),
  }
})

// ---------------------------------------------------------------------------
// Static imports (after vi.mock hoisting)
// ---------------------------------------------------------------------------

import { HOVER_INTENT_DELAY_MS } from "@/lib/graph/hover-intent"
import { EntityCard } from "@/components/subjects/atlas/Atlas"
import type { AtlasNodeAttributes } from "@/lib/types/graph"
import type { MapTokens } from "@/components/subjects/constellation/map/community-layer"

// ---------------------------------------------------------------------------
// 1. Unified hover intent delay
// ---------------------------------------------------------------------------

describe("HOVER_INTENT_DELAY_MS", () => {
  it("is 300ms (unified value used by both Atlas and Cartographer)", () => {
    expect(HOVER_INTENT_DELAY_MS).toBe(300)
  })
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeAttrs(overrides: Partial<AtlasNodeAttributes> = {}): AtlasNodeAttributes {
  return {
    id: "entity:alpha",
    name: "Alpha Entity",
    type: "bordered",
    entityType: "Person",
    mention_count: 5,
    trust_state: "verified",
    community: null,
    recency_score: 0.5,
    focused: false,
    x: 0,
    y: 0,
    size: 8,
    label: "Alpha Entity",
    color: "#888888",
    haloColor: "#888888",
    pulseIntensity: 0.5,
    ...overrides,
  }
}

const STUB_TOKENS: MapTokens = {
  clusters: Array(8).fill("#888888") as string[],
  clusterOther: "#888888",
  domains: Array(12).fill("#888888") as string[],
  domainOther: "#666666",
  edge: "#888888",
  dim: "#888888",
  interaction: "#00C8B4",
  foreground: "#111111",
  background: "#f5f5f5",
  trustVerified: "#555555",
  trustPartial: "#777777",
  trustUnverified: "#999999",
  graphite: "#6b7080", // drift-allowed: test stub only, never reaches browser
  grid: "#eeeeee", // drift-allowed: test stub only, never reaches browser
  fontSans: "system-ui, sans-serif",
}

const NOOP = () => undefined

function renderCard(attrs: AtlasNodeAttributes, pinned = false) {
  return render(
    <EntityCard
      nodeId={attrs.id}
      attrs={attrs}
      screenPos={{ x: 100, y: 100 }}
      tokens={STUB_TOKENS}
      graph={null}
      onOpenWiki={NOOP}
      onOpenTimeline={NOOP}
      onMakeFocal={NOOP}
      onCiteInChat={NOOP}
      onClose={NOOP}
      pinned={pinned}
    />,
  )
}

// ---------------------------------------------------------------------------
// 2. Real EntityCard content assertions
// ---------------------------------------------------------------------------

describe("EntityCard (real component) — trust guard", () => {
  it("renders trust row when trust_state is 'verified'", () => {
    renderCard(makeAttrs({ trust_state: "verified" }))
    expect(screen.getByTestId("entity-card-trust")).toBeTruthy()
    expect(screen.getByTestId("entity-card-trust").getAttribute("aria-label")).toBe("Trust: verified")
  })

  it("renders trust row when trust_state is 'partial'", () => {
    renderCard(makeAttrs({ trust_state: "partial" }))
    expect(screen.getByTestId("entity-card-trust")).toBeTruthy()
  })

  it("renders trust row when trust_state is 'unverified'", () => {
    renderCard(makeAttrs({ trust_state: "unverified" }))
    expect(screen.getByTestId("entity-card-trust")).toBeTruthy()
  })

  it("renders trust row when trust_state is 'contradicted'", () => {
    renderCard(makeAttrs({ trust_state: "contradicted" }))
    expect(screen.getByTestId("entity-card-trust")).toBeTruthy()
    expect(screen.getByTestId("entity-card-trust").getAttribute("aria-label")).toBe("Trust: contradicted")
  })

  it("OMITS trust row when trust_state is 'unknown'", () => {
    renderCard(makeAttrs({ trust_state: "unknown" }))
    expect(screen.queryByTestId("entity-card-trust")).toBeNull()
  })

  it("legend text includes 'contradicted'", () => {
    const { container } = renderCard(makeAttrs({ trust_state: "verified" }))
    expect(container.textContent).toContain("contradicted")
  })
})

describe("EntityCard (real component) — degree + role", () => {
  it("renders 0 connections when graph is null", () => {
    renderCard(makeAttrs())
    expect(screen.getByTestId("entity-card-degree").textContent).toContain("0 connections")
  })

  it("tooltip role for unpinned card", () => {
    const { container } = renderCard(makeAttrs(), false)
    expect(container.querySelector('[role="tooltip"]')).not.toBeNull()
  })

  it("dialog role for pinned card", () => {
    const { container } = renderCard(makeAttrs(), true)
    expect(container.querySelector('[role="dialog"]')).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 3. Axe accessibility — real EntityCard
// ---------------------------------------------------------------------------

describe("EntityCard (real component) axe accessibility", () => {
  it("hover card (role=tooltip, verified trust) is axe-clean", async () => {
    const { container } = renderCard(makeAttrs({ trust_state: "verified" }), false)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const results = await axe(container as any)
    expect(results).toHaveNoViolations()
  })

  it("pinned card (role=dialog, partial trust) is axe-clean", async () => {
    const { container } = renderCard(makeAttrs({ trust_state: "partial" }), true)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const results = await axe(container as any)
    expect(results).toHaveNoViolations()
  })

  it("card with unknown trust (row omitted) is axe-clean", async () => {
    const { container } = renderCard(makeAttrs({ trust_state: "unknown" }), false)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const results = await axe(container as any)
    expect(results).toHaveNoViolations()
  })

  it("card with contradicted trust is axe-clean", async () => {
    const { container } = renderCard(makeAttrs({ trust_state: "contradicted" }), false)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const results = await axe(container as any)
    expect(results).toHaveNoViolations()
  })
})
