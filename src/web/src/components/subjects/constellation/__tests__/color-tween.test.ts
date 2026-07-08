// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for the lens-switch color-tween helpers (B6). Pure math — no
// WebGL, no jsdom.

import { describe, it, expect } from "vitest"
import { colorTweenK, mixChannel, COLOR_TWEEN_S } from "../color-tween"

describe("colorTweenK", () => {
  it("is 0 at the start of the tween", () => {
    expect(colorTweenK(10, 10)).toBe(0)
  })

  it("is 1 at the end of the tween window", () => {
    expect(colorTweenK(10 + COLOR_TWEEN_S, 10)).toBeCloseTo(1)
  })

  it("is 0.5 at the midpoint", () => {
    expect(colorTweenK(10 + COLOR_TWEEN_S / 2, 10)).toBeCloseTo(0.5)
  })

  it("clamps to 1 past the window (no overshoot)", () => {
    expect(colorTweenK(100, 10)).toBe(1)
  })

  it("clamps to 0 before the start (no negative)", () => {
    expect(colorTweenK(5, 10)).toBe(0)
  })

  it("returns 1 immediately for a zero/negative duration (snap)", () => {
    expect(colorTweenK(10, 10, 0)).toBe(1)
    expect(colorTweenK(10, 10, -1)).toBe(1)
  })

  it("honors a custom duration", () => {
    expect(colorTweenK(11, 10, 2)).toBeCloseTo(0.5)
  })
})

describe("mixChannel", () => {
  it("returns `from` at k=0", () => {
    expect(mixChannel(0.2, 0.8, 0)).toBeCloseTo(0.2)
  })

  it("returns `to` at k=1", () => {
    expect(mixChannel(0.2, 0.8, 1)).toBeCloseTo(0.8)
  })

  it("interpolates linearly at the midpoint", () => {
    expect(mixChannel(0.2, 0.8, 0.5)).toBeCloseTo(0.5)
  })

  it("works when `to` is smaller than `from`", () => {
    expect(mixChannel(1.0, 0.0, 0.25)).toBeCloseTo(0.75)
  })
})
