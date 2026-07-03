import { describe, it, expect } from "vitest"
import {
  buildSuperNodes3D,
  superRadius,
  collapsedLevelForDistance,
  COLLAPSE_IN,
  COLLAPSE_OUT,
  LEVEL_STEP_3D,
} from "../supernodes-3d"

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const idAncestor = (id: string, _level: number) => id // identity: treat community as its own ancestor

describe("buildSuperNodes3D", () => {
  it("computes the centroid of each community's members", () => {
    const ents = [
      { x: 0, y: 0, z: 0, community: "A" },
      { x: 2, y: 0, z: 0, community: "A" },
      { x: 10, y: 10, z: 10, community: "B" },
    ]
    const supers = buildSuperNodes3D(ents, idAncestor, 0)
    const a = supers.find((s) => s.id === "A")!
    expect(a.x).toBeCloseTo(1)
    expect(a.count).toBe(2)
    const b = supers.find((s) => s.id === "B")!
    expect(b.x).toBeCloseTo(10)
    expect(b.count).toBe(1)
  })
  it("skips members with no community", () => {
    const supers = buildSuperNodes3D([{ x: 0, y: 0, z: 0, community: null }], idAncestor, 0)
    expect(supers).toHaveLength(0)
  })
  it("groups by the ancestor at the requested level", () => {
    const anc = (id: string, level: number) => (level >= 1 ? "ROOT" : id)
    const ents = [
      { x: 0, y: 0, z: 0, community: "A" },
      { x: 4, y: 0, z: 0, community: "B" },
    ]
    const supers = buildSuperNodes3D(ents, anc, 1)
    expect(supers).toHaveLength(1)
    expect(supers[0].id).toBe("ROOT")
    expect(supers[0].x).toBeCloseTo(2)
    expect(supers[0].count).toBe(2)
  })
})

describe("superRadius", () => {
  it("grows monotonically with member count", () => {
    expect(superRadius(100)).toBeGreaterThan(superRadius(1))
  })
})

describe("collapsedLevelForDistance", () => {
  it("never collapses when there is no hierarchy (maxLevel < 0)", () => {
    expect(collapsedLevelForDistance(1000, null, -1)).toBeNull()
  })
  it("stays expanded below COLLAPSE_IN", () => {
    expect(collapsedLevelForDistance(COLLAPSE_IN - 1, null, 3)).toBeNull()
  })
  it("collapses to level 0 once distance reaches COLLAPSE_IN", () => {
    expect(collapsedLevelForDistance(COLLAPSE_IN, null, 3)).toBe(0)
  })
  it("does not expand again until distance drops to COLLAPSE_OUT (hysteresis band)", () => {
    // Already collapsed at level 0; camera comes back in partway but stays
    // above COLLAPSE_OUT — should hold collapsed, not flicker back to members.
    const midway = (COLLAPSE_IN + COLLAPSE_OUT) / 2
    expect(collapsedLevelForDistance(midway, 0, 3)).toBe(0)
  })
  it("expands once distance drops to COLLAPSE_OUT", () => {
    expect(collapsedLevelForDistance(COLLAPSE_OUT, 0, 3)).toBeNull()
  })
  it("advances to a higher Leiden level as the camera zooms further out while collapsed", () => {
    expect(collapsedLevelForDistance(COLLAPSE_IN + LEVEL_STEP_3D, 0, 3)).toBe(1)
  })
  it("clamps the level at maxLevel even far past COLLAPSE_IN", () => {
    expect(collapsedLevelForDistance(COLLAPSE_IN + LEVEL_STEP_3D * 50, 0, 3)).toBe(3)
  })
})
