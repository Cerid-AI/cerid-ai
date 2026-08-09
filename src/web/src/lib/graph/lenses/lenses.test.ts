// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Unit tests for the 4 Atlas lenses. Verifies each lens's transform
// fires the expected visual change on representative inputs, and that
// composeLenses() chains transforms left-to-right.

import { describe, expect, it } from "vitest"
import Graph from "graphology"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"
import type { MapTokens } from "@/components/subjects/constellation/map/community-layer"
import {
  composeLenses,
  composeLensesWithTokens,
  contradictionLens,
  openQuestionLens,
  provenanceLens,
  qualityLens,
  domainLens,
  bridgesLens,
  makeBridgesLens,
  LENS_ORDER,
  LENS_REGISTRY,
} from "./index"

// Minimal resolved-token fixture for the token-aware factories.
const TOKENS: MapTokens = {
  clusters: Array(8).fill("#888888") as string[], // drift-allowed: test fixture only
  clusterOther: "#888888", // drift-allowed: test fixture only
  domains: Array(12).fill("#888888") as string[], // drift-allowed: test fixture only
  domainOther: "#666666", // drift-allowed: test fixture only
  edge: "#888888", // drift-allowed: test fixture only
  dim: "#333333", // drift-allowed: test fixture only
  interaction: "#00c8b4", // drift-allowed: test fixture only
  foreground: "#111111", // drift-allowed: test fixture only
  background: "#f5f5f5", // drift-allowed: test fixture only
  trustVerified: "#555555", // drift-allowed: test fixture only
  trustPartial: "#777777", // drift-allowed: test fixture only
  trustUnverified: "#999999", // drift-allowed: test fixture only
  graphite: "#6b7080", // drift-allowed: test fixture only
  grid: "#eeeeee", // drift-allowed: test fixture only
}

function mkNode(overrides: Partial<AtlasNodeAttributes> = {}): AtlasNodeAttributes {
  return {
    id: "n",
    name: "Node",
    type: "bordered",
    entityType: "Person",
    community: "c1",
    mention_count: 10,
    trust_state: "verified",
    recency_score: 0.8,
    focused: false,
    x: 0,
    y: 0,
    size: 15,
    label: "Node",
    color: "#7AC8E5", // drift-allowed: test fixture only
    haloColor: "#5AECCB", // drift-allowed: test fixture only
    pulseIntensity: 0.8,
    ...overrides,
  }
}

function mkEdge(overrides: Partial<AtlasEdgeAttributes> = {}): AtlasEdgeAttributes {
  return {
    source: "a",
    target: "b",
    type: "mentions",
    weight: 1,
    attestation: "attested",
    contradiction: false,
    size: 1,
    color: "#7AC8E5", // drift-allowed: test fixture only
    ...overrides,
  }
}

function mkGraph() {
  const g = new Graph<AtlasNodeAttributes, AtlasEdgeAttributes>()
  g.addNode("a", mkNode({ id: "a", community: "c1" }))
  g.addNode("b", mkNode({ id: "b", community: "c1" }))
  g.addNode("c", mkNode({ id: "c", community: "c2" }))
  g.addEdgeWithKey("a-b", "a", "b", mkEdge({ source: "a", target: "b" }))
  g.addEdgeWithKey("a-c", "a", "c", mkEdge({ source: "a", target: "c", contradiction: true }))
  return g
}

// ---------------------------------------------------------------------------
// Contradiction lens
// ---------------------------------------------------------------------------

describe("contradictionLens", () => {
  it("highlights contradicted edges with the lens legendColor", () => {
    const g = mkGraph()
    const out = contradictionLens.transformEdge("a-c", mkEdge({ contradiction: true }), g)
    expect(out.color).toBe(contradictionLens.legendColor)
    expect(out.size).toBeGreaterThanOrEqual(2.5)
  })

  it("dims non-contradicted edges (smaller size)", () => {
    const g = mkGraph()
    const before = mkEdge({ contradiction: false, size: 1 })
    const out = contradictionLens.transformEdge("a-b", before, g)
    expect(out.size).toBeLessThan(before.size)
  })

  it("pops nodes touching a contradicted edge with lens color", () => {
    const g = mkGraph()
    const out = contradictionLens.transformNode("a", g.getNodeAttributes("a"), g)
    expect(out.haloColor).toBe(contradictionLens.legendColor)
    expect(out.pulseIntensity).toBe(1)
  })

  it("dims nodes not touching a contradicted edge", () => {
    const g = mkGraph()
    const before = g.getNodeAttributes("b")
    const out = contradictionLens.transformNode("b", before, g)
    // pulseIntensity is reduced
    expect(out.pulseIntensity).toBeLessThan(before.pulseIntensity)
  })
})

// ---------------------------------------------------------------------------
// Open-question lens
// ---------------------------------------------------------------------------

describe("openQuestionLens", () => {
  it("flags stale + low-trust nodes with lens legendColor", () => {
    const g = mkGraph()
    const attrs = mkNode({ recency_score: 0.2, trust_state: "unverified" })
    const out = openQuestionLens.transformNode("n", attrs, g)
    expect(out.haloColor).toBe(openQuestionLens.legendColor)
  })

  it("does not flag fresh verified nodes (dims them instead)", () => {
    const g = mkGraph()
    const attrs = mkNode({ recency_score: 0.9, trust_state: "verified" })
    const out = openQuestionLens.transformNode("n", attrs, g)
    expect(out.pulseIntensity).toBeLessThan(attrs.pulseIntensity)
    expect(out.haloColor).not.toBe(openQuestionLens.legendColor)
  })

  it("dims edges (smaller size)", () => {
    const g = mkGraph()
    const before = mkEdge({ size: 1 })
    const out = openQuestionLens.transformEdge("a-b", before, g)
    expect(out.size).toBeLessThan(before.size)
  })
})

// ---------------------------------------------------------------------------
// Provenance lens
// ---------------------------------------------------------------------------

describe("provenanceLens", () => {
  it("amplifies node halo with community color", () => {
    const g = mkGraph()
    const before = mkNode({ color: "#7AC8E5", pulseIntensity: 0.5 }) // drift-allowed: test fixture
    const out = provenanceLens.transformNode("a", before, g)
    expect(out.haloColor).toBe("#7AC8E5") // drift-allowed: test fixture — checks that haloColor = input color
    expect(out.pulseIntensity).toBeGreaterThan(0.5)
  })

  it("keeps within-community edges visible (not dimmed)", () => {
    const g = mkGraph()
    const before = g.getEdgeAttributes("a-b")
    const out = provenanceLens.transformEdge("a-b", before, g)
    // Same attrs returned for intra-community edges
    expect(out).toEqual(before)
  })

  it("dims cross-community edges (smaller size)", () => {
    const g = mkGraph()
    const before = g.getEdgeAttributes("a-c")
    const out = provenanceLens.transformEdge("a-c", before, g)
    expect(out.size).toBeLessThan(before.size)
  })
})

// ---------------------------------------------------------------------------
// Quality lens
// ---------------------------------------------------------------------------

describe("qualityLens", () => {
  it("verified entities glow with the lens legendColor at full intensity", () => {
    const g = mkGraph()
    const attrs = mkNode({ trust_state: "verified" })
    const out = qualityLens.transformNode("n", attrs, g)
    expect(out.haloColor).toBe(qualityLens.legendColor)
    expect(out.pulseIntensity).toBe(1)
  })

  it("contradicted entities ring with the contradiction legendColor at full intensity", () => {
    const g = mkGraph()
    const attrs = mkNode({ trust_state: "contradicted" })
    const out = qualityLens.transformNode("n", attrs, g)
    // contradicted uses contradiction legend color (red-ish)
    expect(out.haloColor).toBe(contradictionLens.legendColor)
    expect(out.pulseIntensity).toBe(1)
  })

  it("unverified entities dim toward 0.35", () => {
    const g = mkGraph()
    const attrs = mkNode({ trust_state: "unverified" })
    const out = qualityLens.transformNode("n", attrs, g)
    expect(out.pulseIntensity).toBe(0.35)
  })

  it("unknown trust drops to 0.25", () => {
    const g = mkGraph()
    const attrs = mkNode({ trust_state: "unknown" })
    const out = qualityLens.transformNode("n", attrs, g)
    expect(out.pulseIntensity).toBe(0.25)
  })

  it("edges pass through untouched", () => {
    const g = mkGraph()
    const before = mkEdge({ color: "#7AC8E5" }) // drift-allowed: test fixture
    const out = qualityLens.transformEdge("a-b", before, g)
    expect(out).toBe(before)
  })
})

// ---------------------------------------------------------------------------
// Composition
// ---------------------------------------------------------------------------

describe("composeLenses", () => {
  it("returns identity reducers when no lenses active", () => {
    const g = mkGraph()
    const { nodeReducer, edgeReducer } = composeLenses([], g)
    const node = mkNode()
    expect(nodeReducer("a", node)).toEqual(node)
    const edge = mkEdge()
    expect(edgeReducer("a-b", edge)).toEqual(edge)
  })

  it("applies lenses in order — later lens sees earlier output", () => {
    const g = mkGraph()
    // Open-question lens dims first, then quality lens lights up verified
    const { nodeReducer } = composeLenses([openQuestionLens, qualityLens], g)
    const verifiedNode = g.getNodeAttributes("a")  // verified by default
    const out = nodeReducer("a", verifiedNode)
    // openQuestion would dim, but quality lights it back up
    expect(out.haloColor).toBe(qualityLens.legendColor)
    expect(out.pulseIntensity).toBe(1)
  })

  it("returns AtlasNodeAttributes with all renderer-required fields", () => {
    const g = mkGraph()
    const { nodeReducer } = composeLenses([contradictionLens], g)
    const out = nodeReducer("a", g.getNodeAttributes("a"))
    expect(out.x).toBeDefined()
    expect(out.y).toBeDefined()
    expect(out.size).toBeDefined()
    expect(out.label).toBeDefined()
    expect(out.color).toBeDefined()
    expect(out.haloColor).toBeDefined()
    expect(out.pulseIntensity).toBeDefined()
    // type may be "bordered" (Meridian) or "haloed" (legacy) — just check it's a string
    expect(typeof out.type).toBe("string")
  })
})

// ---------------------------------------------------------------------------
// Registry shape
// ---------------------------------------------------------------------------

describe("domainLens (static fallback)", () => {
  it("has id 'domain' and contract fields", () => {
    expect(domainLens.id).toBe("domain")
    expect(typeof domainLens.label).toBe("string")
    expect(typeof domainLens.legendColor).toBe("string")
    expect(typeof domainLens.transformNode).toBe("function")
    expect(typeof domainLens.transformEdge).toBe("function")
  })

  it("static transformNode passes attrs through (no tokens available)", () => {
    const g = mkGraph()
    const attrs = g.getNodeAttributes("a")
    const out = domainLens.transformNode("a", attrs, g)
    expect(out).toEqual(attrs)
  })

  it("static transformEdge passes attrs through (no tokens available)", () => {
    const g = mkGraph()
    const before = g.getEdgeAttributes("a-b")
    const out = domainLens.transformEdge("a-b", before, g)
    expect(out).toBe(before)
  })
})

describe("makeBridgesLens (C1b)", () => {
  it("colors a zero-score node exactly the dim token", () => {
    const lens = makeBridgesLens(TOKENS, { betweenness: { a: 0 } })
    const g = mkGraph()
    const out = lens.transformNode("a", g.getNodeAttributes("a"), g)
    expect(out.color).toBe(TOKENS.dim)
    expect(out.haloColor).toBe(TOKENS.dim)
  })

  it("colors a max-score node exactly the interaction token", () => {
    const lens = makeBridgesLens(TOKENS, { betweenness: { a: 1 } })
    const g = mkGraph()
    const out = lens.transformNode("a", g.getNodeAttributes("a"), g)
    expect(out.color).toBe(TOKENS.interaction)
  })

  it("treats nodes missing from the score map as score 0 (dim)", () => {
    const lens = makeBridgesLens(TOKENS, { betweenness: { b: 0.9 } })
    const g = mkGraph()
    const out = lens.transformNode("a", g.getNodeAttributes("a"), g)
    expect(out.color).toBe(TOKENS.dim)
  })

  it("with no context (scores still computing) every node reads dim", () => {
    const lens = makeBridgesLens(TOKENS)
    const g = mkGraph()
    const out = lens.transformNode("a", g.getNodeAttributes("a"), g)
    expect(out.color).toBe(TOKENS.dim)
  })

  it("leaves edges untouched", () => {
    const lens = makeBridgesLens(TOKENS, { betweenness: { a: 1 } })
    const g = mkGraph()
    const before = g.getEdgeAttributes("a-b")
    expect(lens.transformEdge("a-b", before, g)).toBe(before)
  })

  it("composeLensesWithTokens threads the betweenness context through", () => {
    const g = mkGraph()
    const { nodeReducer } = composeLensesWithTokens(["bridges"], TOKENS, g, {
      betweenness: { a: 1, b: 0 },
    })
    expect(nodeReducer("a", g.getNodeAttributes("a")).color).toBe(TOKENS.interaction)
    expect(nodeReducer("b", g.getNodeAttributes("b")).color).toBe(TOKENS.dim)
  })
})

describe("bridgesLens (static fallback)", () => {
  it("passes node attrs through untouched (no scores available statically)", () => {
    const g = mkGraph()
    const attrs = g.getNodeAttributes("a")
    expect(bridgesLens.transformNode("a", attrs, g)).toEqual(attrs)
  })
})

describe("LENS_REGISTRY", () => {
  it("contains all 6 named lenses", () => {
    expect(Object.keys(LENS_REGISTRY)).toHaveLength(6)
    expect(LENS_REGISTRY["contradiction"]).toBeDefined()
    expect(LENS_REGISTRY["open-question"]).toBeDefined()
    expect(LENS_REGISTRY["provenance"]).toBeDefined()
    expect(LENS_REGISTRY["quality"]).toBeDefined()
    expect(LENS_REGISTRY["domain"]).toBeDefined()
    expect(LENS_REGISTRY["bridges"]).toBeDefined()
  })

  it("LENS_ORDER matches the registry contents", () => {
    expect(LENS_ORDER).toHaveLength(Object.keys(LENS_REGISTRY).length)
    for (const lens of LENS_ORDER) {
      expect(LENS_REGISTRY[lens.id]).toBeDefined()
    }
  })

  it("every lens has the contract fields", () => {
    for (const lens of LENS_ORDER) {
      expect(typeof lens.id).toBe("string")
      expect(typeof lens.label).toBe("string")
      expect(typeof lens.description).toBe("string")
      expect(typeof lens.legendColor).toBe("string")
      expect(typeof lens.transformNode).toBe("function")
      expect(typeof lens.transformEdge).toBe("function")
    }
  })
})
