// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
import { describe, it, expect } from "vitest"
import { memberHidden, edgeHidden } from "./combo-collapse"

describe("memberHidden (A10 per-community combos)", () => {
  it("is false when nothing is collapsed", () => {
    expect(memberHidden("c1", new Set())).toBe(false)
  })
  it("hides a node whose community is collapsed", () => {
    expect(memberHidden("c1", new Set(["c1"]))).toBe(true)
    expect(memberHidden("c2", new Set(["c1"]))).toBe(false)
  })
  it("coerces non-string community ids and tolerates null", () => {
    expect(memberHidden(null, new Set(["c1"]))).toBe(false)
    expect(memberHidden(undefined, new Set(["c1"]))).toBe(false)
    expect(memberHidden(5 as unknown as string, new Set(["5"]))).toBe(true)
  })
})

describe("edgeHidden", () => {
  it("hides an edge when either endpoint's community is collapsed", () => {
    const collapsed = new Set(["c1"])
    expect(edgeHidden("c1", "c2", collapsed)).toBe(true)
    expect(edgeHidden("c2", "c1", collapsed)).toBe(true)
    expect(edgeHidden("c2", "c3", collapsed)).toBe(false)
  })
  it("is false with nothing collapsed", () => {
    expect(edgeHidden("c1", "c2", new Set())).toBe(false)
  })
})
