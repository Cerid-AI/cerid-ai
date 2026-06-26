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
  domainSlot,
  domainColor,
  trustColor,
  nodeSize,
} from "./identity"

// ---------------------------------------------------------------------------
// Stub tokens
// ---------------------------------------------------------------------------

const TOKENS: MapTokens = {
  clusters: [
    "#AA0000", "#00AA00", "#0000AA", "#AAAA00", // drift-allowed: test stub only
    "#AA00AA", "#00AAAA", "#AA5500", "#5500AA", // drift-allowed: test stub only
  ],
  clusterOther: "#555555", // drift-allowed: test stub only
  domains: [
    "#D10000", "#CC4400", "#AA8800", "#558800", // drift-allowed: test stub only (slots 0-3)
    "#008844", "#007755", "#006688", "#2244AA", // drift-allowed: test stub only (slots 4-7)
    "#4400AA", "#770088", "#AA0066", "#CC0033", // drift-allowed: test stub only (slots 8-11)
  ],
  domainOther:   "#666666", // drift-allowed: test stub only
  edge:          "#CCCCCC", // drift-allowed: test stub only
  dim:           "#888888", // drift-allowed: test stub only
  interaction:   "#00E5D8", // drift-allowed: test stub only
  foreground:    "#111111", // drift-allowed: test stub only
  background:    "#FFFFFF", // drift-allowed: test stub only
  trustVerified:   "#004488", // drift-allowed: test stub only
  trustPartial:    "#884400", // drift-allowed: test stub only
  trustUnverified: "#880000", // drift-allowed: test stub only
  graphite:        "#6b7080", // drift-allowed: test stub only
  grid:          "#EEEEEE", // drift-allowed: test stub only
  fontSans:      "system-ui, sans-serif", // drift-allowed: test stub only
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
// domainSlot
// ---------------------------------------------------------------------------

// Canonical 12 taxonomy domain names (from config/taxonomy.py).
// The test asserts these are all collision-free under domainSlot.
const CANONICAL_DOMAINS = [
  "coding",
  "finance",
  "projects",
  "personal",
  "general",
  "conversations",
  "notes",
  "mail",
  "messages",
  "meetings",
  "inbox",
  "digests",
] as const

describe("domainSlot", () => {
  it("returns a value in [0, 11]", () => {
    for (const name of [...CANONICAL_DOMAINS, "research", "custom-domain", "x"]) {
      const slot = domainSlot(name)
      expect(slot).toBeGreaterThanOrEqual(0)
      expect(slot).toBeLessThanOrEqual(11)
    }
  })

  it("is stable — same domain always returns same slot", () => {
    expect(domainSlot("coding")).toBe(domainSlot("coding"))
    expect(domainSlot("research")).toBe(domainSlot("research"))
    expect(domainSlot("my-custom-domain")).toBe(domainSlot("my-custom-domain"))
  })

  it("canonical 12 taxonomy names are all collision-free", () => {
    // Built-in collision check: DOMAIN_HASH_SALT (796) was chosen precisely for this.
    // If this test fails after a taxonomy change, update DOMAIN_HASH_SALT in identity.ts.
    const slots = CANONICAL_DOMAINS.map(domainSlot)
    const unique = new Set(slots)
    expect(unique.size).toBe(CANONICAL_DOMAINS.length)
  })

  it("canonical slot assignments are stable (regression guard)", () => {
    // These values are derived from DOMAIN_HASH_SALT=796.
    // Changing the hash algorithm or salt breaks this test intentionally.
    expect(domainSlot("projects")).toBe(0)
    expect(domainSlot("meetings")).toBe(1)
    expect(domainSlot("inbox")).toBe(2)
    expect(domainSlot("finance")).toBe(3)
    expect(domainSlot("messages")).toBe(4)
    expect(domainSlot("notes")).toBe(5)
    expect(domainSlot("general")).toBe(6)
    expect(domainSlot("coding")).toBe(7)
    expect(domainSlot("conversations")).toBe(8)
    expect(domainSlot("mail")).toBe(9)
    expect(domainSlot("digests")).toBe(10)
    expect(domainSlot("personal")).toBe(11)
  })
})

// ---------------------------------------------------------------------------
// domainColor
// ---------------------------------------------------------------------------

describe("domainColor", () => {
  it("returns domainOther for null", () => {
    expect(domainColor(TOKENS, null)).toBe(TOKENS.domainOther)
  })

  it("returns domainOther for undefined", () => {
    expect(domainColor(TOKENS, undefined)).toBe(TOKENS.domainOther)
  })

  it("returns domainOther for empty string", () => {
    expect(domainColor(TOKENS, "")).toBe(TOKENS.domainOther)
  })

  it("returns a value from tokens.domains for a known domain", () => {
    const c = domainColor(TOKENS, "coding")
    expect(TOKENS.domains).toContain(c)
  })

  it("is stable — same domain always returns same color", () => {
    expect(domainColor(TOKENS, "finance")).toBe(domainColor(TOKENS, "finance"))
    expect(domainColor(TOKENS, "research")).toBe(domainColor(TOKENS, "research"))
  })

  it("all canonical domains return a color from tokens.domains (not domainOther)", () => {
    for (const name of CANONICAL_DOMAINS) {
      const c = domainColor(TOKENS, name)
      expect(TOKENS.domains).toContain(c)
      expect(c).not.toBe(TOKENS.domainOther)
    }
  })

  it("runtime domain (not in taxonomy) still returns a stable token color", () => {
    // Runtime-minted domains like 'research' hash to a slot in 0..11
    const c = domainColor(TOKENS, "research")
    expect(TOKENS.domains).toContain(c)
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
