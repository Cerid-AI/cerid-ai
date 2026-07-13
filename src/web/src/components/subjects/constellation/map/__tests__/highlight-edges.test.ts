import { describe, it, expect } from "vitest"
import Graph from "graphology"
import { incidentEdgeSegments, curveControlPoint } from "../highlight-edges"

function fixture(): Graph {
  const g = new Graph({ type: "undirected" })
  g.addNode("a", { x: 0, y: 0 })
  g.addNode("b", { x: 10, y: 0 })
  g.addNode("c", { x: 0, y: 10 })
  g.addNode("d", { x: 99, y: 99 })
  g.addEdge("a", "b")
  g.addEdge("a", "c")
  return g
}

describe("incidentEdgeSegments", () => {
  it("returns one segment per incident edge of the focus node", () => {
    const segs = incidentEdgeSegments(fixture(), "a")
    expect(segs).toHaveLength(2)
  })
  it("uses the endpoints' graph-space coordinates", () => {
    const segs = incidentEdgeSegments(fixture(), "a")
    const ab = segs.find((s) => (s.x2 === 10 && s.y2 === 0) || (s.x1 === 10 && s.y1 === 0))
    expect(ab).toBeDefined()
  })
  it("returns nothing for a null/absent focus", () => {
    expect(incidentEdgeSegments(fixture(), null)).toHaveLength(0)
    expect(incidentEdgeSegments(fixture(), "zzz")).toHaveLength(0)
  })
  it("defaults curvature to 0 when the edge has no curvature attribute", () => {
    const segs = incidentEdgeSegments(fixture(), "a")
    expect(segs.every((s) => s.curvature === 0)).toBe(true)
  })
  it("carries each edge's curvature and preserves source→target endpoint order", () => {
    const g = new Graph({ type: "undirected" })
    g.addNode("a", { x: 0, y: 0 })
    g.addNode("b", { x: 10, y: 0 })
    // Edge declared b→a: the segment must keep b as (x1,y1) — curvature
    // sign is relative to the edge's own source→target direction.
    g.addEdgeWithKey("b::a::mentions", "b", "a", { curvature: 0.4 })
    const segs = incidentEdgeSegments(g, "a")
    expect(segs).toHaveLength(1)
    expect(segs[0]).toMatchObject({ x1: 10, y1: 0, x2: 0, y2: 0, curvature: 0.4 })
  })
  it("emits one segment per PARALLEL edge with its own curvature (fan)", () => {
    const g = new Graph({ type: "undirected", multi: true })
    g.addNode("a", { x: 0, y: 0 })
    g.addNode("b", { x: 10, y: 0 })
    g.addEdgeWithKey("a::b::mentions", "a", "b", { curvature: -0.4 })
    g.addEdgeWithKey("a::b::works_on", "a", "b", { curvature: 0.4 })
    const segs = incidentEdgeSegments(g, "a")
    expect(segs).toHaveLength(2)
    expect(segs.map((s) => s.curvature).sort()).toEqual([-0.4, 0.4])
  })
})

describe("curveControlPoint", () => {
  it("returns the midpoint for zero curvature", () => {
    expect(curveControlPoint(0, 0, 10, 4, 0)).toEqual({ x: 5, y: 2 })
  })
  it("offsets perpendicular to the segment by length·curvature (edge-curve shader math)", () => {
    // Horizontal source→target segment (canvas y-down): cp = (mid, -len·c).
    const cp = curveControlPoint(0, 0, 10, 0, 0.25)
    expect(cp.x).toBeCloseTo(5)
    expect(cp.y).toBeCloseTo(-2.5)
    // Vertical segment: offset lands on +x.
    const cpv = curveControlPoint(0, 0, 0, 10, 0.25)
    expect(cpv.x).toBeCloseTo(2.5)
    expect(cpv.y).toBeCloseTo(5)
  })
  it("is symmetric: swapping endpoints and negating curvature yields the same point", () => {
    const a = curveControlPoint(1, 2, 7, -3, 0.4)
    const b = curveControlPoint(7, -3, 1, 2, -0.4)
    expect(b.x).toBeCloseTo(a.x)
    expect(b.y).toBeCloseTo(a.y)
  })
  it("mirrors the offset when curvature flips sign", () => {
    const mid = { x: 5, y: 0 }
    const up = curveControlPoint(0, 0, 10, 0, 0.3)
    const down = curveControlPoint(0, 0, 10, 0, -0.3)
    expect(up.y).toBeCloseTo(-(down.y))
    expect(up.x).toBeCloseTo(mid.x)
    expect(down.x).toBeCloseTo(mid.x)
  })
})
