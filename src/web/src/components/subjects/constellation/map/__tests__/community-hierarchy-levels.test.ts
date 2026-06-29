import { describe, it, expect } from "vitest"
import type { CommunityHull } from "@/lib/api/graph-map"
import type { CommunityHierarchy } from "@/lib/api/community-hierarchy"
import { buildAncestorIndex, buildLevelCommunities, buildLevelSuperEdges, levelForRatio, cleanSummaryLabel } from "../community-hierarchy-levels"

const hull = (id: string, anchor: [number, number], count: number): CommunityHull => ({
  id, count, hull: [[anchor[0]-1, anchor[1]-1], [anchor[0]+1, anchor[1]-1], [anchor[0]+1, anchor[1]+1]], anchor, label: id, top_hubs: [], trust_mix: {},
})

// Two L0 communities (0:1, 0:2) under one L1 parent (1:9).
const HIER: CommunityHierarchy = {
  levels: 2,
  nodes: [
    { community_id: "0:1", level: 0, parent_id: "1:9", member_count: 10, summary: null },
    { community_id: "0:2", level: 0, parent_id: "1:9", member_count: 30, summary: null },
    { community_id: "1:9", level: 1, parent_id: null, member_count: 40, summary: "Platform cluster" },
  ],
}
const L0 = [hull("0:1", [0, 0], 10), hull("0:2", [10, 0], 30)]

describe("buildAncestorIndex", () => {
  it("resolves levels, ancestors, children", () => {
    const ix = buildAncestorIndex(HIER)
    expect(ix.levelOf("0:1")).toBe(0)
    expect(ix.ancestorAt("0:1", 1)).toBe("1:9")
    expect(ix.ancestorAt("0:1", 0)).toBe("0:1")
    expect(ix.childrenOf("1:9").sort()).toEqual(["0:1", "0:2"])
    expect(ix.maxLevel).toBe(1)
  })
})

describe("buildLevelCommunities", () => {
  it("level 0 is the input; level 1 is one synthetic community", () => {
    const levels = buildLevelCommunities(L0, HIER)
    expect(levels).toHaveLength(2)
    expect(levels[0]).toEqual(L0)
    expect(levels[1]).toHaveLength(1)
    const l1 = levels[1][0]
    expect(l1.id).toBe("1:9")
    expect(l1.count).toBe(40)
    expect(l1.label).toContain("Platform")
    // count-weighted centroid of (0,0)@10 and (10,0)@30 = x = (0*10+10*30)/40 = 7.5
    expect(l1.anchor[0]).toBeCloseTo(7.5)
    // hull is the union of descendant hull points
    expect(l1.hull.length).toBeGreaterThanOrEqual(L0[0].hull.length)
  })
  it("falls back to a single level when hierarchy is undefined", () => {
    const levels = buildLevelCommunities(L0, undefined)
    expect(levels).toEqual([L0])
  })
})

describe("cleanSummaryLabel", () => {
  it("strips the LLM boilerplate lead-in", () => {
    expect(cleanSummaryLabel("The theme revolves around data encoding")).toBe("data encoding")
    expect(cleanSummaryLabel("This community is about the Python ecosystem")).toBe("Python ecosystem")
  })
  it("keeps a summary that has no boilerplate", () => {
    expect(cleanSummaryLabel("Platform cluster")).toBe("Platform cluster")
  })
  it("returns null for null / boilerplate-only", () => {
    expect(cleanSummaryLabel(null)).toBeNull()
    expect(cleanSummaryLabel("The theme revolves around")).toBeNull()
  })
})

describe("buildLevelCommunities label fallback", () => {
  it("names an unsummarized level-L community by its biggest sub-community", () => {
    // 0:2 (count 30) dominates 0:1 (count 10); the L1 node 1:9 has no summary.
    const hierNoSummary: CommunityHierarchy = {
      levels: 2,
      nodes: [
        { community_id: "0:1", level: 0, parent_id: "1:9", member_count: 10, summary: null },
        { community_id: "0:2", level: 0, parent_id: "1:9", member_count: 30, summary: null },
        { community_id: "1:9", level: 1, parent_id: null, member_count: 40, summary: null },
      ],
    }
    const l0 = [hull("0:1", [0, 0], 10), hull("0:2", [10, 0], 30)]
    l0[1].label = "PYTHON"
    const levels = buildLevelCommunities(l0, hierNoSummary)
    expect(levels[1][0].label).toBe("PYTHON")
  })
})

describe("buildLevelSuperEdges", () => {
  it("level 1 aggregates edges between L1 ancestors (cross-L0 within same L1 collapse away)", () => {
    const entities = [{ community: "0:1" }, { community: "0:2" }]
    const links: [number, number, number, string][] = [[0, 1, 5, "co_mention"]]
    const lvls = buildLevelSuperEdges(entities, links, HIER)
    // at L1 both entities map to 1:9 → same community → edge drops
    expect(lvls[1]).toHaveLength(0)
    // at L0 the edge survives (0:1 ↔ 0:2)
    expect(lvls[0]).toHaveLength(1)
  })
})

describe("levelForRatio", () => {
  it("maps ratio bands to levels, clamped", () => {
    expect(levelForRatio(1.5, 3, 1.4, 1.0)).toBe(0)
    expect(levelForRatio(2.5, 3, 1.4, 1.0)).toBe(1)
    expect(levelForRatio(99, 3, 1.4, 1.0)).toBe(2)  // clamp
    expect(levelForRatio(0.5, 3, 1.4, 1.0)).toBe(0) // below band → level 0 floor
  })
})
