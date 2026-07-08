// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for the adaptive-quality helpers in quality.ts and the
// distance-based label-decimation helper in hub-labels.tsx. Both are
// pure functions that require no WebGL or jsdom context.

import { describe, it, expect } from "vitest"
import { degradeTier, upgradeTier, visibleLabelCount, labelFillOpacity, QUALITY_TIERS } from "../quality"

describe("degradeTier", () => {
  it("ultra steps down to high", () => {
    expect(degradeTier("ultra")).toBe("high")
  })

  it("high steps down to medium", () => {
    expect(degradeTier("high")).toBe("medium")
  })

  it("medium steps down to low", () => {
    expect(degradeTier("medium")).toBe("low")
  })

  it("low floors at low (cannot degrade further)", () => {
    expect(degradeTier("low")).toBe("low")
  })

  it("each step produces a tier that is in QUALITY_TIERS", () => {
    for (const tier of QUALITY_TIERS) {
      expect(QUALITY_TIERS).toContain(degradeTier(tier))
    }
  })
})

describe("upgradeTier", () => {
  it("low steps up to medium when ceiling is ultra", () => {
    expect(upgradeTier("low", "ultra")).toBe("medium")
  })

  it("medium steps up to high when ceiling is ultra", () => {
    expect(upgradeTier("medium", "ultra")).toBe("high")
  })

  it("high steps up to ultra when ceiling is ultra", () => {
    expect(upgradeTier("high", "ultra")).toBe("ultra")
  })

  it("caps at the ceiling tier — cannot upgrade past it", () => {
    expect(upgradeTier("medium", "medium")).toBe("medium")
    expect(upgradeTier("high", "high")).toBe("high")
    expect(upgradeTier("ultra", "ultra")).toBe("ultra")
  })

  it("upgrade from low with ceiling=high cannot reach ultra", () => {
    let tier = upgradeTier("low", "high")
    expect(tier).toBe("medium")
    tier = upgradeTier(tier, "high")
    expect(tier).toBe("high")
    tier = upgradeTier(tier, "high")
    expect(tier).toBe("high") // capped
  })

  it("effective tier starts at persisted tier (upgrade from ceiling is a no-op)", () => {
    // Simulates the initial state: effectiveQuality === quality (ceiling).
    expect(upgradeTier("ultra", "ultra")).toBe("ultra")
    expect(upgradeTier("high", "high")).toBe("high")
  })
})

describe("degradeTier + upgradeTier round-trip", () => {
  it("degrade then upgrade returns to original when ceiling permits", () => {
    const tier = "high" as const
    const degraded = degradeTier(tier)
    expect(degraded).toBe("medium")
    expect(upgradeTier(degraded, tier)).toBe("high")
  })

  it("a low ceiling prevents recovery above it even after step-up", () => {
    // Ceiling locked at medium; degraded to low; step up only reaches medium.
    const ceiling = "medium" as const
    const degraded = degradeTier("medium")
    expect(degraded).toBe("low")
    expect(upgradeTier(degraded, ceiling)).toBe("medium")
    expect(upgradeTier("medium", ceiling)).toBe("medium") // stays at cap
  })
})

describe("visibleLabelCount", () => {
  it("returns full count when close (distance < 28)", () => {
    expect(visibleLabelCount(10, 18)).toBe(18)
    expect(visibleLabelCount(27, 18)).toBe(18)
  })

  it("returns 12 max when at default camera distance (28–39)", () => {
    expect(visibleLabelCount(28, 18)).toBe(12)
    expect(visibleLabelCount(35, 18)).toBe(12)
    expect(visibleLabelCount(28, 6)).toBe(6) // max wins when count < 12
  })

  it("returns 6 max when zoomed out (40–54)", () => {
    expect(visibleLabelCount(40, 18)).toBe(6)
    expect(visibleLabelCount(54, 18)).toBe(6)
    expect(visibleLabelCount(40, 4)).toBe(4) // max wins when count < 6
  })

  it("returns 3 max when very far out (>= 55)", () => {
    expect(visibleLabelCount(55, 18)).toBe(3)
    expect(visibleLabelCount(100, 18)).toBe(3)
    expect(visibleLabelCount(55, 2)).toBe(2) // max wins when count < 3
  })

  it("respects max=0 at all distances", () => {
    expect(visibleLabelCount(0, 0)).toBe(0)
    expect(visibleLabelCount(100, 0)).toBe(0)
  })
})

describe("labelFillOpacity", () => {
  it("is brightest when close in (< 28)", () => {
    expect(labelFillOpacity(10)).toBeCloseTo(0.85)
    expect(labelFillOpacity(27)).toBeCloseTo(0.85)
  })

  it("dims by one step at the default viewing band (28–39)", () => {
    expect(labelFillOpacity(28)).toBeCloseTo(0.75)
    expect(labelFillOpacity(39)).toBeCloseTo(0.75)
  })

  it("dims further when zoomed out (40–54)", () => {
    expect(labelFillOpacity(40)).toBeCloseTo(0.62)
    expect(labelFillOpacity(54)).toBeCloseTo(0.62)
  })

  it("is faintest very far out (>= 55) but never fully invisible", () => {
    expect(labelFillOpacity(55)).toBeCloseTo(0.5)
    expect(labelFillOpacity(1000)).toBeCloseTo(0.5)
  })

  it("decreases monotonically with distance", () => {
    const samples = [5, 27, 28, 39, 40, 54, 55, 200].map(labelFillOpacity)
    for (let i = 1; i < samples.length; i++) {
      expect(samples[i]).toBeLessThanOrEqual(samples[i - 1])
    }
  })

  it("always returns a value in [0, 1]", () => {
    for (const d of [-5, 0, 30, 60, 500]) {
      const o = labelFillOpacity(d)
      expect(o).toBeGreaterThanOrEqual(0)
      expect(o).toBeLessThanOrEqual(1)
    }
  })
})
