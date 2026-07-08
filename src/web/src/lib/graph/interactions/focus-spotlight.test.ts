// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { describe, it, expect, vi } from "vitest"
import { createFocusSpotlight, focusNodeAlpha, focusNodeSize } from "./focus-spotlight"

/** Manual rAF/clock harness (same shape as position-morph tests). */
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
    tick(ms: number) {
      now += ms
      const cbs = [...scheduled.values()]
      scheduled.clear()
      for (const cb of cbs) cb(now)
    },
    pending: () => scheduled.size,
  }
}

const ADJACENCY: Record<string, string[]> = {
  a: ["b", "c"],
  b: ["a"],
  c: ["a"],
  lone: [],
}

function makeSpotlight(opts: { reducedMotion?: boolean; clock?: ReturnType<typeof makeClock> } = {}) {
  const refresh = vi.fn()
  const spotlight = createFocusSpotlight({
    getNeighbors: (id) => ADJACENCY[id] ?? [],
    hasNode: (id) => id in ADJACENCY,
    refresh,
    reducedMotion: opts.reducedMotion,
    fadeMs: 180,
    raf: opts.clock?.raf,
    cancelRaf: opts.clock?.cancelRaf,
    now: opts.clock?.now,
  })
  return { spotlight, refresh }
}

describe("focusNodeAlpha / focusNodeSize (pure fade math)", () => {
  it("leaves base alpha untouched at progress 0 and fades 80% at progress 1", () => {
    expect(focusNodeAlpha(0.9, 0)).toBeCloseTo(0.9)
    expect(focusNodeAlpha(0.9, 1)).toBeCloseTo(0.9 * 0.2)
  })
  it("shrinks size by up to 40% with a hit-floor", () => {
    expect(focusNodeSize(10, 0, 2)).toBeCloseTo(10)
    expect(focusNodeSize(10, 1, 2)).toBeCloseTo(6)
    expect(focusNodeSize(1, 1, 2)).toBe(2) // floor wins
  })
})

describe("createFocusSpotlight (reduced motion)", () => {
  it("focuses instantly: neighbors = center + 1-hop, progress 1, one refresh", () => {
    const { spotlight, refresh } = makeSpotlight({ reducedMotion: true })
    spotlight.setCenter("a")
    expect(spotlight.progress()).toBe(1)
    expect([...spotlight.neighbors()!].sort()).toEqual(["a", "b", "c"])
    expect(refresh).toHaveBeenCalled()
  })
  it("clears instantly on setCenter(null)", () => {
    const { spotlight } = makeSpotlight({ reducedMotion: true })
    spotlight.setCenter("a")
    spotlight.setCenter(null)
    expect(spotlight.progress()).toBe(0)
    expect(spotlight.neighbors()).toBeNull()
  })
  it("treats an unknown node id like null", () => {
    const { spotlight } = makeSpotlight({ reducedMotion: true })
    spotlight.setCenter("ghost")
    expect(spotlight.neighbors()).toBeNull()
    expect(spotlight.progress()).toBe(0)
  })
})

describe("createFocusSpotlight (animated)", () => {
  it("ramps progress up over fadeMs, refreshing each frame", () => {
    const clock = makeClock()
    const { spotlight, refresh } = makeSpotlight({ clock })
    spotlight.setCenter("a")
    expect([...spotlight.neighbors()!].sort()).toEqual(["a", "b", "c"])
    clock.tick(90) // mid-ramp
    const mid = spotlight.progress()
    expect(mid).toBeGreaterThan(0)
    expect(mid).toBeLessThan(1)
    clock.tick(200) // past fadeMs
    expect(spotlight.progress()).toBe(1)
    expect(refresh.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(clock.pending()).toBe(0)
  })

  it("keeps the neighbor set during fade-out and clears it only at progress 0", () => {
    const clock = makeClock()
    const { spotlight } = makeSpotlight({ clock })
    spotlight.setCenter("a")
    clock.tick(300) // fully focused
    spotlight.setCenter(null)
    clock.tick(90) // mid fade-out
    expect(spotlight.neighbors()).not.toBeNull() // still easing out
    expect(spotlight.progress()).toBeLessThan(1)
    clock.tick(200) // fade-out complete
    expect(spotlight.progress()).toBe(0)
    expect(spotlight.neighbors()).toBeNull()
  })

  it("interrupts an in-flight ramp when the center changes", () => {
    const clock = makeClock()
    const { spotlight } = makeSpotlight({ clock })
    spotlight.setCenter("a")
    clock.tick(90)
    spotlight.setCenter("b") // re-target mid-ramp
    expect([...spotlight.neighbors()!].sort()).toEqual(["a", "b"])
    clock.tick(300)
    expect(spotlight.progress()).toBe(1)
  })

  it("dispose() cancels any pending frame", () => {
    const clock = makeClock()
    const { spotlight } = makeSpotlight({ clock })
    spotlight.setCenter("a")
    spotlight.dispose()
    expect(clock.pending()).toBe(0)
  })
})
