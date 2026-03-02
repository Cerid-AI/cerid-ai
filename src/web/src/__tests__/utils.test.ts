// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { tokenCost, getAccuracyTier, parseTags } from "@/lib/utils"

// ---------------------------------------------------------------------------
// tokenCost
// ---------------------------------------------------------------------------

describe("tokenCost", () => {
  it("calculates cost for 1M tokens at $1/1M", () => {
    expect(tokenCost(1_000_000, 1.0)).toBeCloseTo(1.0)
  })

  it("returns 0 for 0 tokens", () => {
    expect(tokenCost(0, 5.0)).toBe(0)
  })

  it("calculates fractional cost correctly", () => {
    // 250 tokens at $0.15/1M ≈ $0.0000375
    expect(tokenCost(250, 0.15)).toBeCloseTo(0.0000375)
  })
})

// ---------------------------------------------------------------------------
// getAccuracyTier
// ---------------------------------------------------------------------------

describe("getAccuracyTier", () => {
  it("returns High/green for accuracy >= 0.8", () => {
    const tier = getAccuracyTier(0.85)
    expect(tier.label).toBe("High")
    expect(tier.textColor).toContain("green")
    expect(tier.barColor).toContain("green")
  })

  it("returns Medium/yellow for accuracy >= 0.5", () => {
    const tier = getAccuracyTier(0.6)
    expect(tier.label).toBe("Medium")
    expect(tier.textColor).toContain("yellow")
    expect(tier.barColor).toContain("yellow")
  })

  it("returns Low/red for accuracy < 0.5", () => {
    const tier = getAccuracyTier(0.3)
    expect(tier.label).toBe("Low")
    expect(tier.textColor).toContain("red")
    expect(tier.barColor).toContain("red")
  })

  it("treats 0.8 as High (boundary)", () => {
    expect(getAccuracyTier(0.8).label).toBe("High")
  })

  it("treats 0.5 as Medium (boundary)", () => {
    expect(getAccuracyTier(0.5).label).toBe("Medium")
  })

  it("treats 0 as Low", () => {
    expect(getAccuracyTier(0).label).toBe("Low")
  })

  it("treats 1.0 as High", () => {
    expect(getAccuracyTier(1.0).label).toBe("High")
  })
})

// ---------------------------------------------------------------------------
// parseTags
// ---------------------------------------------------------------------------

describe("parseTags", () => {
  it("returns array as-is", () => {
    expect(parseTags(["a", "b"])).toEqual(["a", "b"])
  })

  it("parses valid JSON string array", () => {
    expect(parseTags('["x","y"]')).toEqual(["x", "y"])
  })

  it("returns empty array for invalid JSON string", () => {
    expect(parseTags("not-json")).toEqual([])
  })

  it("returns empty array for undefined", () => {
    expect(parseTags(undefined)).toEqual([])
  })

  it("returns empty array for null", () => {
    expect(parseTags(null)).toEqual([])
  })

  it("returns empty array for number", () => {
    expect(parseTags(42)).toEqual([])
  })

  it("returns empty array for JSON object (not array)", () => {
    expect(parseTags('{"key":"val"}')).toEqual([])
  })

  it("returns empty array for empty string", () => {
    expect(parseTags("")).toEqual([])
  })
})
