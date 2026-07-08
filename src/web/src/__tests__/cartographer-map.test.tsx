// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Vitest tests for the Cartographer map system.
//   1. CartographerMap renders loading state
//   2. CartographerMap renders with mocked fetchGraphMap data without crashing
//   3. Map config persists to localStorage

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { GraphMapResponse } from "@/lib/api/graph-map"

// ---------------------------------------------------------------------------
// Mock sigma — it tries to create a WebGL context in jsdom which isn't
// supported. Mock before any sigma imports.
// ---------------------------------------------------------------------------

vi.mock("sigma", () => {
  class MockCaptor {
    on = vi.fn()
    off = vi.fn()
  }
  class MockSigma {
    kill = vi.fn()
    refresh = vi.fn()
    on = vi.fn()
    off = vi.fn()
    setSetting = vi.fn()
    getMouseCaptor = vi.fn(() => new MockCaptor())
    getGraph = vi.fn(() => ({
      hasNode: vi.fn(() => false),
      forEachNeighbor: vi.fn(),
      forEachEdge: vi.fn(),
      source: vi.fn(() => "src"),
      target: vi.fn(() => "tgt"),
    }))
    getCamera = vi.fn(() => ({
      ratio: 1.5,
      getState: vi.fn(() => ({ x: 0, y: 0, ratio: 1, angle: 0 })),
      on: vi.fn(),
      off: vi.fn(),
    }))
    getContainer = vi.fn(() => {
      const div = document.createElement("div")
      Object.defineProperty(div, "offsetWidth", { get: () => 800 })
      Object.defineProperty(div, "offsetHeight", { get: () => 600 })
      return div
    })
    graphToViewport = vi.fn(({ x, y }: { x: number; y: number }) => ({ x, y }))
    viewportToFramedGraph = vi.fn(({ x, y }: { x: number; y: number }) => ({ x, y }))
    getNodeDisplayData = vi.fn(() => ({ x: 0, y: 0 }))
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    constructor(_graph: unknown, _container: unknown, _settings?: unknown) {}
  }
  return { default: MockSigma }
})

vi.mock("graphology", () => {
  class MockGraph {
    addNode = vi.fn()
    addEdgeWithKey = vi.fn()
    hasNode = vi.fn(() => false)
    hasEdge = vi.fn(() => false)
    forEachNeighbor = vi.fn()
    forEachEdge = vi.fn()
    source = vi.fn(() => "src")
    target = vi.fn(() => "tgt")
    order = 0
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    constructor(_opts?: unknown) {}
  }
  return { default: MockGraph }
})

// ---------------------------------------------------------------------------
// Mock fetchGraphMap
// ---------------------------------------------------------------------------

const mockFetchGraphMap = vi.fn()

vi.mock("@/lib/api/graph-map", () => ({
  fetchGraphMap: (...args: unknown[]) => mockFetchGraphMap(...args),
}))

// Mock @sigma/edge-curve and @sigma/node-border — they call WebGL APIs at
// module-load time which are not available in jsdom.
vi.mock("@sigma/edge-curve", () => ({
  default: class {},
}))

vi.mock("@sigma/node-border", () => ({
  createNodeBorderProgram: vi.fn(() => class {}),
}))

vi.mock("sigma/rendering", () => ({
  NodeCircleProgram: class {},
  createNodeCompoundProgram: vi.fn(() => class {}),
}))

// Mock FA2Layout worker — Worker is not defined in jsdom; stub the supervisor.
vi.mock("graphology-layout-forceatlas2/worker", () => {
  class MockFA2Layout {
    start = vi.fn()
    stop = vi.fn()
    kill = vi.fn()
    isRunning = vi.fn(() => false)
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    constructor(_graph: unknown, _opts?: unknown) {}
  }
  return { default: MockFA2Layout }
})

// ---------------------------------------------------------------------------
// Static imports (after vi.mock hoisting)
// ---------------------------------------------------------------------------

import { CartographerMap } from "@/components/subjects/constellation/map/CartographerMap"
import { useGraphMap } from "@/components/subjects/constellation/map/use-graph-map"
import { loadMapConfig, saveMapConfig, MAP_CONFIG_DEFAULTS } from "@/components/subjects/constellation/map/map-config"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeGraphMapData(overrides: Partial<GraphMapResponse> = {}): GraphMapResponse {
  return {
    count: 3,
    entities: [
      {
        id: "entity:alpha",
        name: "Alpha",
        x: 0.1,
        y: 0.2,
        z: 0,
        type: "PERSON",
        community: "community-0",
        mention_count: 5,
        trust_state: "verified",
        projection: "umap",
        primary_domain: "research",
      },
      {
        id: "entity:beta",
        name: "Beta",
        x: 0.5,
        y: 0.6,
        z: 0,
        type: "ORG",
        community: "community-0",
        mention_count: 2,
        trust_state: "partial",
        projection: "umap",
        primary_domain: "coding",
      },
      {
        id: "entity:gamma",
        name: "Gamma",
        x: 0.8,
        y: 0.3,
        z: 0,
        type: "LOC",
        community: "community-1",
        mention_count: 1,
        trust_state: "unverified",
        projection: "umap",
        primary_domain: null,
      },
    ],
    links: [
      [0, 1, 3.0, "co_mention"],
      [1, 2, 1.5, "co_mention"],
    ],
    communities: [
      {
        id: "community-0",
        count: 2,
        hull: [
          [0, 0],
          [1, 0],
          [1, 1],
          [0, 1],
        ],
        anchor: [0.5, 0.5],
        label: "Tech Cluster",
        top_hubs: [
          { id: "entity:alpha", name: "Alpha", degree: 3 },
          { id: "entity:beta", name: "Beta", degree: 2 },
        ],
        trust_mix: { verified: 0.5, partial: 0.5 },
      },
    ],
    silhouette: 0.72,
    computed_at: "2026-06-09T00:00:00Z",
    cached: false,
    isolated_count: 0,
    ...overrides,
  }
}

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

const DEFAULT_CONFIG = {
  edgeBudget: "8k" as const,
  labelDensity: "normal" as const,
  territories: "nebula" as const,
  liveLayout: true,
  hideOrphans: false,
  collapseCommunities: true,
}

// ---------------------------------------------------------------------------
// CartographerMap — loading / error / empty states
// ---------------------------------------------------------------------------

describe("CartographerMap — loading state", () => {
  it("renders loading spinner when isLoading=true", () => {
    render(
      <CartographerMap
        lens="cluster"
        typeFilter={new Set()}
        config={DEFAULT_CONFIG}
        data={undefined}
        isLoading={true}
        isError={false}
        onInspect={vi.fn()}
        onCommunityClick={vi.fn()}
      />,
      { wrapper: createWrapper() },
    )
    expect(screen.getByText(/loading knowledge map/i)).toBeTruthy()
  })

  it("renders error state when isError=true", () => {
    render(
      <CartographerMap
        lens="cluster"
        typeFilter={new Set()}
        config={DEFAULT_CONFIG}
        data={undefined}
        isLoading={false}
        isError={true}
        errorMessage="Graph map fetch failed: 503"
        onInspect={vi.fn()}
        onCommunityClick={vi.fn()}
      />,
      { wrapper: createWrapper() },
    )
    expect(screen.getByText(/graph map fetch failed: 503/i)).toBeTruthy()
  })

  it("renders empty state when data has no entities", () => {
    render(
      <CartographerMap
        lens="cluster"
        typeFilter={new Set()}
        config={DEFAULT_CONFIG}
        data={{ ...makeGraphMapData(), entities: [], count: 0, links: [], communities: [] }}
        isLoading={false}
        isError={false}
        onInspect={vi.fn()}
        onCommunityClick={vi.fn()}
      />,
      { wrapper: createWrapper() },
    )
    expect(screen.getByText(/no map data yet/i)).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// CartographerMap — renders with data
// ---------------------------------------------------------------------------

describe("CartographerMap — renders with data", () => {
  it("renders the map application container without crashing", () => {
    const data = makeGraphMapData()
    const { container } = render(
      <CartographerMap
        lens="cluster"
        typeFilter={new Set()}
        config={DEFAULT_CONFIG}
        data={data}
        isLoading={false}
        isError={false}
        onInspect={vi.fn()}
        onCommunityClick={vi.fn()}
      />,
      { wrapper: createWrapper() },
    )
    expect(container.querySelector('[role="application"]')).not.toBeNull()
  })

  it("shows entity count in the stats overlay", () => {
    const data = makeGraphMapData()
    render(
      <CartographerMap
        lens="cluster"
        typeFilter={new Set()}
        config={DEFAULT_CONFIG}
        data={data}
        isLoading={false}
        isError={false}
        onInspect={vi.fn()}
        onCommunityClick={vi.fn()}
      />,
      { wrapper: createWrapper() },
    )
    expect(screen.getByText(/3 entities/)).toBeTruthy()
  })

  it("shows silhouette score when present", () => {
    const data = makeGraphMapData({ silhouette: 0.72 })
    render(
      <CartographerMap
        lens="cluster"
        typeFilter={new Set()}
        config={DEFAULT_CONFIG}
        data={data}
        isLoading={false}
        isError={false}
        onInspect={vi.fn()}
        onCommunityClick={vi.fn()}
      />,
      { wrapper: createWrapper() },
    )
    expect(screen.getByText(/silhouette 0\.72/)).toBeTruthy()
  })

  it("shows cached indicator when data.cached=true", () => {
    const data = makeGraphMapData({ cached: true })
    render(
      <CartographerMap
        lens="cluster"
        typeFilter={new Set()}
        config={DEFAULT_CONFIG}
        data={data}
        isLoading={false}
        isError={false}
        onInspect={vi.fn()}
        onCommunityClick={vi.fn()}
      />,
      { wrapper: createWrapper() },
    )
    expect(screen.getByText(/cached/)).toBeTruthy()
  })

  it("accepts domain lens without crashing", () => {
    const data = makeGraphMapData()
    const { container } = render(
      <CartographerMap
        lens="domain"
        typeFilter={new Set()}
        config={DEFAULT_CONFIG}
        data={data}
        isLoading={false}
        isError={false}
        onInspect={vi.fn()}
        onCommunityClick={vi.fn()}
      />,
      { wrapper: createWrapper() },
    )
    expect(container.querySelector('[role="application"]')).not.toBeNull()
  })

  it("domain lens with null primary_domain falls back gracefully (no crash)", () => {
    const data = makeGraphMapData()
    // gamma has primary_domain: null — domain lens should use domainOther
    const { container } = render(
      <CartographerMap
        lens="domain"
        typeFilter={new Set()}
        config={DEFAULT_CONFIG}
        data={data}
        isLoading={false}
        isError={false}
        onInspect={vi.fn()}
        onCommunityClick={vi.fn()}
      />,
      { wrapper: createWrapper() },
    )
    expect(container.querySelector('[role="application"]')).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// MapConfig — localStorage persistence
// ---------------------------------------------------------------------------

describe("MapConfig — localStorage persistence", () => {
  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    localStorage.clear()
  })

  it("loads defaults when no stored config", () => {
    const cfg = loadMapConfig()
    expect(cfg).toEqual(MAP_CONFIG_DEFAULTS)
  })

  it("persists config to localStorage via saveMapConfig", () => {
    saveMapConfig({ edgeBudget: "2k", labelDensity: "sparse", territories: "off" as const, liveLayout: false, hideOrphans: false, collapseCommunities: false })
    const loaded = loadMapConfig()
    expect(loaded.edgeBudget).toBe("2k")
    expect(loaded.labelDensity).toBe("sparse")
    expect(loaded.territories).toBe("off")
  })

  it("round-trips all config fields", () => {
    const config = { edgeBudget: "all" as const, labelDensity: "rich" as const, territories: "nebula" as const, liveLayout: true, hideOrphans: false, collapseCommunities: true }
    saveMapConfig(config)
    expect(loadMapConfig()).toEqual(config)
  })
})

// ---------------------------------------------------------------------------
// useGraphMap hook
// ---------------------------------------------------------------------------

describe("useGraphMap — fetches and exposes drainNewIds", () => {
  beforeEach(() => {
    mockFetchGraphMap.mockResolvedValue(makeGraphMapData())
  })

  it("returns query data after fetch resolves", async () => {
    const { result } = renderHook(() => useGraphMap(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toBeDefined()
    expect(result.current.data?.entities.length).toBe(3)
  })

  it("drainNewIds returns a Set", async () => {
    const { result } = renderHook(() => useGraphMap(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const newIds = result.current.drainNewIds()
    expect(newIds instanceof Set).toBe(true)
  })
})
