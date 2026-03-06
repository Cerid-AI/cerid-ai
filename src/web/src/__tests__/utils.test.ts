// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { tokenCost, getAccuracyTier, parseTags, getFileRenderMode, getLanguageFromFilename, formatCost } from "@/lib/utils"

// ---------------------------------------------------------------------------
// formatCost
// ---------------------------------------------------------------------------

describe("formatCost", () => {
  it("returns $0.00 for zero", () => {
    expect(formatCost(0)).toBe("$0.00")
  })

  it("returns $0.00 for negative values", () => {
    expect(formatCost(-0.05)).toBe("$0.00")
  })

  it("returns 4 decimal places for sub-mill costs", () => {
    expect(formatCost(0.0001)).toBe("$0.0001")
    expect(formatCost(0.0009)).toBe("$0.0009")
  })

  it("returns 3 decimal places for mill-to-cent costs", () => {
    expect(formatCost(0.005)).toBe("$0.005")
    expect(formatCost(0.001)).toBe("$0.001")
  })

  it("returns 2 decimal places for cent+ costs", () => {
    expect(formatCost(0.05)).toBe("$0.05")
    expect(formatCost(1.23)).toBe("$1.23")
    expect(formatCost(0.01)).toBe("$0.01")
  })

  it("handles typical model costs", () => {
    expect(formatCost(0.0003)).toBe("$0.0003")
    expect(formatCost(0.05)).toBe("$0.05")
  })
})

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

// ---------------------------------------------------------------------------
// getFileRenderMode
// ---------------------------------------------------------------------------

describe("getFileRenderMode", () => {
  it("returns 'code' for Python files", () => {
    expect(getFileRenderMode("example.py")).toBe("code")
  })

  it("returns 'code' for TypeScript files", () => {
    expect(getFileRenderMode("app.tsx")).toBe("code")
  })

  it("returns 'code' for JSON files", () => {
    expect(getFileRenderMode("config.json")).toBe("code")
  })

  it("returns 'markdown' for .md files", () => {
    expect(getFileRenderMode("README.md")).toBe("markdown")
  })

  it("returns 'markdown' for .mdx files", () => {
    expect(getFileRenderMode("page.mdx")).toBe("markdown")
  })

  it("returns 'table' for CSV files", () => {
    expect(getFileRenderMode("data.csv")).toBe("table")
  })

  it("returns 'table' for TSV files", () => {
    expect(getFileRenderMode("data.tsv")).toBe("table")
  })

  it("returns 'text' for unknown extensions", () => {
    expect(getFileRenderMode("notes.txt")).toBe("text")
  })

  it("returns 'text' for PDF files", () => {
    expect(getFileRenderMode("document.pdf")).toBe("text")
  })

  it("returns 'text' for extensionless files", () => {
    expect(getFileRenderMode("Makefile")).toBe("text")
  })

  it("handles uppercase extensions", () => {
    expect(getFileRenderMode("script.PY")).toBe("code")
  })
})

// ---------------------------------------------------------------------------
// getLanguageFromFilename
// ---------------------------------------------------------------------------

describe("getLanguageFromFilename", () => {
  it("maps .py to python", () => {
    expect(getLanguageFromFilename("app.py")).toBe("python")
  })

  it("maps .ts to typescript", () => {
    expect(getLanguageFromFilename("index.ts")).toBe("typescript")
  })

  it("maps .jsx to jsx", () => {
    expect(getLanguageFromFilename("App.jsx")).toBe("jsx")
  })

  it("maps .sh to bash", () => {
    expect(getLanguageFromFilename("script.sh")).toBe("bash")
  })

  it("maps .yml to yaml", () => {
    expect(getLanguageFromFilename("config.yml")).toBe("yaml")
  })

  it("returns 'text' for unknown extensions", () => {
    expect(getLanguageFromFilename("file.xyz")).toBe("text")
  })

  it("maps Dockerfile to dockerfile", () => {
    expect(getLanguageFromFilename("Dockerfile")).toBe("dockerfile")
  })

  it("handles uppercase extensions", () => {
    expect(getLanguageFromFilename("main.RS")).toBe("rust")
  })
})
