// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Unit tests for the cosmos.gl data-prep helpers (B8). Pure Float32Array
// conversions from the /graph/embeddings/3d payload into cosmos.gl's flat
// buffer formats — no WebGL, no cosmos instance.

import { describe, it, expect } from "vitest"
import {
  positionsFromEntities,
  randomPositions,
  linksToPairs,
  colorsFromRgb,
} from "../cosmos-data"

type Ent = { x: number; y: number }
const ents: Ent[] = [
  { x: 1, y: 2 },
  { x: -3, y: 4 },
  { x: 5, y: -6 },
]

describe("positionsFromEntities", () => {
  it("flattens x,y into [x1,y1,x2,y2,...]", () => {
    expect(Array.from(positionsFromEntities(ents))).toEqual([1, 2, -3, 4, 5, -6])
  })

  it("returns a Float32Array of length 2N", () => {
    const out = positionsFromEntities(ents)
    expect(out).toBeInstanceOf(Float32Array)
    expect(out.length).toBe(6)
  })

  it("handles an empty list", () => {
    expect(positionsFromEntities([]).length).toBe(0)
  })
})

describe("randomPositions", () => {
  it("produces 2N values inside [0, spaceSize] with an injected rng", () => {
    const seq = [0, 0.5, 1, 0.25, 0.75, 0.999]
    let i = 0
    const rand = () => seq[i++]
    const out = randomPositions(3, 100, rand)
    expect(out.length).toBe(6)
    // Float32 storage, so compare with tolerance element-wise.
    const expected = [0, 50, 100, 25, 75, 99.9]
    out.forEach((v, idx) => expect(v).toBeCloseTo(expected[idx], 3))
  })

  it("stays within bounds with the default rng", () => {
    const out = randomPositions(50, 200)
    for (const v of out) {
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThanOrEqual(200)
    }
  })
})

describe("linksToPairs", () => {
  const links: [number, number, number, string][] = [
    [0, 1, 5, "co_mention"],
    [1, 2, 0.4, "similar"],
  ]

  it("emits [source,target,...] index pairs for valid links", () => {
    expect(Array.from(linksToPairs(links, 3))).toEqual([0, 1, 1, 2])
  })

  it("drops links that reference out-of-range indices", () => {
    const bad: [number, number, number, string][] = [
      [0, 1, 1, "co_mention"],
      [1, 9, 1, "similar"], // 9 out of range for N=3
    ]
    expect(Array.from(linksToPairs(bad, 3))).toEqual([0, 1])
  })

  it("drops self-loops", () => {
    const loop: [number, number, number, string][] = [[2, 2, 1, "co_mention"]]
    expect(linksToPairs(loop, 3).length).toBe(0)
  })

  it("returns a Float32Array", () => {
    expect(linksToPairs(links, 3)).toBeInstanceOf(Float32Array)
  })
})

describe("colorsFromRgb", () => {
  it("expands n×3 RGB into n×4 RGBA with the given alpha", () => {
    const rgb = new Float32Array([1, 0, 0, 0, 1, 0])
    const out = colorsFromRgb(rgb, 2, 0.8)
    const expected = [1, 0, 0, 0.8, 0, 1, 0, 0.8]
    out.forEach((v, idx) => expect(v).toBeCloseTo(expected[idx], 5))
  })

  it("defaults missing colors to opaque white", () => {
    const out = colorsFromRgb(undefined, 2, 1)
    expect(Array.from(out)).toEqual([1, 1, 1, 1, 1, 1, 1, 1])
  })

  it("returns a Float32Array of length 4N", () => {
    const out = colorsFromRgb(new Float32Array([0.2, 0.4, 0.6]), 1, 1)
    expect(out).toBeInstanceOf(Float32Array)
    expect(out.length).toBe(4)
  })
})
