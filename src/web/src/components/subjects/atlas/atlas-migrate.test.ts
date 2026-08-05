// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
import { describe, it, expect } from "vitest"
import Graph from "graphology"
import {
  seedWarmPositions,
  planMigrationTargets,
  syncCommonNodeAttrs,
  syncEdges,
  EXIT_SIZE,
} from "./atlas-migrate"

const ZERO_JITTER = () => 0

/** Minimal Atlas-shaped node attrs for migration tests. */
function attrs(over: Record<string, unknown> = {}) {
  return {
    x: 0,
    y: 0,
    size: 8,
    color: "#A0A0FF", // drift-allowed: test stub only
    community: "c1",
    trust_state: "verified",
    entityType: "Person",
    label: "n",
    ...over,
  }
}

function mkLive(): Graph {
  const g = new Graph({ multi: false, allowSelfLoops: false })
  g.addNode("focal", attrs({ x: 5, y: 5 }))
  g.addNode("keep", attrs({ x: 10, y: -10 }))
  g.addNode("gone", attrs({ x: -3, y: 7 }))
  g.addEdgeWithKey("focal::keep::mentions", "focal", "keep", { color: "#CCCCCC", size: 1 }) // drift-allowed: test stub only
  g.addEdgeWithKey("focal::gone::mentions", "focal", "gone", { color: "#CCCCCC", size: 1 }) // drift-allowed: test stub only
  return g
}

function mkNext(): Graph {
  const g = new Graph({ multi: false, allowSelfLoops: false })
  g.addNode("focal", attrs({ x: 0, y: 0 }))
  g.addNode("keep", attrs({ x: 0, y: 0, community: "c2", size: 12 }))
  g.addNode("fresh", attrs({ x: 0, y: 0 })) // enters; connected to keep
  g.addEdgeWithKey("focal::keep::mentions", "focal", "keep", { color: "#CCCCCC", size: 1 }) // drift-allowed: test stub only
  g.addEdgeWithKey("keep::fresh::mentions", "keep", "fresh", { color: "#CCCCCC", size: 2 }) // drift-allowed: test stub only
  return g
}

describe("seedWarmPositions", () => {
  it("copies live positions onto common nodes in next", () => {
    const live = mkLive()
    const next = mkNext()
    seedWarmPositions(next, live, "focal", ZERO_JITTER)
    expect(next.getNodeAttribute("focal", "x")).toBe(5)
    expect(next.getNodeAttribute("keep", "x")).toBe(10)
    expect(next.getNodeAttribute("keep", "y")).toBe(-10)
  })

  it("spawns entering nodes at a live neighbor's position and returns the spawn map", () => {
    const live = mkLive()
    const next = mkNext()
    const spawns = seedWarmPositions(next, live, "focal", ZERO_JITTER)
    // "fresh" neighbors "keep" in next; keep lives at (10,-10)
    expect(next.getNodeAttribute("fresh", "x")).toBe(10)
    expect(next.getNodeAttribute("fresh", "y")).toBe(-10)
    expect(spawns.get("fresh")).toEqual({ x: 10, y: -10 })
    expect(spawns.has("keep")).toBe(false) // common nodes don't spawn
  })

  it("falls back to the live focal position for enters with no live neighbor", () => {
    const live = mkLive()
    const next = mkNext()
    next.addNode("orphan", attrs())
    seedWarmPositions(next, live, "focal", ZERO_JITTER)
    expect(next.getNodeAttribute("orphan", "x")).toBe(5)
    expect(next.getNodeAttribute("orphan", "y")).toBe(5)
  })
})

describe("planMigrationTargets", () => {
  it("classifies enter/exit and builds morph targets", () => {
    const live = mkLive()
    const next = mkNext()
    // Simulate post-layout positions on next
    next.setNodeAttribute("keep", "x", 20)
    next.setNodeAttribute("keep", "y", 30)
    next.setNodeAttribute("fresh", "x", 15)
    const plan = planMigrationTargets(live, next)
    expect(plan.enter).toEqual(["fresh"])
    expect(plan.exit).toEqual(["gone"])
    // Common node tweens to next position AND next size (mention growth)
    expect(plan.targets.get("keep")).toMatchObject({ x: 20, y: 30, size: 12 })
    // Enter node tweens size 0→final and spawnProgress 0→1
    expect(plan.targets.get("fresh")).toMatchObject({ x: 15, size: 8, spawnProgress: 1 })
    // Exit node shrinks AND fades in place (spawnProgress 1→0 drives the
    // reducers' node + incident-edge alpha fade)
    expect(plan.targets.get("gone")).toMatchObject({ size: EXIT_SIZE, spawnProgress: 0 })
  })
})

describe("syncCommonNodeAttrs", () => {
  it("copies styling attrs but never positions or transient hover state", () => {
    const live = mkLive()
    const next = mkNext()
    live.setNodeAttribute("keep", "highlighted", true)
    syncCommonNodeAttrs(live, next, ["focal", "keep"])
    expect(live.getNodeAttribute("keep", "community")).toBe("c2")
    expect(live.getNodeAttribute("keep", "x")).toBe(10) // position untouched
    expect(live.getNodeAttribute("keep", "highlighted")).toBe(true) // transient untouched
    // size is morphed, not snapped
    expect(live.getNodeAttribute("keep", "size")).toBe(8)
  })
})

describe("syncEdges", () => {
  it("drops live-only edges and adds next-only edges with deterministic keys", () => {
    const live = mkLive()
    const next = mkNext()
    // fresh must exist in live before its edges can be added
    live.addNode("fresh", attrs())
    syncEdges(live, next)
    expect(live.hasEdge("focal::keep::mentions")).toBe(true)
    expect(live.hasEdge("focal::gone::mentions")).toBe(false) // dropped
    expect(live.hasEdge("keep::fresh::mentions")).toBe(true) // added
    expect(live.getEdgeAttribute("keep::fresh::mentions", "size")).toBe(2)
  })

  it("defers drops for edges incident to exit nodes (they fall with dropNode on settle)", () => {
    const live = mkLive()
    const next = mkNext()
    live.addNode("fresh", attrs())
    // Stale live-only edge between two SURVIVING nodes must still drop now.
    live.addEdgeWithKey("keep::focal::works_on", "keep", "focal", { color: "#CCCCCC", size: 1 }) // drift-allowed: test stub only
    syncEdges(live, next, new Set(["gone"]))
    // Exit-incident edge survives the sync — exit nodes keep their edges
    // for the whole morph instead of reading as de-linked.
    expect(live.hasEdge("focal::gone::mentions")).toBe(true)
    // Non-exit live-only edge still drops immediately.
    expect(live.hasEdge("keep::focal::works_on")).toBe(false)
    // Next-only edges still arrive.
    expect(live.hasEdge("keep::fresh::mentions")).toBe(true)
    // The deferred edge falls automatically with the exit node on settle.
    live.dropNode("gone")
    expect(live.hasEdge("focal::gone::mentions")).toBe(false)
  })
})
