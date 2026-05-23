// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for the 4 Atlas lenses. Verifies each lens's transform
// fires the expected visual change on representative inputs, and that
// composeLenses() chains transforms left-to-right.

import { describe, expect, it } from "vitest"
import Graph from "graphology"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"
import {
  composeLenses,
  contradictionLens,
  openQuestionLens,
  provenanceLens,
  qualityLens,
  LENS_ORDER,
  LENS_REGISTRY,
} from "./index"

function mkNode(overrides: Partial<AtlasNodeAttributes> = {}): AtlasNodeAttributes {
  return {
    id: "n",
    name: "Node",
    type: "haloed",
    community: "c1",
    mention_count: 10,
    trust_state: "verified",
    recency_score: 0.8,
    focused: false,
    x: 0,
    y: 0,
    size: 15,
    label: "Node",
    color: "#7AC8E5",
    haloColor: "#5AECCB",
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
    color: "#7AC8E5",
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
  it("highlights contradicted edges in red", () => {
    const g = mkGraph()
    const out = contradictionLens.transformEdge("a-c", mkEdge({ contradiction: true }), g)
    expect(out.color).toBe("#FF6B6B")
    expect(out.size).toBeGreaterThanOrEqual(2.5)
  })

  it("dims non-contradicted edges", () => {
    const g = mkGraph()
    const before = mkEdge({ contradiction: false, color: "#7AC8E5", size: 1 })
    const out = contradictionLens.transformEdge("a-b", before, g)
    expect(out.color).toBe("#3D4760")
    expect(out.size).toBeLessThan(before.size)
  })

  it("pops nodes touching a contradicted edge", () => {
    const g = mkGraph()
    const out = contradictionLens.transformNode("a", g.getNodeAttributes("a"), g)
    expect(out.haloColor).toBe("#FF6B6B")
    expect(out.pulseIntensity).toBe(1)
  })

  it("dims nodes not touching a contradicted edge", () => {
    const g = mkGraph()
    const out = contradictionLens.transformNode("b", g.getNodeAttributes("b"), g)
    expect(out.color).toBe("#3D4760")
  })
})

// ---------------------------------------------------------------------------
// Open-question lens
// ---------------------------------------------------------------------------

describe("openQuestionLens", () => {
  it("amber-halos stale + low-trust nodes", () => {
    const g = mkGraph()
    const attrs = mkNode({ recency_score: 0.2, trust_state: "unverified" })
    const out = openQuestionLens.transformNode("n", attrs, g)
    expect(out.haloColor).toBe("#E8C56A")
  })

  it("does not flag fresh verified nodes", () => {
    const g = mkGraph()
    const attrs = mkNode({ recency_score: 0.9, trust_state: "verified" })
    const out = openQuestionLens.transformNode("n", attrs, g)
    // Dimmed, not flagged
    expect(out.color).toBe("#3D4760")
    expect(out.haloColor).not.toBe("#E8C56A")
  })

  it("dims edges", () => {
    const g = mkGraph()
    const out = openQuestionLens.transformEdge("a-b", mkEdge(), g)
    expect(out.color).toBe("#3D4760")
  })
})

// ---------------------------------------------------------------------------
// Provenance lens
// ---------------------------------------------------------------------------

describe("provenanceLens", () => {
  it("amplifies node halo with community color", () => {
    const g = mkGraph()
    const before = mkNode({ color: "#7AC8E5", pulseIntensity: 0.5 })
    const out = provenanceLens.transformNode("a", before, g)
    expect(out.haloColor).toBe("#7AC8E5")
    expect(out.pulseIntensity).toBeGreaterThan(0.5)
  })

  it("keeps within-community edges visible", () => {
    const g = mkGraph()
    const out = provenanceLens.transformEdge(
      "a-b",
      g.getEdgeAttributes("a-b"),
      g,
    )
    expect(out.color).not.toBe("#3D4760")
  })

  it("dims cross-community edges", () => {
    const g = mkGraph()
    const out = provenanceLens.transformEdge(
      "a-c",
      g.getEdgeAttributes("a-c"),
      g,
    )
    expect(out.color).toBe("#3D4760")
  })
})

// ---------------------------------------------------------------------------
// Quality lens
// ---------------------------------------------------------------------------

describe("qualityLens", () => {
  it("verified entities glow teal at full intensity", () => {
    const g = mkGraph()
    const attrs = mkNode({ trust_state: "verified" })
    const out = qualityLens.transformNode("n", attrs, g)
    expect(out.haloColor).toBe("#5AECCB")
    expect(out.pulseIntensity).toBe(1)
  })

  it("contradicted entities ring red at full intensity", () => {
    const g = mkGraph()
    const attrs = mkNode({ trust_state: "contradicted" })
    const out = qualityLens.transformNode("n", attrs, g)
    expect(out.haloColor).toBe("#FF6B6B")
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
    const before = mkEdge({ color: "#7AC8E5" })
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
    // openQuestion would dim, but quality lights it back up to teal
    expect(out.haloColor).toBe("#5AECCB")
    expect(out.pulseIntensity).toBe(1)
  })

  it("returns AtlasNodeAttributes with all renderer-required fields", () => {
    const g = mkGraph()
    const { nodeReducer } = composeLenses([contradictionLens], g)
    const out = nodeReducer("a", g.getNodeAttributes("a"))
    // All AtlasNodeAttributes fields must survive
    expect(out.x).toBeDefined()
    expect(out.y).toBeDefined()
    expect(out.size).toBeDefined()
    expect(out.label).toBeDefined()
    expect(out.color).toBeDefined()
    expect(out.haloColor).toBeDefined()
    expect(out.pulseIntensity).toBeDefined()
    expect(out.type).toBe("haloed")
  })
})

// ---------------------------------------------------------------------------
// Registry shape
// ---------------------------------------------------------------------------

describe("LENS_REGISTRY", () => {
  it("contains all 4 named lenses", () => {
    expect(LENS_REGISTRY.contradiction).toBeDefined()
    expect(LENS_REGISTRY["open-question"]).toBeDefined()
    expect(LENS_REGISTRY.provenance).toBeDefined()
    expect(LENS_REGISTRY.quality).toBeDefined()
  })

  it("LENS_ORDER matches the registry contents", () => {
    expect(LENS_ORDER).toHaveLength(4)
    const ids = LENS_ORDER.map((l) => l.id)
    expect(ids).toEqual(["contradiction", "open-question", "provenance", "quality"])
  })

  it("every lens has the contract fields", () => {
    for (const lens of LENS_ORDER) {
      expect(lens.id).toBeTruthy()
      expect(lens.label).toBeTruthy()
      expect(lens.description).toBeTruthy()
      expect(lens.legendColor).toMatch(/^#[0-9A-F]{6}$/i)
      expect(typeof lens.transformNode).toBe("function")
      expect(typeof lens.transformEdge).toBe("function")
    }
  })
})
