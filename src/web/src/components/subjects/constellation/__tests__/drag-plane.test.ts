import { describe, it, expect } from "vitest"
import { planeIntersect } from "../drag-plane"

describe("planeIntersect", () => {
  it("hits a plane facing the ray at the expected point", () => {
    // Ray from (0,0,10) toward -z; plane at z=0 with normal +z.
    const hit = planeIntersect([0, 0, 10], [0, 0, -1], [0, 0, 0], [0, 0, 1])
    expect(hit).not.toBeNull()
    expect(hit![0]).toBeCloseTo(0)
    expect(hit![1]).toBeCloseTo(0)
    expect(hit![2]).toBeCloseTo(0)
  })
  it("projects an angled ray onto the plane", () => {
    const hit = planeIntersect([0, 0, 10], [1, 0, -1], [0, 0, 0], [0, 0, 1])
    expect(hit![0]).toBeCloseTo(10)
    expect(hit![2]).toBeCloseTo(0)
  })
  it("returns null for a ray parallel to the plane", () => {
    expect(planeIntersect([0, 0, 10], [1, 0, 0], [0, 0, 0], [0, 0, 1])).toBeNull()
  })
})
