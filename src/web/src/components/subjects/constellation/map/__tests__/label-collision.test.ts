// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
import { describe, it, expect } from "vitest"
import { selectVisibleLabels, rectsOverlap, type LabelRect } from "../label-collision"

const rect = (id: string, cx: number, cy: number, w: number, h: number, priority: number): LabelRect => ({
  id, cx, cy, w, h, priority,
})

describe("rectsOverlap", () => {
  it("detects overlap between two centered boxes", () => {
    expect(rectsOverlap(rect("a", 0, 0, 10, 4, 1), rect("b", 5, 0, 10, 4, 1))).toBe(true)
  })
  it("returns false for disjoint boxes", () => {
    expect(rectsOverlap(rect("a", 0, 0, 10, 4, 1), rect("b", 100, 0, 10, 4, 1))).toBe(false)
  })
  it("treats edge-touching as non-overlapping", () => {
    expect(rectsOverlap(rect("a", 0, 0, 10, 4, 1), rect("b", 10, 0, 10, 4, 1))).toBe(false)
  })
})

describe("selectVisibleLabels", () => {
  it("keeps every label when none overlap", () => {
    const ids = selectVisibleLabels([
      rect("a", 0, 0, 10, 4, 1),
      rect("b", 100, 0, 10, 4, 1),
    ])
    expect(ids).toEqual(new Set(["a", "b"]))
  })

  it("drops the lower-priority label of an overlapping pair", () => {
    const ids = selectVisibleLabels([
      rect("small", 0, 0, 10, 4, 5),
      rect("big", 4, 0, 10, 4, 50),
    ])
    expect(ids.has("big")).toBe(true)
    expect(ids.has("small")).toBe(false)
  })

  it("places labels in strict priority order (largest community wins its space)", () => {
    // Three mutually-overlapping boxes; only the highest priority survives.
    const ids = selectVisibleLabels([
      rect("a", 0, 0, 20, 4, 10),
      rect("b", 2, 0, 20, 4, 30),
      rect("c", 4, 0, 20, 4, 20),
    ])
    expect(ids).toEqual(new Set(["b"]))
  })

  it("applies optional padding so near-misses are suppressed", () => {
    const boxes = [rect("a", 0, 0, 10, 4, 10), rect("b", 12, 0, 10, 4, 5)]
    expect(selectVisibleLabels(boxes)).toEqual(new Set(["a", "b"]))          // 2px gap, no pad
    expect(selectVisibleLabels(boxes, 3)).toEqual(new Set(["a"]))            // pad closes the gap
  })
})
