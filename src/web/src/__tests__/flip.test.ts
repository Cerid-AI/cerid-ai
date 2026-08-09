// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { snapshotPositions, playFromSnapshot, flip } from "@/lib/flip"

describe("FLIP helper", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: false }),
    })
  })

  it("snapshotPositions ignores nullish entries", () => {
    const a = document.createElement("div")
    const snaps = snapshotPositions([a, null, undefined])
    expect(snaps).toHaveLength(1)
    expect(snaps[0].el).toBe(a)
  })

  it("playFromSnapshot is a no-op when prefers-reduced-motion is set", async () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: true }),
    })
    const el = document.createElement("div")
    document.body.appendChild(el)
    const snaps = snapshotPositions([el])
    await playFromSnapshot(snaps, { duration: 10 })
    expect(el.style.transform).toBe("")
  })

  it("playFromSnapshot is a no-op when no snapshots", async () => {
    await expect(playFromSnapshot([], { duration: 10 })).resolves.toBeUndefined()
  })

  it("flip composes capture, mutate, and play", async () => {
    const el = document.createElement("div")
    document.body.appendChild(el)
    const mutate = vi.fn()
    await flip([el], mutate, { duration: 10 })
    expect(mutate).toHaveBeenCalledOnce()
  })

  it("a second concurrent flip on the same element cancels the first cleanly", async () => {
    const el = document.createElement("div")
    el.style.position = "absolute"
    el.style.left = "0px"
    document.body.appendChild(el)
    // Stub getBoundingClientRect to simulate movement.
    let leftValue = 0
    vi.spyOn(el, "getBoundingClientRect").mockImplementation(
      () => ({ left: leftValue, top: 0, right: 10, bottom: 10, width: 10, height: 10, x: leftValue, y: 0, toJSON: () => ({}) } as DOMRect),
    )
    const first = flip([el], () => { leftValue = 100 }, { duration: 50 })
    const second = flip([el], () => { leftValue = 200 }, { duration: 50 })
    await Promise.all([first, second])
    // After the second flip finishes, inline styles must be cleared.
    expect(el.style.transform).toBe("")
    expect(el.style.transition).toBe("")
  })
})
