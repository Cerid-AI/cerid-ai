// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for the Meridian identity pipeline pure functions.
// No DOM / sigma / canvas dependencies — pure logic only.

import { describe, expect, it } from "vitest"
import type { MapTokens } from "@/components/subjects/constellation/map/community-layer"
import {
  communitySlot,
  clusterColor,
  trustColor,
  nodeSize,
} from "./identity"

// ---------------------------------------------------------------------------
// Stub tokens
// ---------------------------------------------------------------------------

const TOKENS: MapTokens = {
  clusters: [
    "#AA0000", "#00AA00", "#0000AA", "#AAAA00",
    "#AA00AA", "#00AAAA", "#AA5500", "#5500AA",
  ],
  clusterOther: "#555555", // drift-allowed: test stub only
  edge:          "#CCCCCC", // drift-allowed: test stub only
  dim:           "#888888", // drift-allowed: test stub only
  interaction:   "#00E5D8", // drift-allowed: test stub only
  foreground:    "#111111", // drift-allowed: test stub only
  background:    "#FFFFFF", // drift-allowed: test stub only
  trustVerified:   "#004488", // drift-allowed: test stub only
  trustPartial:    "#884400", // drift-allowed: test stub only
  trustUnverified: "#880000", // drift-allowed: test stub only
  grid:          "#EEEEEE", // drift-allowed: test stub only
}

// ---------------------------------------------------------------------------
// communitySlot
// ---------------------------------------------------------------------------

describe("communitySlot", () => {
  it("returns a value in [0, 7]", () => {
    for (const id of ["c1", "c2", "alpha", "beta", "team-a", "project-x"]) {
      const slot = communitySlot(id)
      expect(slot).toBeGreaterThanOrEqual(0)
      expect(slot).toBeLessThanOrEqual(7)
    }
  })

  it("is stable — same id always returns same slot", () => {
    expect(communitySlot("community-abc")).toBe(communitySlot("community-abc"))
    expect(communitySlot("xyz")).toBe(communitySlot("xyz"))
  })

  it("different ids produce varied slots (not all the same)", () => {
    const slots = new Set(
      Array.from({ length: 20 }, (_, i) => communitySlot(`c${i}`))
    )
    // With 20 inputs and 8 slots, expect at least 4 distinct slots
    expect(slots.size).toBeGreaterThanOrEqual(4)
  })
})

// ---------------------------------------------------------------------------
// clusterColor
// ---------------------------------------------------------------------------

describe("clusterColor", () => {
  it("returns clusterOther for null community", () => {
    expect(clusterColor(TOKENS, null)).toBe(TOKENS.clusterOther)
  })

  it("returns clusterOther for undefined community", () => {
    expect(clusterColor(TOKENS, undefined)).toBe(TOKENS.clusterOther)
  })

  it("returns clusterOther for empty string", () => {
    expect(clusterColor(TOKENS, "")).toBe(TOKENS.clusterOther)
  })

  it("returns a value from tokens.clusters", () => {
    const c = clusterColor(TOKENS, "my-community")
    expect(TOKENS.clusters).toContain(c)
  })

  it("is stable — same community always returns same color", () => {
    expect(clusterColor(TOKENS, "stable-id")).toBe(clusterColor(TOKENS, "stable-id"))
  })
})

// ---------------------------------------------------------------------------
// trustColor
// ---------------------------------------------------------------------------

describe("trustColor", () => {
  it("verified → tokens.trustVerified", () => {
    expect(trustColor(TOKENS, "verified")).toBe(TOKENS.trustVerified)
  })

  it("partial → tokens.trustPartial", () => {
    expect(trustColor(TOKENS, "partial")).toBe(TOKENS.trustPartial)
  })

  it("unverified → tokens.trustUnverified", () => {
    expect(trustColor(TOKENS, "unverified")).toBe(TOKENS.trustUnverified)
  })

  it("contradicted → tokens.trustUnverified (lens provides red accent)", () => {
    expect(trustColor(TOKENS, "contradicted")).toBe(TOKENS.trustUnverified)
  })

  it("unknown → tokens.dim", () => {
    expect(trustColor(TOKENS, "unknown")).toBe(TOKENS.dim)
  })

  it("unrecognized state → tokens.dim", () => {
    expect(trustColor(TOKENS, "not_a_state")).toBe(TOKENS.dim)
  })
})

// ---------------------------------------------------------------------------
// nodeSize
// ---------------------------------------------------------------------------

describe("nodeSize", () => {
  it("floors at 6 for zero mentions", () => {
    expect(nodeSize(0)).toBe(6)
  })

  it("grows with mention count", () => {
    expect(nodeSize(1)).toBeGreaterThan(6)
    expect(nodeSize(25)).toBeGreaterThan(nodeSize(1))
    expect(nodeSize(100)).toBeGreaterThan(nodeSize(25))
  })

  it("caps at 18px regardless of huge mention counts", () => {
    expect(nodeSize(100_000)).toBe(18)
  })

  it("ignores negative mention counts", () => {
    expect(nodeSize(-10)).toBe(6)
  })

  it("is sqrt-scaled (not linear) — 100x mentions < 10x size", () => {
    const small = nodeSize(1)
    const big = nodeSize(100)
    expect(big / small).toBeLessThan(10)
  })
})
