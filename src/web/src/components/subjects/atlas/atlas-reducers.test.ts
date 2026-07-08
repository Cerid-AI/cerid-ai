// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { describe, it, expect } from "vitest"
import { buildAtlasNodeReducer, buildAtlasEdgeReducer, hexWithAlpha } from "./atlas-reducers"
import { focusNodeAlpha } from "@/lib/graph/interactions/focus-spotlight"
import type { AtlasNodeAttributes, AtlasEdgeAttributes } from "@/lib/types/graph"

function mkNodeAttrs(overrides: Partial<AtlasNodeAttributes> = {}): AtlasNodeAttributes {
  return {
    id: "n1",
    name: "Node One",
    type: "bordered",
    entityType: "Person",
    community: "c1",
    mention_count: 5,
    trust_state: "verified",
    recency_score: 0.5,
    focused: false,
    x: 0,
    y: 0,
    size: 10,
    label: "Node One",
    color: "#A0A0FF", // drift-allowed: test stub only
    haloColor: "#4488FF", // drift-allowed: test stub only
    pulseIntensity: 0.5,
    ...overrides,
  }
}

function mkEdgeAttrs(overrides: Partial<AtlasEdgeAttributes> = {}): AtlasEdgeAttributes {
  return {
    source: "n1",
    target: "n2",
    type: "works_on",
    weight: 1,
    attestation: "attested",
    contradiction: false,
    size: 1.5,
    color: "#CCCCCC", // drift-allowed: test stub only
    ...overrides,
  } as AtlasEdgeAttributes
}

/** Minimal spotlight stub the reducers read. */
function mkSpotlight(neighbors: Set<string> | null, progress = 1) {
  return { neighbors: () => neighbors, progress: () => progress }
}

const TOKENS = { clusterOther: "#888888", edge: "#CCCCCC" } // drift-allowed: test stub only

describe("hexWithAlpha", () => {
  it("appends the alpha byte to a #rrggbb color", () => {
    expect(hexWithAlpha("#a0a0ff", 1)).toBe("#a0a0ffff") // drift-allowed: test stub only
    expect(hexWithAlpha("#a0a0ff", 0)).toBe("#a0a0ff00") // drift-allowed: test stub only
  })
  it("replaces the alpha byte of a #rrggbbaa color", () => {
    expect(hexWithAlpha("#a0a0ff80", 1)).toBe("#a0a0ffff") // drift-allowed: test stub only
  })
  it("passes non-hex colors through untouched", () => {
    expect(hexWithAlpha("oklch(0.5 0.1 200)", 0.5)).toBe("oklch(0.5 0.1 200)")
  })
})

describe("buildAtlasNodeReducer", () => {
  it("passes attributes through when no lens, chips, or focus are active", () => {
    const reduce = buildAtlasNodeReducer({
      typeChips: new Set(),
      tokens: TOKENS,
      spotlight: mkSpotlight(null),
    })
    const attrs = mkNodeAttrs()
    expect(reduce("n1", attrs)).toEqual(attrs)
  })

  it("dims nodes whose entityType is filtered out by active chips", () => {
    const reduce = buildAtlasNodeReducer({
      typeChips: new Set(["Project"]),
      tokens: TOKENS,
      spotlight: mkSpotlight(null),
    })
    const out = reduce("n1", mkNodeAttrs({ entityType: "Person", size: 10 }))
    expect(out.color).toBe(TOKENS.clusterOther)
    expect(out.size).toBe(5)
    const kept = reduce("n1", mkNodeAttrs({ entityType: "Project" }))
    expect(kept.color).toBe("#A0A0FF") // drift-allowed: test stub only
  })

  it("fades non-neighbors under spotlight: alpha color, cleared label, shrunk size", () => {
    const reduce = buildAtlasNodeReducer({
      typeChips: new Set(),
      tokens: TOKENS,
      spotlight: mkSpotlight(new Set(["other"]), 1),
    })
    const out = reduce("n1", mkNodeAttrs({ size: 10 }))
    expect(out.color).toBe(hexWithAlpha("#A0A0FF", 0.2)) // focusNodeAlpha(1, 1) = 0.2 // drift-allowed: test stub only
    expect(out.label).toBe("")
    expect(out.size).toBe(6) // focusNodeSize(10, 1, 3) = 6
  })

  it("leaves spotlight neighbors at full strength", () => {
    const reduce = buildAtlasNodeReducer({
      typeChips: new Set(),
      tokens: TOKENS,
      spotlight: mkSpotlight(new Set(["n1"]), 1),
    })
    const attrs = mkNodeAttrs()
    expect(reduce("n1", attrs)).toEqual(attrs)
  })

  it("fades entering nodes in by spawnProgress (A5 growth)", () => {
    const reduce = buildAtlasNodeReducer({
      typeChips: new Set(),
      tokens: TOKENS,
      spotlight: mkSpotlight(null),
    })
    const half = reduce("n1", mkNodeAttrs({ spawnProgress: 0.5 }))
    expect(half.color).toBe(hexWithAlpha("#A0A0FF", 0.5)) // drift-allowed: test stub only
    const grown = reduce("n1", mkNodeAttrs({ spawnProgress: 1 }))
    expect(grown.color).toBe("#A0A0FF") // fully arrived → untouched // drift-allowed: test stub only
  })

  it("multiplies spawn fade into the spotlight fade for entering non-neighbors", () => {
    const reduce = buildAtlasNodeReducer({
      typeChips: new Set(),
      tokens: TOKENS,
      spotlight: mkSpotlight(new Set(["other"]), 1),
    })
    const out = reduce("n1", mkNodeAttrs({ spawnProgress: 0.5 }))
    expect(out.color).toBe(hexWithAlpha("#A0A0FF", focusNodeAlpha(1, 1) * 0.5)) // drift-allowed: test stub only
  })

  it("applies the lens reducer before chips and spotlight", () => {
    const reduce = buildAtlasNodeReducer({
      lensNodeReducer: (_n, attrs) => ({ ...attrs, color: "#123456" }), // drift-allowed: test stub only
      typeChips: new Set(),
      tokens: TOKENS,
      spotlight: mkSpotlight(new Set(["other"]), 1),
    })
    const out = reduce("n1", mkNodeAttrs())
    expect(out.color).toBe(hexWithAlpha("#123456", 0.2)) // lens color, then spotlight fade // drift-allowed: test stub only
  })
})

describe("buildAtlasEdgeReducer", () => {
  const graph = {
    source: (e: string) => (e === "e-in" ? "a" : "x"),
    target: (e: string) => (e === "e-in" ? "b" : "y"),
  }

  it("returns lens output untouched when no focus is active", () => {
    const reduce = buildAtlasEdgeReducer({
      typeChips: new Set(),
      tokens: TOKENS,
      spotlight: mkSpotlight(null),
      graph,
    })
    const attrs = mkEdgeAttrs()
    expect(reduce("e-in", attrs)).toEqual(attrs)
  })

  it("keeps neighborhood edges and fades outside edges under focus", () => {
    const reduce = buildAtlasEdgeReducer({
      typeChips: new Set(),
      tokens: TOKENS,
      spotlight: mkSpotlight(new Set(["a", "b"]), 1),
      graph,
    })
    const kept = reduce("e-in", mkEdgeAttrs())
    expect(kept.color).toBe("#CCCCCC") // drift-allowed: test stub only
    const faded = reduce("e-out", mkEdgeAttrs())
    expect(faded.color).not.toBe("#CCCCCC") // drift-allowed: test stub only
    expect(faded.color.startsWith("#cccccc")).toBe(true) // alpha-suffixed base // drift-allowed: test stub only
  })

  it("hides thin edges at overview zoom (LOD) and fades band edges", () => {
    const reduce = buildAtlasEdgeReducer({
      typeChips: new Set(),
      tokens: TOKENS,
      spotlight: mkSpotlight(null),
      graph,
      getLodTier: () => "overview",
    })
    // Well below the overview floor (2.2) → fully faded = hidden.
    expect(reduce("e-in", mkEdgeAttrs({ size: 1 })).hidden).toBe(true)
    // Inside the fade band above the floor → visible but alpha-faded.
    const band = reduce("e-in", mkEdgeAttrs({ size: 2.4 }))
    expect(band.hidden).toBeFalsy()
    expect(band.color.startsWith("#cccccc")).toBe(true) // drift-allowed: test stub only
  })

  it("shows every edge untouched at detail zoom", () => {
    const reduce = buildAtlasEdgeReducer({
      typeChips: new Set(),
      tokens: TOKENS,
      spotlight: mkSpotlight(null),
      graph,
      getLodTier: () => "detail",
    })
    const attrs = mkEdgeAttrs({ size: 1 })
    expect(reduce("e-in", attrs)).toEqual(attrs)
  })

  it("keeps focused-neighborhood edges visible regardless of LOD", () => {
    const reduce = buildAtlasEdgeReducer({
      typeChips: new Set(),
      tokens: TOKENS,
      spotlight: mkSpotlight(new Set(["a", "b"]), 1),
      graph,
      getLodTier: () => "overview",
    })
    const kept = reduce("e-in", mkEdgeAttrs({ size: 1 }))
    expect(kept.hidden).toBeFalsy()
    expect(kept.color).toBe("#CCCCCC") // drift-allowed: test stub only
  })
})
