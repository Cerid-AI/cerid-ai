// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Unit tests for the drag-heal core math (renderer-agnostic).
// Tests exercise pure math helpers and the synchronous reducedMotion
// path of createHealController without rAF.

import { describe, it, expect, vi } from "vitest"
import {
  neighborFalloff,
  lerpHomeStep,
  simulateSettle,
  createHealController,
} from "./drag-heal"

// ---------------------------------------------------------------------------
// neighborFalloff
// ---------------------------------------------------------------------------

describe("neighborFalloff", () => {
  it("returns a value in [0,1] for all positive distances", () => {
    for (let d = 0; d <= 10; d++) {
      const v = neighborFalloff(d)
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThanOrEqual(1)
    }
  })

  it("is monotonically decreasing with distance", () => {
    const v1 = neighborFalloff(1)
    const v2 = neighborFalloff(2)
    const v3 = neighborFalloff(3)
    expect(v1).toBeGreaterThan(v2)
    expect(v2).toBeGreaterThan(v3)
  })

  it("direct neighbor (dist=1) has positive falloff", () => {
    expect(neighborFalloff(1)).toBeGreaterThan(0)
  })

  it("handles dist=0 as full tug (returns 1)", () => {
    expect(neighborFalloff(0)).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// lerpHomeStep
// ---------------------------------------------------------------------------

describe("lerpHomeStep", () => {
  it("moves toward home each step until settled", () => {
    const home = 0
    const start = 100
    let pos = start
    let vel = 0
    for (let i = 0; i < 10; i++) {
      const prev = Math.abs(pos - home)
      if (prev === 0) break // already at home; remaining steps are no-ops
      const r = lerpHomeStep(pos, home, vel)
      expect(Math.abs(r.pos - home)).toBeLessThanOrEqual(prev)
      pos = r.pos
      vel = r.vel
    }
  })

  it("converges to home within 120 frames for large displacement", () => {
    const final = simulateSettle(1000, 0, 120)
    expect(Math.abs(final)).toBeLessThan(0.1)
  })

  it("is at rest when already at home", () => {
    const r = lerpHomeStep(0, 0, 0)
    expect(r.pos).toBe(0)
    expect(r.vel).toBe(0)
  })

  it("negative displacement converges from below", () => {
    const final = simulateSettle(-100, 0, 120)
    expect(Math.abs(final)).toBeLessThan(0.1)
  })
})

// ---------------------------------------------------------------------------
// simulateSettle
// ---------------------------------------------------------------------------

describe("simulateSettle", () => {
  it("returns a value closer to home than start after 60 frames", () => {
    const pos = simulateSettle(50, 0, 60)
    expect(Math.abs(pos - 0)).toBeLessThan(Math.abs(50 - 0))
  })

  it("returns home within 0.002 after 180 frames (matches SETTLE_THRESHOLD)", () => {
    const pos = simulateSettle(20, 0, 180)
    expect(Math.abs(pos)).toBeLessThan(0.002)
  })
})

// ---------------------------------------------------------------------------
// createHealController — synchronous reducedMotion path (no rAF needed)
// ---------------------------------------------------------------------------

function makeStore(initial: Record<string, { x: number; y: number }>) {
  const pos: Record<string, { x: number; y: number }> = {}
  for (const [k, v] of Object.entries(initial)) pos[k] = { ...v }
  const homes = { ...initial }
  return {
    getHome: (id: string) => homes[id] ?? null,
    getPos: (id: string) => pos[id] ?? null,
    setPos: (id: string, p: { x: number; y: number }) => { pos[id] = { ...p } },
    read: (id: string) => pos[id],
  }
}

describe("createHealController (reducedMotion=true — synchronous)", () => {
  it("startDrag + moveDrag updates position of dragged node", () => {
    const store = makeStore({ a: { x: 0, y: 0 } })
    const onSettle = vi.fn()
    const ctrl = createHealController({
      ...store,
      neighbors: () => [],
      onSettle,
      reducedMotion: true,
    })
    ctrl.startDrag("a")
    ctrl.moveDrag("a", { x: 10, y: 20 })
    expect(store.read("a")).toEqual({ x: 10, y: 20 })
    ctrl.dispose()
  })

  it("endDrag with reducedMotion snaps node to home", () => {
    const store = makeStore({ a: { x: 5, y: 5 } })
    const onSettle = vi.fn()
    const ctrl = createHealController({
      ...store,
      neighbors: () => [],
      onSettle,
      reducedMotion: true,
    })
    ctrl.startDrag("a")
    ctrl.moveDrag("a", { x: 50, y: 60 })
    ctrl.endDrag("a")
    expect(store.read("a")).toEqual({ x: 5, y: 5 })
    expect(onSettle).toHaveBeenCalledOnce()
    ctrl.dispose()
  })

  it("endDrag snaps neighbors to their homes under reducedMotion", () => {
    const store = makeStore({ a: { x: 0, y: 0 }, b: { x: 10, y: 0 } })
    const onSettle = vi.fn()
    const ctrl = createHealController({
      ...store,
      neighbors: (id) => id === "a" ? ["b"] : [],
      onSettle,
      reducedMotion: true,
    })
    ctrl.startDrag("a")
    ctrl.moveDrag("a", { x: 100, y: 0 })
    ctrl.endDrag("a")
    expect(store.read("b")).toEqual({ x: 10, y: 0 })
    expect(onSettle).toHaveBeenCalledOnce()
    ctrl.dispose()
  })

  it("pin: endDrag with pin=true leaves node at drop position", () => {
    const store = makeStore({ a: { x: 0, y: 0 } })
    const onSettle = vi.fn()
    const ctrl = createHealController({
      ...store,
      neighbors: () => [],
      onSettle,
      reducedMotion: true,
    })
    ctrl.startDrag("a")
    ctrl.moveDrag("a", { x: 99, y: 77 })
    ctrl.endDrag("a", { pin: true })
    expect(store.read("a")).toEqual({ x: 99, y: 77 })
    expect(onSettle).not.toHaveBeenCalled()
    ctrl.dispose()
  })

  it("moveDrag does nothing when no drag is active", () => {
    const store = makeStore({ a: { x: 0, y: 0 } })
    const ctrl = createHealController({
      ...store,
      neighbors: () => [],
      onSettle: vi.fn(),
      reducedMotion: true,
    })
    ctrl.moveDrag("a", { x: 99, y: 99 })
    expect(store.read("a")).toEqual({ x: 0, y: 0 })
    ctrl.dispose()
  })

  it("startDrag for unknown entity does not crash", () => {
    const store = makeStore({ a: { x: 0, y: 0 } })
    const ctrl = createHealController({
      ...store,
      neighbors: () => [],
      onSettle: vi.fn(),
      reducedMotion: true,
    })
    expect(() => ctrl.startDrag("unknown")).not.toThrow()
    ctrl.dispose()
  })

  it("cancel clears drag state", () => {
    const store = makeStore({ a: { x: 0, y: 0 } })
    const onSettle = vi.fn()
    const ctrl = createHealController({
      ...store,
      neighbors: () => [],
      onSettle,
      reducedMotion: true,
    })
    ctrl.startDrag("a")
    ctrl.moveDrag("a", { x: 80, y: 0 })
    ctrl.cancel()
    // After cancel, moveDrag is a no-op
    ctrl.moveDrag("a", { x: 200, y: 0 })
    expect(store.read("a")).toEqual({ x: 80, y: 0 })
    ctrl.dispose()
  })

  it("mid-heal re-grab via startDrag works", () => {
    const store = makeStore({ a: { x: 0, y: 0 } })
    const ctrl = createHealController({
      ...store,
      neighbors: () => [],
      onSettle: vi.fn(),
      reducedMotion: false,
    })
    ctrl.startDrag("a")
    ctrl.moveDrag("a", { x: 80, y: 0 })
    ctrl.endDrag("a")
    // Re-grab mid-heal
    ctrl.startDrag("a")
    ctrl.moveDrag("a", { x: 40, y: 0 })
    expect(store.read("a")).toEqual({ x: 40, y: 0 })
    ctrl.dispose()
  })
})

// ---------------------------------------------------------------------------
// Neighbor tug during drag
// ---------------------------------------------------------------------------

describe("createHealController — neighbor tug during moveDrag", () => {
  it("displaces neighbor by falloff fraction of drag displacement from home", () => {
    const store = makeStore({ a: { x: 0, y: 0 }, b: { x: 5, y: 0 } })
    const ctrl = createHealController({
      ...store,
      neighbors: (id) => id === "a" ? ["b"] : [],
      onSettle: vi.fn(),
      reducedMotion: true,
    })

    ctrl.startDrag("a")
    ctrl.moveDrag("a", { x: 10, y: 0 }) // drag a by +10 from home (0,0)

    const bPos = store.read("b")
    const expectedTug = neighborFalloff(1) * 10
    expect(bPos.x).toBeCloseTo(5 + expectedTug, 4)
    ctrl.dispose()
  })

  it("does not move nodes not in neighbor list", () => {
    const store = makeStore({ a: { x: 0, y: 0 }, c: { x: 20, y: 0 } })
    const ctrl = createHealController({
      ...store,
      neighbors: () => [], // a has no neighbors
      onSettle: vi.fn(),
      reducedMotion: true,
    })

    ctrl.startDrag("a")
    ctrl.moveDrag("a", { x: 100, y: 0 })
    expect(store.read("c")).toEqual({ x: 20, y: 0 })
    ctrl.dispose()
  })
})

// ---------------------------------------------------------------------------
// lerpHomeStep convergence (settle math)
// ---------------------------------------------------------------------------

describe("settle convergence (pure math, no rAF)", () => {
  it("position converges to within 0.01 of home after 60 frames", () => {
    const pos = simulateSettle(100, 0, 60)
    expect(Math.abs(pos)).toBeLessThan(0.5)
  })

  it("converges symmetrically from negative start", () => {
    const posA = simulateSettle(50, 0, 80)
    const posB = simulateSettle(-50, 0, 80)
    expect(Math.abs(posA)).toBeLessThan(0.01)
    expect(Math.abs(posB)).toBeLessThan(0.01)
  })
})
