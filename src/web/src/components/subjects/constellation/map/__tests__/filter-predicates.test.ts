import { describe, it, expect } from "vitest"
import { matchesSearch, isOrphan } from "../filter-predicates"

describe("matchesSearch", () => {
  it("is case-insensitive substring match", () => {
    expect(matchesSearch("Quenchforge", "quench")).toBe(true)
    expect(matchesSearch("Quenchforge", "FORGE")).toBe(true)
  })
  it("matches everything on an empty/whitespace query", () => {
    expect(matchesSearch("anything", "")).toBe(true)
    expect(matchesSearch("anything", "   ")).toBe(true)
  })
  it("returns false on no match", () => {
    expect(matchesSearch("Quenchforge", "zzz")).toBe(false)
  })
})

describe("isOrphan", () => {
  it("is true only at degree 0", () => {
    expect(isOrphan(0)).toBe(true)
    expect(isOrphan(1)).toBe(false)
  })
})
