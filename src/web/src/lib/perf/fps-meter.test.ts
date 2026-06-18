// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest"
import { createFpsMeter } from "./fps-meter"

describe("createFpsMeter", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.spyOn(performance, "now").mockReturnValue(0)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("returns null current() until first window completes", () => {
    const meter = createFpsMeter({ windowMs: 1000 })
    meter.tick()
    expect(meter.current()).toBeNull()
  })

  it("computes avg FPS over a 1s window with 60 ticks ≈ 60fps", () => {
    let now = 0
    const spy = vi.spyOn(performance, "now").mockImplementation(() => now)
    const onWindow = vi.fn()
    const meter = createFpsMeter({ windowMs: 1000, onWindow })
    // Simulate 60 evenly-spaced frames over 1 second
    for (let i = 0; i < 60; i++) {
      now = (i + 1) * (1000 / 60)
      meter.tick()
    }
    expect(onWindow).toHaveBeenCalled()
    const stats = onWindow.mock.calls[0][0]
    expect(stats.avgFps).toBeGreaterThanOrEqual(58)
    expect(stats.avgFps).toBeLessThanOrEqual(62)
    expect(stats.frames).toBeGreaterThanOrEqual(58)
    spy.mockRestore()
  })

  it("captures min/max instantaneous FPS within window", () => {
    let now = 0
    vi.spyOn(performance, "now").mockImplementation(() => now)
    const meter = createFpsMeter({ windowMs: 1000 })
    // 30 fast frames (5ms apart → 200fps) then a 200ms stall (5fps)
    for (let i = 0; i < 30; i++) {
      now += 5
      meter.tick()
    }
    now += 200
    meter.tick()
    // Pad to fill window
    while (now < 1000) {
      now += 16
      meter.tick()
    }
    const stats = meter.current()
    expect(stats).not.toBeNull()
    if (stats) {
      expect(stats.maxFps).toBeGreaterThan(100)  // saw fast frames
      expect(stats.minFps).toBeLessThan(10)      // saw the stall
    }
  })

  it("stop() halts further ticks from updating state", () => {
    const meter = createFpsMeter({ windowMs: 100 })
    meter.stop()
    const now = 200
    vi.spyOn(performance, "now").mockImplementation(() => now)
    meter.tick()
    meter.tick()
    expect(meter.current()).toBeNull()
  })
})
