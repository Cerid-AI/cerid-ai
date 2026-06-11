// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for the draw-node-hover module.
// These tests run in jsdom and use a stub canvas that records ctx.font
// and ctx.fillStyle to verify the token-aware rendering contract.

import { describe, expect, it, vi } from "vitest"
import type { MapTokens } from "@/components/subjects/constellation/map/community-layer"
import { makeDrawNodeHover, resolveCanvasFont } from "./draw-node-hover"

// ---------------------------------------------------------------------------
// Stub tokens (same as identity.test.ts)
// ---------------------------------------------------------------------------

const TOKENS: MapTokens = {
  clusters: [
    "#AA0000", "#00AA00", "#0000AA", "#AAAA00", // drift-allowed: test stub only
    "#AA00AA", "#00AAAA", "#AA5500", "#5500AA", // drift-allowed: test stub only
  ],
  clusterOther: "#555555", // drift-allowed: test stub only
  domains: [
    "#D10000", "#CC4400", "#AA8800", "#558800", // drift-allowed: test stub only (slots 0-3)
    "#008844", "#007755", "#006688", "#2244AA", // drift-allowed: test stub only (slots 4-7)
    "#4400AA", "#770088", "#AA0066", "#CC0033", // drift-allowed: test stub only (slots 8-11)
  ],
  domainOther:   "#666666", // drift-allowed: test stub only
  edge:          "#CCCCCC", // drift-allowed: test stub only
  dim:           "#888888", // drift-allowed: test stub only
  interaction:   "#00E5D8", // drift-allowed: test stub only
  foreground:    "#111111", // drift-allowed: test stub only
  background:    "#FFFFFF", // drift-allowed: test stub only
  trustVerified:   "#004488", // drift-allowed: test stub only
  trustPartial:    "#884400", // drift-allowed: test stub only
  trustUnverified: "#880000", // drift-allowed: test stub only
  grid:          "#EEEEEE", // drift-allowed: test stub only
  fontSans:      "Inter, system-ui, sans-serif", // drift-allowed: test stub only
}

// Minimal stub settings
const SETTINGS = {
  labelSize: 11,
  labelFont: "system-ui",
  labelWeight: "400",
  labelColor: { color: "#111111" }, // drift-allowed: test stub only
} as Parameters<ReturnType<typeof makeDrawNodeHover>>[2]

// ---------------------------------------------------------------------------
// makeDrawNodeHover
// ---------------------------------------------------------------------------

describe("makeDrawNodeHover", () => {
  it("returns a function", () => {
    const fn = makeDrawNodeHover(TOKENS)
    expect(typeof fn).toBe("function")
  })

  it("uses tokens.background as the plate fill color", () => {
    const fn = makeDrawNodeHover(TOKENS)
    const fillStyles: string[] = []
    const ctx = {
      font: "",
      fillStyle: "",
      strokeStyle: "",
      lineWidth: 0,
      shadowOffsetX: 0,
      shadowOffsetY: 0,
      shadowBlur: 0,
      shadowColor: "",
      textAlign: "",
      textBaseline: "",
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      quadraticCurveTo: vi.fn(),
      arc: vi.fn(),
      closePath: vi.fn(),
      fill: vi.fn(() => { fillStyles.push((ctx as unknown as { fillStyle: string }).fillStyle) }),
      stroke: vi.fn(),
      fillText: vi.fn(),
      measureText: vi.fn(() => ({ width: 50 })),
    }
    fn(ctx as unknown as CanvasRenderingContext2D, { x: 0, y: 0, size: 8, label: "Test", color: "#AA0000" /* drift-allowed: test stub only */ }, SETTINGS)
    // At least one fill call should use the background token
    expect(fillStyles).toContain(TOKENS.background)
  })

  it("uses tokens.foreground for label text", () => {
    const fn = makeDrawNodeHover(TOKENS)
    const fillTextStyles: string[] = []
    const ctx = {
      font: "",
      get fillStyle() { return this._fill },
      set fillStyle(v: string) { this._fill = v },
      _fill: "",
      strokeStyle: "",
      lineWidth: 0,
      shadowOffsetX: 0,
      shadowOffsetY: 0,
      shadowBlur: 0,
      shadowColor: "",
      textAlign: "",
      textBaseline: "",
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      quadraticCurveTo: vi.fn(),
      arc: vi.fn(),
      closePath: vi.fn(),
      fill: vi.fn(),
      stroke: vi.fn(),
      fillText: vi.fn(() => {
        fillTextStyles.push((ctx as unknown as { _fill: string })._fill)
      }),
      measureText: vi.fn(() => ({ width: 50 })),
    }
    fn(ctx as unknown as CanvasRenderingContext2D, { x: 0, y: 0, size: 8, label: "Test", color: "#AA0000" /* drift-allowed: test stub only */ }, SETTINGS)
    expect(fillTextStyles.length).toBeGreaterThan(0)
    expect(fillTextStyles[0]).toBe(TOKENS.foreground)
  })

  it("uses tokens.fontSans in ctx.font (not var())", () => {
    const fn = makeDrawNodeHover(TOKENS)
    let capturedFont = ""
    const ctx = {
      set font(v: string) { capturedFont = v },
      get font() { return capturedFont },
      fillStyle: "",
      strokeStyle: "",
      lineWidth: 0,
      shadowOffsetX: 0,
      shadowOffsetY: 0,
      shadowBlur: 0,
      shadowColor: "",
      textAlign: "",
      textBaseline: "",
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      quadraticCurveTo: vi.fn(),
      arc: vi.fn(),
      closePath: vi.fn(),
      fill: vi.fn(),
      stroke: vi.fn(),
      fillText: vi.fn(),
      measureText: vi.fn(() => ({ width: 50 })),
    }
    fn(ctx as unknown as CanvasRenderingContext2D, { x: 0, y: 0, size: 8, label: "Test", color: "#AA0000" /* drift-allowed: test stub only */ }, SETTINGS)
    expect(capturedFont).toContain(TOKENS.fontSans)
    expect(capturedFont).not.toContain("var(")
  })

  it("handles nodes with no label without crashing", () => {
    const fn = makeDrawNodeHover(TOKENS)
    const ctx = {
      font: "",
      fillStyle: "",
      strokeStyle: "",
      lineWidth: 0,
      shadowOffsetX: 0,
      shadowOffsetY: 0,
      shadowBlur: 0,
      shadowColor: "",
      textAlign: "",
      textBaseline: "",
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      quadraticCurveTo: vi.fn(),
      arc: vi.fn(),
      closePath: vi.fn(),
      fill: vi.fn(),
      stroke: vi.fn(),
      fillText: vi.fn(),
      measureText: vi.fn(() => ({ width: 0 })),
    }
    expect(() =>
      fn(ctx as unknown as CanvasRenderingContext2D, { x: 0, y: 0, size: 8, label: null as unknown as string, color: "#AA0000" /* drift-allowed: test stub only */ }, SETTINGS)
    ).not.toThrow()
  })
})

// ---------------------------------------------------------------------------
// resolveCanvasFont
// ---------------------------------------------------------------------------

describe("resolveCanvasFont", () => {
  it("returns a non-empty string", () => {
    const mockEl = {
      style: {},
    } as unknown as Element
    // Mock getComputedStyle for this test
    const origGetComputedStyle = globalThis.getComputedStyle
    globalThis.getComputedStyle = vi.fn(() => ({
      getPropertyValue: (name: string) => name === "--font-sans" ? "Inter, system-ui" : "",
    })) as unknown as typeof getComputedStyle
    const result = resolveCanvasFont(mockEl)
    globalThis.getComputedStyle = origGetComputedStyle
    expect(result).toBeTruthy()
    expect(result).not.toContain("var(")
  })

  it("falls back to system-ui when property is empty", () => {
    const mockEl = {} as Element
    const origGetComputedStyle = globalThis.getComputedStyle
    globalThis.getComputedStyle = vi.fn(() => ({
      getPropertyValue: () => "",
    })) as unknown as typeof getComputedStyle
    const result = resolveCanvasFont(mockEl)
    globalThis.getComputedStyle = origGetComputedStyle
    expect(result).toBe("system-ui, sans-serif")
  })
})
