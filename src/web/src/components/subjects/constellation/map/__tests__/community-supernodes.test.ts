import { describe, it, expect } from "vitest"
import { superNodeRadius, aggregateCommunityEdges, isCollapsed, makeViewportSpread } from "../community-supernodes"

describe("superNodeRadius", () => {
  it("grows with member count but stays bounded", () => {
    expect(superNodeRadius(1)).toBeGreaterThanOrEqual(8)
    expect(superNodeRadius(100)).toBeGreaterThan(superNodeRadius(10))
    expect(superNodeRadius(100000)).toBeLessThanOrEqual(60)
  })
})

describe("aggregateCommunityEdges", () => {
  it("aggregates cross-community link weights and drops intra-community links", () => {
    const entities = [
      { id: "a", community: "0:1" },
      { id: "b", community: "0:1" },
      { id: "c", community: "0:2" },
    ]
    // links are [srcIndex, tgtIndex, weight, kind]
    const links: [number, number, number, string][] = [
      [0, 1, 5, "co_mention"], // a-b: same community → dropped
      [0, 2, 3, "co_mention"], // a-c: 0:1 ↔ 0:2
      [1, 2, 4, "similar"],    // b-c: 0:1 ↔ 0:2 → sums with above
    ]
    const result = aggregateCommunityEdges(entities, links)
    expect(result).toHaveLength(1)
    expect(result[0].a).toBe("0:1")
    expect(result[0].b).toBe("0:2")
    expect(result[0].weight).toBe(7)
  })
  it("ignores links whose endpoints have no community", () => {
    const entities = [{ id: "a", community: null }, { id: "b", community: "0:2" }]
    const links: [number, number, number, string][] = [[0, 1, 5, "co_mention"]]
    expect(aggregateCommunityEdges(entities, links)).toHaveLength(0)
  })
})

describe("isCollapsed", () => {
  it("is true at/above the threshold, false below", () => {
    expect(isCollapsed(2.5, 2.0)).toBe(true)
    expect(isCollapsed(2.0, 2.0)).toBe(true)
    expect(isCollapsed(1.0, 2.0)).toBe(false)
  })
})

describe("makeViewportSpread", () => {
  it("fans a tightly-clustered set out toward the padded viewport edges", () => {
    // Three points clustered near the center of a 1000x1000 viewport.
    const clustered = [{ x: 495, y: 495 }, { x: 500, y: 500 }, { x: 505, y: 505 }]
    const spread = makeViewportSpread(clustered, 1000, 1000, 0.08)
    const a = spread(clustered[0])
    const b = spread(clustered[2])
    // After spread the extreme points are far apart (near the padded edges),
    // not the ~10px apart they were in the input.
    expect(Math.hypot(b.x - a.x, b.y - a.y)).toBeGreaterThan(800)
  })
  it("maps the bbox center to the viewport center", () => {
    const pts = [{ x: 0, y: 0 }, { x: 100, y: 50 }]
    const spread = makeViewportSpread(pts, 800, 600, 0.08)
    const c = spread({ x: 50, y: 25 })
    expect(c.x).toBeCloseTo(400)
    expect(c.y).toBeCloseTo(300)
  })
  it("is identity for an empty set", () => {
    const spread = makeViewportSpread([], 800, 600)
    expect(spread({ x: 42, y: 7 })).toEqual({ x: 42, y: 7 })
  })
})
