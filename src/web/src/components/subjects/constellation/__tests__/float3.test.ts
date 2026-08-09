import { describe, it, expect } from "vitest"
import { float3 } from "../float3"

describe("float3", () => {
  it("is deterministic for a given (seed, t)", () => {
    expect(float3(0.4, 12.5)).toEqual(float3(0.4, 12.5))
  })
  it("stays bounded within [-amp, amp] per axis", () => {
    for (let s = 0; s < 1; s += 0.13) {
      for (let t = 0; t < 40; t += 0.7) {
        for (const c of float3(s, t, 2)) {
          expect(c).toBeGreaterThanOrEqual(-2.0001)
          expect(c).toBeLessThanOrEqual(2.0001)
        }
      }
    }
  })
  it("scales linearly with amplitude", () => {
    const a = float3(0.7, 9)
    const b = float3(0.7, 9, 3)
    for (let i = 0; i < 3; i++) expect(b[i]).toBeCloseTo(a[i] * 3, 6)
  })
  it("gives different phase per seed", () => {
    expect(float3(0.1, 5)).not.toEqual(float3(0.9, 5))
  })
  it("is continuous in t (no jumps)", () => {
    const a = float3(0.3, 5)
    const b = float3(0.3, 5.001)
    for (let i = 0; i < 3; i++) expect(Math.abs(a[i] - b[i])).toBeLessThan(0.02)
  })
})
