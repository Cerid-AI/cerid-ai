// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for the graphology adapter (Cerid v1.0 Phase A).
// Pure-logic tests; no DOM / network.

import { describe, expect, it } from "vitest"
import type { NeighborhoodResponse } from "@/lib/types/graph"
import { __TESTING__, adaptNeighborhood } from "./graphology-adapter"

const {
  nodeSize,
  edgeWidth,
  communityColor,
  haloColor,
  edgeColor,
  truncateLabel,
  pulseIntensity,
} = __TESTING__

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
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Sizing
// ---------------------------------------------------------------------------

describe("nodeSize", () => {
  it("floors at 8 for zero mentions", () => {
    expect(nodeSize(0)).toBe(8)
  })

  it("grows logarithmically with mention count", () => {
    const small = nodeSize(1)
    const mid = nodeSize(10)
    const big = nodeSize(100)
    expect(small).toBeLessThan(mid)
    expect(mid).toBeLessThan(big)
    // Log scaling: 100 mentions should NOT be 10x 10 mentions
    expect(big / mid).toBeLessThan(2)
  })

  it("caps at 48px regardless of huge mention counts", () => {
    expect(nodeSize(100_000)).toBe(48)
  })

  it("ignores negative mention counts (defensive)", () => {
    expect(nodeSize(-5)).toBe(8)
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
// Color resolution
// ---------------------------------------------------------------------------

describe("communityColor", () => {
  it("returns fallback graphite for null community", () => {
    expect(communityColor(null)).toBe("#5C6680")
  })

  it("is stable — same input always returns same color", () => {
    const c1 = communityColor("c1")
    const c2 = communityColor("c1")
    expect(c1).toBe(c2)
  })

  it("returns a valid hex color from the palette", () => {
    const color = communityColor("anything")
    expect(color).toMatch(/^#[0-9A-F]{6}$/i)
  })
})

describe("haloColor", () => {
  it.each([
    ["verified", "#5AECCB"],
    ["partial", "#E8C56A"],
    ["unverified", "#D4AF37"],
    ["contradicted", "#FF6B6B"],
    ["unknown", "#5C6680"],
  ])("maps %s → %s", (state, expected) => {
    expect(haloColor(state)).toBe(expected)
  })

  it("falls back to unknown for unrecognized state", () => {
    expect(haloColor("not_a_state")).toBe("#5C6680")
  })
})

describe("edgeColor", () => {
  it("uses contradicted red when contradiction=true regardless of type", () => {
    expect(edgeColor("works_on", true)).toBe("#FF6B6B")
  })

  it("uses type-specific color when no contradiction", () => {
    expect(edgeColor("works_on", false)).toBe("#D4AF37")
    expect(edgeColor("mentions", false)).toBe("#7AC8E5")
  })

  it("falls back to mentions color for unrecognized type", () => {
    expect(edgeColor("custom_type", false)).toBe("#7AC8E5")
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
    // 0.5 * 1.4 = 0.7
    expect(pulseIntensity(nodeWith(0.5, true))).toBeCloseTo(0.7, 5)
    // 0.8 * 1.4 = 1.12 → clamped to 1.0
    expect(pulseIntensity(nodeWith(0.8, true))).toBe(1)
    // floored base of 0.25 * 1.4 = 0.35
    expect(pulseIntensity(nodeWith(0, true))).toBeCloseTo(0.35, 5)
  })
})

// ---------------------------------------------------------------------------
// Adapter — happy path + edge cases
// ---------------------------------------------------------------------------

describe("adaptNeighborhood", () => {
  it("builds a graphology Graph with all nodes + edges", () => {
    const g = adaptNeighborhood(mkResponse())
    expect(g.order).toBe(2)
    expect(g.size).toBe(1)
    expect(g.hasNode("alex")).toBe(true)
    expect(g.hasNode("api_redesign")).toBe(true)
  })

  it("populates AtlasNodeAttributes (size, label, color, haloColor, pulse, type)", () => {
    const g = adaptNeighborhood(mkResponse())
    const alex = g.getNodeAttributes("alex")
    expect(alex.id).toBe("alex")
    expect(alex.name).toBe("Alex Chen")
    expect(alex.label).toBe("Alex Chen")
    expect(alex.size).toBeGreaterThan(0)
    expect(alex.color).toMatch(/^#[0-9A-F]{6}$/i)
    expect(alex.haloColor).toBe("#5AECCB")  // verified
    expect(alex.x).toBe(0)  // placeholder until layout runs
    expect(alex.focused).toBe(true)
    // recency 0.92 * 1.4 (focused) = 1.288 → clamped to 1.0
    expect(alex.pulseIntensity).toBe(1)
    expect(alex.type).toBe("haloed")
  })

  it("populates AtlasEdgeAttributes (size, color)", () => {
    const g = adaptNeighborhood(mkResponse())
    const edges = g.mapEdges((_key, attrs) => attrs)
    expect(edges).toHaveLength(1)
    const edge = edges[0]
    expect(edge.source).toBe("alex")
    expect(edge.type).toBe("works_on")
    expect(edge.size).toBeGreaterThan(0.4)
    expect(edge.color).toBe("#D4AF37")  // works_on gold
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
    const g = adaptNeighborhood(res)
    expect(g.order).toBe(2)  // not 3
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
    const g = adaptNeighborhood(res)
    expect(g.size).toBe(1)  // not 2
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
    const g = adaptNeighborhood(res)
    expect(g.size).toBe(1)
  })

  it("dedupes identical edges (same source+target+type)", () => {
    const res = mkResponse()
    res.edges.push({ ...res.edges[0] })
    const g = adaptNeighborhood(res)
    expect(g.size).toBe(1)
  })

  it("colors contradiction edges red regardless of type", () => {
    const res = mkResponse()
    res.edges[0].contradiction = true
    const g = adaptNeighborhood(res)
    const edge = g.mapEdges((_k, a) => a)[0]
    expect(edge.color).toBe("#FF6B6B")
  })

  it("handles empty response", () => {
    const g = adaptNeighborhood({
      focal_entity: "x",
      nodes: [],
      edges: [],
      truncated: false,
      cached: false,
    })
    expect(g.order).toBe(0)
    expect(g.size).toBe(0)
  })

  it("preserves focused flag on the focal node", () => {
    const g = adaptNeighborhood(mkResponse())
    expect(g.getNodeAttribute("alex", "focused")).toBe(true)
    expect(g.getNodeAttribute("api_redesign", "focused")).toBe(false)
  })

  it("scales as expected for 100 nodes (smoke test for hot path)", () => {
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
    const g = adaptNeighborhood({
      focal_entity: "n0",
      nodes,
      edges,
      truncated: false,
      cached: false,
    })
    const elapsed = performance.now() - start
    expect(g.order).toBe(100)
    expect(g.size).toBe(100)
    expect(elapsed).toBeLessThan(50)  // <50ms for 100 nodes
  })
})
