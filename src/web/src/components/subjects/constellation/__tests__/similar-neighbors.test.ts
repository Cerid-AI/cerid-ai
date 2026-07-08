// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for the client-side kNN SIMILAR_TO ranking (B5). Pure — operates
// on the already-loaded /graph/embeddings/3d links, no fetch, no WebGL.

import { describe, it, expect } from "vitest"
import { rankSimilarNeighbors } from "../similar-neighbors"

type Ent = { id: string; name: string }
const ents: Ent[] = [
  { id: "a", name: "Alpha" },
  { id: "b", name: "Beta" },
  { id: "c", name: "Gamma" },
  { id: "d", name: "Delta" },
]
type Link = [number, number, number, string]

describe("rankSimilarNeighbors", () => {
  it("returns [] for an out-of-range pinned index", () => {
    expect(rankSimilarNeighbors(-1, [], ents)).toEqual([])
    expect(rankSimilarNeighbors(99, [], ents)).toEqual([])
  })

  it("returns [] when the pinned node has no similar edges", () => {
    const links: Link[] = [[0, 1, 5, "co_mention"]]
    expect(rankSimilarNeighbors(0, links, ents)).toEqual([])
  })

  it("ignores co_mention edges, keeps only similar", () => {
    const links: Link[] = [
      [0, 1, 9, "co_mention"],
      [0, 2, 0.4, "similar"],
    ]
    const out = rankSimilarNeighbors(0, links, ents)
    expect(out.map((n) => n.id)).toEqual(["c"])
  })

  it("matches similar edges in both directions (source or target is pinned)", () => {
    const links: Link[] = [
      [0, 1, 0.5, "similar"], // pinned is source
      [2, 0, 0.7, "similar"], // pinned is target
    ]
    const out = rankSimilarNeighbors(0, links, ents)
    expect(out.map((n) => n.id).sort()).toEqual(["b", "c"])
  })

  it("sorts by score descending", () => {
    const links: Link[] = [
      [0, 1, 0.3, "similar"],
      [0, 2, 0.9, "similar"],
      [0, 3, 0.6, "similar"],
    ]
    const out = rankSimilarNeighbors(0, links, ents)
    expect(out.map((n) => n.id)).toEqual(["c", "d", "b"])
    expect(out.map((n) => n.score)).toEqual([0.9, 0.6, 0.3])
  })

  it("dedupes multiple edges to the same neighbor, keeping the strongest", () => {
    const links: Link[] = [
      [0, 1, 0.2, "similar"],
      [1, 0, 0.8, "similar"],
    ]
    const out = rankSimilarNeighbors(0, links, ents)
    expect(out).toHaveLength(1)
    expect(out[0].score).toBe(0.8)
  })

  it("respects the limit", () => {
    const links: Link[] = [
      [0, 1, 0.9, "similar"],
      [0, 2, 0.8, "similar"],
      [0, 3, 0.7, "similar"],
    ]
    expect(rankSimilarNeighbors(0, links, ents, 2)).toHaveLength(2)
  })

  it("normalizes scores to the top neighbor (normScore 0..1)", () => {
    const links: Link[] = [
      [0, 1, 0.5, "similar"],
      [0, 2, 1.0, "similar"],
    ]
    const out = rankSimilarNeighbors(0, links, ents)
    expect(out[0].normScore).toBeCloseTo(1)
    expect(out[1].normScore).toBeCloseTo(0.5)
  })

  it("ignores self-loops", () => {
    const links: Link[] = [[0, 0, 0.9, "similar"]]
    expect(rankSimilarNeighbors(0, links, ents)).toEqual([])
  })

  it("carries id + name from the entity list", () => {
    const links: Link[] = [[0, 2, 0.6, "similar"]]
    const out = rankSimilarNeighbors(0, links, ents)
    expect(out[0]).toMatchObject({ index: 2, id: "c", name: "Gamma" })
  })
})
