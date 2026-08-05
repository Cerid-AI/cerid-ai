// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
import { describe, it, expect } from "vitest"
import { bboxOf, lodTier, cameraTargetForPoints, lodEdgeMinSize, lodEdgeAlpha } from "../semantic-zoom"

describe("bboxOf", () => {
  it("returns the centroid of a point set as the camera target", () => {
    const r = bboxOf([[0, 0], [10, 0], [10, 10], [0, 10]])
    expect(r).not.toBeNull()
    expect(r!.x).toBeCloseTo(5)
    expect(r!.y).toBeCloseTo(5)
  })
  it("returns a smaller ratio (zoom in) for a tight cluster", () => {
    const tight = bboxOf([[4, 4], [6, 6]])!
    const wide = bboxOf([[0, 0], [100, 100]])!
    expect(tight.ratio).toBeLessThan(wide.ratio)
  })
  it("returns null for an empty set", () => {
    expect(bboxOf([])).toBeNull()
  })
})

describe("cameraTargetForPoints", () => {
  it("returns the bbox CENTER (not centroid) so the camera re-centers on the cluster", () => {
    // Skewed point set: centroid would be pulled toward the cluster of 3 near origin,
    // but the bbox center is the midpoint of the extremes.
    const r = cameraTargetForPoints([[0, 0], [0, 0], [0, 0], [10, 10]])!
    expect(r.x).toBeCloseTo(5)
    expect(r.y).toBeCloseTo(5)
  })
  it("clamps a single point (zero extent) to minRatio", () => {
    const r = cameraTargetForPoints([[3, 3]])!
    expect(r.ratio).toBeCloseTo(0.15)
    expect(r.x).toBeCloseTo(3)
    expect(r.y).toBeCloseTo(3)
  })
  it("scales ratio with extent and never exceeds maxRatio", () => {
    const tight = cameraTargetForPoints([[0, 0], [0.1, 0.1]])!
    const wide = cameraTargetForPoints([[0, 0], [5, 5]])!
    expect(tight.ratio).toBeLessThan(wide.ratio)
    expect(wide.ratio).toBeLessThanOrEqual(1)
  })
  it("returns null for an empty set", () => {
    expect(cameraTargetForPoints([])).toBeNull()
  })
})

describe("lodTier", () => {
  it("maps camera ratio to overview/mid/detail tiers", () => {
    expect(lodTier(3.0)).toBe("overview")
    expect(lodTier(1.0)).toBe("mid")
    expect(lodTier(0.2)).toBe("detail")
  })
})

describe("lodEdgeMinSize", () => {
  it("shows all edges at detail (zoomed in), raises the floor as you zoom out", () => {
    expect(lodEdgeMinSize("detail")).toBe(0)
    expect(lodEdgeMinSize("mid")).toBeGreaterThan(lodEdgeMinSize("detail"))
    expect(lodEdgeMinSize("overview")).toBeGreaterThanOrEqual(lodEdgeMinSize("mid"))
  })
})

describe("lodEdgeAlpha", () => {
  it("returns full alpha for every edge at detail tier", () => {
    expect(lodEdgeAlpha("detail", 0.1)).toBe(1)
    expect(lodEdgeAlpha("detail", 2.5)).toBe(1)
  })
  it("returns 0 at or below the tier floor (edge fully faded = hidden)", () => {
    expect(lodEdgeAlpha("mid", lodEdgeMinSize("mid"))).toBe(0)
    expect(lodEdgeAlpha("overview", 1.0)).toBe(0)
  })
  it("ramps continuously from 0 to 1 across the fade band above the floor", () => {
    const floor = lodEdgeMinSize("mid")
    const midBand = lodEdgeAlpha("mid", floor + 0.2)
    expect(midBand).toBeGreaterThan(0)
    expect(midBand).toBeLessThan(1)
    // Monotone: thicker edges are more opaque within the band.
    expect(lodEdgeAlpha("mid", floor + 0.3)).toBeGreaterThan(lodEdgeAlpha("mid", floor + 0.1))
  })
  it("saturates at 1 for edges well above the band", () => {
    expect(lodEdgeAlpha("mid", 2.5)).toBe(1)
    expect(lodEdgeAlpha("overview", 2.5)).toBeCloseTo(0.75, 5)
  })
})
