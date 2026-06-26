// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for pure functions in neural-links.tsx and instanced-nodes.tsx.
// Covers the kindToIsSimilar mapping and the focus-isolation dim constants.
// Shader geometry (WebGL) cannot be tested in jsdom; only the pure helpers
// and exported constants are verified here.

import { describe, it, expect } from "vitest"
import { kindToIsSimilar, NON_NEIGHBOR_EDGE_DIM } from "../neural-links"
import { NON_NEIGHBOR_NODE_DIM } from "../instanced-nodes"

describe("kindToIsSimilar", () => {
  it('maps "co_mention" to 0.0 (primary, brighter treatment)', () => {
    expect(kindToIsSimilar("co_mention")).toBe(0.0)
  })

  it('maps "similar" to 1.0 (secondary, dimmer/cooler treatment)', () => {
    expect(kindToIsSimilar("similar")).toBe(1.0)
  })

  it("maps any unknown kind to 0.0 (co_mention default)", () => {
    expect(kindToIsSimilar("")).toBe(0.0)
    expect(kindToIsSimilar("unknown")).toBe(0.0)
    expect(kindToIsSimilar("SIMILAR")).toBe(0.0)
  })

  it("distinguishes the two canonical kinds", () => {
    expect(kindToIsSimilar("co_mention")).not.toBe(kindToIsSimilar("similar"))
  })
})

describe("focus isolation dim constants", () => {
  it("NON_NEIGHBOR_NODE_DIM is ~0.12 (decisive node fade on hover/select)", () => {
    expect(NON_NEIGHBOR_NODE_DIM).toBeCloseTo(0.12, 5)
  })

  it("NON_NEIGHBOR_NODE_DIM is well below the visibility skip threshold (0.15)", () => {
    // The click handler skips nodes with vis < 0.15; the dim must be below that
    // so non-neighbor nodes become non-clickable while focused.
    expect(NON_NEIGHBOR_NODE_DIM).toBeLessThan(0.15)
  })

  it("NON_NEIGHBOR_EDGE_DIM is ~0.06 (decisive edge fade, less than node dim)", () => {
    expect(NON_NEIGHBOR_EDGE_DIM).toBeCloseTo(0.06, 5)
    expect(NON_NEIGHBOR_EDGE_DIM).toBeLessThan(NON_NEIGHBOR_NODE_DIM)
  })
})
