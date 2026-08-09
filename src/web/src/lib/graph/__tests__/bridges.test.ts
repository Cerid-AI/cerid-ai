// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Unit tests for the bridges-lens color ramp (C1). Pure — the betweenness
// itself is computed in a worker (graph-metrics.worker.ts); this is just the
// score→color mapping.

import { describe, it, expect } from "vitest"
import { normalizeScores, lerpHex, bridgesColor } from "../bridges"

describe("normalizeScores", () => {
  it("scales the max score to 1", () => {
    const out = normalizeScores({ a: 2, b: 4, c: 1 })
    expect(out.b).toBeCloseTo(1)
    expect(out.a).toBeCloseTo(0.5)
    expect(out.c).toBeCloseTo(0.25)
  })

  it("returns all-zero unchanged (no divide-by-zero)", () => {
    const out = normalizeScores({ a: 0, b: 0 })
    expect(out).toEqual({ a: 0, b: 0 })
  })

  it("handles an empty map", () => {
    expect(normalizeScores({})).toEqual({})
  })
})

describe("lerpHex", () => {
  it("returns the start color at t=0", () => {
    expect(lerpHex("#000000", "#ffffff", 0)).toBe("#000000") // drift-allowed: hex-math unit test
  })

  it("returns the end color at t=1", () => {
    expect(lerpHex("#000000", "#ffffff", 1)).toBe("#ffffff") // drift-allowed: hex-math unit test
  })

  it("interpolates to grey at t=0.5", () => {
    expect(lerpHex("#000000", "#ffffff", 0.5)).toBe("#808080") // drift-allowed: hex-math unit test
  })

  it("clamps t outside [0,1]", () => {
    expect(lerpHex("#000000", "#ffffff", -1)).toBe("#000000") // drift-allowed: hex-math unit test
    expect(lerpHex("#000000", "#ffffff", 2)).toBe("#ffffff") // drift-allowed: hex-math unit test
  })

  it("interpolates each channel independently", () => {
    // midpoint per channel
    expect(lerpHex("#204060", "#80a0c0", 0.5)).toBe("#507090") // drift-allowed: hex-math unit test
  })

  it("always returns a 7-char #rrggbb string", () => {
    const c = lerpHex("#123456", "#abcdef", 0.37) // drift-allowed: hex-math unit test
    expect(c).toMatch(/^#[0-9a-f]{6}$/)
  })
})

describe("bridgesColor", () => {
  const DIM = "#333333" // drift-allowed: hex-math unit test
  const HOT = "#00c8b4" // drift-allowed: hex-math unit test

  it("is the dim color at score 0", () => {
    expect(bridgesColor(0, DIM, HOT)).toBe(DIM)
  })

  it("is the interaction color at score 1", () => {
    expect(bridgesColor(1, DIM, HOT)).toBe(HOT)
  })

  it("clamps out-of-range scores", () => {
    expect(bridgesColor(-5, DIM, HOT)).toBe(DIM)
    expect(bridgesColor(9, DIM, HOT)).toBe(HOT)
  })

  it("lifts mid-range via gamma so bridges pop before the very top", () => {
    // sqrt gamma: score 0.25 -> t 0.5, so it should be past the midpoint hue.
    const mid = bridgesColor(0.25, DIM, HOT)
    const linearMid = lerpHex(DIM, HOT, 0.25)
    expect(mid).not.toBe(linearMid)
    expect(mid).toBe(lerpHex(DIM, HOT, 0.5))
  })
})
