// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
import { describe, it, expect, vi } from "vitest"
import Graph from "graphology"
import { interpolateFrame, easeOutCubic, morphPositions } from "./position-morph"

function makeGraph(): Graph {
  const g = new Graph()
  g.addNode("a", { x: 0, y: 0, size: 4 })
  g.addNode("b", { x: 10, y: -10, size: 8 })
  return g
}

/** Manual rAF/clock harness so morph frames are driven deterministically. */
function makeClock() {
  let now = 0
  let nextHandle = 1
  const scheduled = new Map<number, FrameRequestCallback>()
  return {
    now: () => now,
    raf: (cb: FrameRequestCallback) => {
      const h = nextHandle++
      scheduled.set(h, cb)
      return h
    },
    cancelRaf: (h: number) => {
      scheduled.delete(h)
    },
    /** Advance time and fire every scheduled callback exactly once. */
    tick(ms: number) {
      now += ms
      const cbs = [...scheduled.values()]
      scheduled.clear()
      for (const cb of cbs) cb(now)
    },
    pending: () => scheduled.size,
  }
}

describe("easeOutCubic", () => {
  it("anchors at 0 and 1, front-loads motion (ease-out)", () => {
    expect(easeOutCubic(0)).toBe(0)
    expect(easeOutCubic(1)).toBe(1)
    expect(easeOutCubic(0.5)).toBeGreaterThan(0.5)
  })
})

describe("interpolateFrame", () => {
  it("returns `from` values at t=0 and `to` values at t=1", () => {
    const from = { x: 0, y: 5 }
    const to = { x: 10, y: -5 }
    expect(interpolateFrame(from, to, 0, (t) => t)).toEqual(from)
    expect(interpolateFrame(from, to, 1, (t) => t)).toEqual(to)
  })
  it("interpolates every numeric key with the supplied ease", () => {
    const r = interpolateFrame({ x: 0, size: 2 }, { x: 10, size: 4 }, 0.5, (t) => t)
    expect(r.x).toBeCloseTo(5)
    expect(r.size).toBeCloseTo(3)
  })
})

describe("morphPositions", () => {
  it("snaps synchronously under reduced motion and fires onFrame + onDone", () => {
    const g = makeGraph()
    const onFrame = vi.fn()
    const onDone = vi.fn()
    morphPositions(g, new Map([["a", { x: 100, y: 50 }]]), {
      reducedMotion: true,
      onFrame,
      onDone,
    })
    expect(g.getNodeAttribute("a", "x")).toBe(100)
    expect(g.getNodeAttribute("a", "y")).toBe(50)
    expect(onFrame).toHaveBeenCalledTimes(1)
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it("tweens toward targets across frames and lands exactly on them", () => {
    const g = makeGraph()
    const clock = makeClock()
    const onFrame = vi.fn()
    const onDone = vi.fn()
    morphPositions(
      g,
      new Map<string, Record<string, number>>([
        ["a", { x: 100, y: 0 }],
        ["b", { x: 10, y: 30, size: 12 }],
      ]),
      { durationMs: 100, onFrame, onDone, raf: clock.raf, cancelRaf: clock.cancelRaf, now: clock.now },
    )
    clock.tick(50) // mid-flight
    const midX = g.getNodeAttribute("a", "x") as number
    expect(midX).toBeGreaterThan(0)
    expect(midX).toBeLessThan(100)
    expect(onFrame).toHaveBeenCalled()
    expect(onDone).not.toHaveBeenCalled()

    clock.tick(60) // past duration → settle
    expect(g.getNodeAttribute("a", "x")).toBe(100)
    expect(g.getNodeAttribute("b", "x")).toBe(10)
    expect(g.getNodeAttribute("b", "y")).toBe(30)
    expect(g.getNodeAttribute("b", "size")).toBe(12)
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(clock.pending()).toBe(0)
  })

  it("cancel() stops the tween without firing onDone", () => {
    const g = makeGraph()
    const clock = makeClock()
    const onDone = vi.fn()
    const handle = morphPositions(g, new Map([["a", { x: 100, y: 0 }]]), {
      durationMs: 100,
      onDone,
      raf: clock.raf,
      cancelRaf: clock.cancelRaf,
      now: clock.now,
    })
    clock.tick(30)
    const frozenX = g.getNodeAttribute("a", "x") as number
    handle.cancel()
    clock.tick(100)
    expect(g.getNodeAttribute("a", "x")).toBe(frozenX)
    expect(onDone).not.toHaveBeenCalled()
    expect(clock.pending()).toBe(0)
  })

  it("skips target ids that are not in the graph", () => {
    const g = makeGraph()
    expect(() =>
      morphPositions(g, new Map([["ghost", { x: 1, y: 1 }]]), { reducedMotion: true }),
    ).not.toThrow()
  })
})
