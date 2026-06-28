import { describe, it, expect } from "vitest"
import Graph from "graphology"
import { incidentEdgeSegments } from "../highlight-edges"

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
})
