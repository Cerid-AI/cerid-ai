// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for the graphology adapter (Meridian identity pipeline).
// Pure-logic tests; no DOM / network.

import { describe, expect, it } from "vitest"
import type { MapTokens } from "@/components/subjects/constellation/map/community-layer"
import type { NeighborhoodResponse } from "@/lib/types/graph"
import { __TESTING__, adaptNeighborhood, recolorGraph } from "./graphology-adapter"

const {
  nodeSize,
  edgeWidth,
  truncateLabel,
  pulseIntensity,
} = __TESTING__

// ---------------------------------------------------------------------------
// Stub tokens — hex values so normalizeColor isn't needed in pure unit tests
// ---------------------------------------------------------------------------

const TOKENS: MapTokens = {
  clusters: [
    "#A0A0FF", "#A0FFA0", "#FFA0A0", "#FFFF80", // drift-allowed: test stub only
    "#FF80FF", "#80FFFF", "#C0C0FF", "#FFC080", // drift-allowed: test stub only
  ],
  clusterOther: "#888888", // drift-allowed: test stub only, never reaches the design system
  domains: [
    "#D10000", "#CC4400", "#AA8800", "#558800", // drift-allowed: test stub only (slots 0-3)
    "#008844", "#007755", "#006688", "#2244AA", // drift-allowed: test stub only (slots 4-7)
    "#4400AA", "#770088", "#AA0066", "#CC0033", // drift-allowed: test stub only (slots 8-11)
  ],
  domainOther:   "#666666", // drift-allowed: test stub only
  edge:          "#CCCCCC", // drift-allowed: test stub only
  dim:           "#666666", // drift-allowed: test stub only
  interaction:   "#00C8B4", // drift-allowed: test stub only
  foreground:    "#111111", // drift-allowed: test stub only
  background:    "#FFFFFF", // drift-allowed: test stub only
  trustVerified:   "#4488FF", // drift-allowed: test stub only
  trustPartial:    "#FFAA44", // drift-allowed: test stub only
  trustUnverified: "#FF4444", // drift-allowed: test stub only
  graphite:        "#6b7080", // drift-allowed: test stub only
  grid:          "#EEEEEE", // drift-allowed: test stub only
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mkResponse(overrides: Partial<NeighborhoodResponse> = {}): NeighborhoodResponse {
  return {
    focal_entity: "alex",
    nodes: [
      {
        id: "alex",
        name: "Alex Chen",
        type: "Person",
        community: "c1",
        mention_count: 47,
        trust_state: "verified",
        recency_score: 0.92,
        focused: true,
      },
      {
        id: "api_redesign",
        name: "API Redesign",
        type: "Project",
        community: "c1",
        mention_count: 23,
        trust_state: "verified",
        recency_score: 0.85,
        focused: false,
      },
    ],
    edges: [
      {
        source: "alex",
        target: "api_redesign",
        type: "works_on",
        weight: 1.4,
        attestation: "attested",
        contradiction: false,
      },
    ],
    truncated: false,
    cached: false,
    isolated_count: 0,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Sizing
// ---------------------------------------------------------------------------

describe("nodeSize", () => {
  it("floors at 6 for zero mentions", () => {
    expect(nodeSize(0)).toBe(6)
  })

  it("grows with mention count (sqrt ramp)", () => {
    const small = nodeSize(1)
    const mid = nodeSize(25)
    const big = nodeSize(100)
    expect(small).toBeLessThan(mid)
    expect(mid).toBeLessThan(big)
    // sqrt scaling: 100 mentions should NOT be 10x 10 mentions
    expect(big / mid).toBeLessThan(3)
  })

  it("caps at 18px regardless of huge mention counts", () => {
    expect(nodeSize(100_000)).toBe(18)
  })

  it("ignores negative mention counts (defensive)", () => {
    expect(nodeSize(-5)).toBe(6)
  })
})

describe("edgeWidth", () => {
  it("floors at 0.4 for zero weight", () => {
    expect(edgeWidth(0)).toBe(0.4)
  })

  it("caps at 4px regardless of huge weights", () => {
    expect(edgeWidth(100_000)).toBe(4)
  })
})

// ---------------------------------------------------------------------------
// Label
// ---------------------------------------------------------------------------

describe("truncateLabel", () => {
  it("preserves short labels", () => {
    expect(truncateLabel("Alex")).toBe("Alex")
  })

  it("truncates long labels with ellipsis", () => {
    const long = "A".repeat(40)
    const truncated = truncateLabel(long)
    expect(truncated).toHaveLength(28)
    expect(truncated.endsWith("…")).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Pulse intensity (halo brightness derivation)
// ---------------------------------------------------------------------------

describe("pulseIntensity", () => {
  function nodeWith(recency: number, focused = false) {
    return {
      id: "x",
      name: "X",
      type: "Person",
      community: null,
      mention_count: 0,
      trust_state: "verified" as const,
      recency_score: recency,
      focused,
    }
  }

  it("floors at 0.25 for stale entities so halo still glints", () => {
    expect(pulseIntensity(nodeWith(0))).toBe(0.25)
    expect(pulseIntensity(nodeWith(0.1))).toBe(0.25)
  })

  it("passes through mid-range recency", () => {
    expect(pulseIntensity(nodeWith(0.5))).toBe(0.5)
    expect(pulseIntensity(nodeWith(0.7))).toBeCloseTo(0.7, 5)
  })

  it("caps at 1.0 even for max recency", () => {
    expect(pulseIntensity(nodeWith(1.0))).toBe(1)
    expect(pulseIntensity(nodeWith(99))).toBe(1)
  })

  it("boosts focused entities by 40%, clamped", () => {
    expect(pulseIntensity(nodeWith(0.5, true))).toBeCloseTo(0.7, 5)
    expect(pulseIntensity(nodeWith(0.8, true))).toBe(1)
    expect(pulseIntensity(nodeWith(0, true))).toBeCloseTo(0.35, 5)
  })
})

// ---------------------------------------------------------------------------
// Adapter — happy path + edge cases
// ---------------------------------------------------------------------------

describe("adaptNeighborhood", () => {
  it("builds a graphology Graph with all nodes + edges", () => {
    const g = adaptNeighborhood(mkResponse(), TOKENS)
    expect(g.order).toBe(2)
    expect(g.size).toBe(1)
    expect(g.hasNode("alex")).toBe(true)
    expect(g.hasNode("api_redesign")).toBe(true)
  })

  it("populates AtlasNodeAttributes with identity pipeline values", () => {
    const g = adaptNeighborhood(mkResponse(), TOKENS)
    const alex = g.getNodeAttributes("alex")
    expect(alex.id).toBe("alex")
    expect(alex.name).toBe("Alex Chen")
    expect(alex.label).toBe("Alex Chen")
    expect(alex.size).toBeGreaterThan(0)
    // color = clusterColor (from tokens.clusters)
    expect(alex.color).toMatch(/^#[0-9A-F]{6}$/i)
    // borderColor = trustColor (verified → trustVerified stub)
    expect(alex.borderColor).toBe(TOKENS.trustVerified)
    expect(alex.x).toBe(0)
    expect(alex.focused).toBe(true)
    expect(alex.pulseIntensity).toBe(1)  // 0.92 * 1.4 clamped
    expect(alex.type).toBe("bordered")
  })

  it("preserves the API entity type as entityType (attrs.type is the sigma program key)", () => {
    const g = adaptNeighborhood(mkResponse(), TOKENS)
    expect(g.getNodeAttribute("alex", "entityType")).toBe("Person")
    expect(g.getNodeAttribute("api_redesign", "entityType")).toBe("Project")
  })

  it("edge color = tokens.edge (neutral in rest state)", () => {
    const g = adaptNeighborhood(mkResponse(), TOKENS)
    const edges = g.mapEdges((_key, attrs) => attrs)
    expect(edges).toHaveLength(1)
    expect(edges[0].color).toBe(TOKENS.edge)
    expect(edges[0].type).toBe("curved")
  })

  it("contradiction flag preserved on edges for lens use", () => {
    const res = mkResponse()
    res.edges[0].contradiction = true
    const g = adaptNeighborhood(res, TOKENS)
    const edge = g.mapEdges((_k, a) => a)[0]
    // Rest-state color remains tokens.edge; lens drives the red accent
    expect(edge.color).toBe(TOKENS.edge)
    expect(edge.contradiction).toBe(true)
  })

  it("drops nodes with missing id", () => {
    const res = mkResponse()
    res.nodes.push({
      id: "",
      name: "Missing",
      type: "Person",
      community: null,
      mention_count: 0,
      trust_state: "unknown",
      recency_score: 0,
      focused: false,
    })
    const g = adaptNeighborhood(res, TOKENS)
    expect(g.order).toBe(2)
  })

  it("drops edges pointing to unknown nodes (defensive)", () => {
    const res = mkResponse()
    res.edges.push({
      source: "alex",
      target: "nonexistent",
      type: "mentions",
      weight: 1,
      attestation: "attested",
      contradiction: false,
    })
    const g = adaptNeighborhood(res, TOKENS)
    expect(g.size).toBe(1)
  })

  it("drops self-loops", () => {
    const res = mkResponse()
    res.edges.push({
      source: "alex",
      target: "alex",
      type: "mentions",
      weight: 1,
      attestation: "attested",
      contradiction: false,
    })
    const g = adaptNeighborhood(res, TOKENS)
    expect(g.size).toBe(1)
  })

  it("dedupes identical edges (same source+target+type)", () => {
    const res = mkResponse()
    res.edges.push({ ...res.edges[0] })
    const g = adaptNeighborhood(res, TOKENS)
    expect(g.size).toBe(1)
  })

  it("handles empty response", () => {
    const g = adaptNeighborhood({
      focal_entity: "x",
      nodes: [],
      edges: [],
      truncated: false,
      cached: false,
      isolated_count: 0,
    }, TOKENS)
    expect(g.order).toBe(0)
    expect(g.size).toBe(0)
  })

  it("preserves focused flag on the focal node", () => {
    const g = adaptNeighborhood(mkResponse(), TOKENS)
    expect(g.getNodeAttribute("alex", "focused")).toBe(true)
    expect(g.getNodeAttribute("api_redesign", "focused")).toBe(false)
  })

  it("scales as expected for 100 nodes (smoke test)", () => {
    const nodes = Array.from({ length: 100 }, (_, i) => ({
      id: `n${i}`,
      name: `Node ${i}`,
      type: "Person" as const,
      community: `c${i % 5}`,
      mention_count: i,
      trust_state: "verified" as const,
      recency_score: i / 100,
      focused: false,
    }))
    const edges = Array.from({ length: 100 }, (_, i) => ({
      source: `n${i}`,
      target: `n${(i + 1) % 100}`,
      type: "mentions" as const,
      weight: 1,
      attestation: "attested" as const,
      contradiction: false,
    }))
    const start = performance.now()
    const g = adaptNeighborhood({ focal_entity: "n0", nodes, edges, truncated: false, cached: false, isolated_count: 0 }, TOKENS)
    const elapsed = performance.now() - start
    expect(g.order).toBe(100)
    expect(g.size).toBe(100)
    expect(elapsed).toBeLessThan(50)
  })
})

// ---------------------------------------------------------------------------
// recolorGraph — theme change re-apply
// ---------------------------------------------------------------------------

describe("recolorGraph", () => {
  it("updates node color/borderColor to new tokens", () => {
    const g = adaptNeighborhood(mkResponse(), TOKENS)
    const altTokens: MapTokens = {
      ...TOKENS,
      clusters: Array(8).fill("#FF0000") as string[], // drift-allowed: test stub only
      trustVerified: "#0000FF", // drift-allowed: test stub only
      edge: "#AABBCC", // drift-allowed: test stub only
    }
    recolorGraph(g, altTokens)
    expect(g.getNodeAttribute("alex", "color")).toBe("#FF0000") // drift-allowed: test assertion against stub token
    expect(g.getNodeAttribute("alex", "borderColor")).toBe("#0000FF") // drift-allowed: test assertion against stub token
    expect(g.mapEdges((_k, a) => a.color)[0]).toBe("#AABBCC") // drift-allowed: test assertion against stub token
  })
})
