// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Synthetic graph fixture generator for Atlas + Constellation perf
// budgets. Produces NeighborhoodResponse payloads that are:
//
//   - Deterministic (PRNG seeded; same N + seed → same graph)
//   - Realistic shape: power-law node degree, log-distributed mention
//     count, Leiden-style community clustering, mixed trust states,
//     ~5% contradiction edges, ~10% inferred (vs attested) edges.
//   - Streams from the adapter unchanged — same code path as production.
//
// Usage:
//   const fixture = generateSyntheticGraph({ nodes: 1000, seed: 42 })
//   // → NeighborhoodResponse shape: { focal_entity, nodes, edges, ... }
//
// Why we need this:
//   The Phase A exit criterion "60fps on 1K-node Atlas" can't be
//   verified against production data (no shared fixture KB) and can't
//   be verified via vitest+jsdom (no WebGL). The synthetic fixture
//   bridges that gap: same data shape as /graph/neighborhood, runs
//   client-side, deterministic so Playwright budget assertions are
//   stable across CI runs.

import type {
  GraphEdge,
  GraphNode,
  NeighborhoodResponse,
} from "@/lib/types/graph"

// ---------------------------------------------------------------------------
// Seeded PRNG — Mulberry32 (small, fast, decent statistical properties for
// fixture generation — NOT cryptographic).
// ---------------------------------------------------------------------------

function mulberry32(seed: number): () => number {
  let t = seed >>> 0
  return function next() {
    t = (t + 0x6d2b79f5) | 0
    let r = Math.imul(t ^ (t >>> 15), 1 | t)
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296
  }
}

// ---------------------------------------------------------------------------
// Distributions
// ---------------------------------------------------------------------------

const TRUST_STATES = ["verified", "partial", "unverified", "contradicted", "unknown"] as const
const TRUST_WEIGHTS = [0.55, 0.18, 0.15, 0.04, 0.08]  // sums to 1.0
const ENTITY_TYPES = ["Person", "Project", "Topic", "Place", "Organization", "Document", "Event", "Claim"]
const EDGE_TYPES = ["mentions", "works_on", "discussed_with", "contradicts", "temporal"]
const EDGE_TYPE_WEIGHTS = [0.55, 0.15, 0.15, 0.05, 0.1]

function pickWeighted<T>(values: readonly T[], weights: readonly number[], rng: () => number): T {
  const r = rng()
  let acc = 0
  for (let i = 0; i < values.length; i++) {
    acc += weights[i]
    if (r <= acc) return values[i]
  }
  return values[values.length - 1]
}

/** Power-law degree distribution: most nodes have few edges, few have many. */
function powerLawSample(rng: () => number, alpha = 2.2, xmin = 1, xmax = 200): number {
  const u = 1 - rng()
  const sample = xmin * Math.pow(u, -1 / (alpha - 1))
  return Math.min(xmax, Math.max(xmin, Math.round(sample)))
}

/** Log-normal mention count: cluster around 5-30 with a long tail. */
function mentionCountSample(rng: () => number): number {
  const u1 = rng() || 0.0001
  const u2 = rng()
  // Box-Muller → normal → exp → log-normal
  const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2)
  const mu = 2.5  // log-space mean ≈ 12
  const sigma = 1.0
  return Math.max(1, Math.round(Math.exp(mu + sigma * z)))
}

// ---------------------------------------------------------------------------
// Generator
// ---------------------------------------------------------------------------

export interface SyntheticGraphOptions {
  /** Number of nodes to generate (100, 1000, 5000, 10000 are the budget sizes) */
  nodes: number
  /** PRNG seed — same seed always produces the same graph */
  seed?: number
  /** Number of Leiden-style communities. Defaults to sqrt(nodes). */
  communities?: number
  /** Override focal entity id; defaults to the first node */
  focalEntity?: string
}

export function generateSyntheticGraph({
  nodes: nodeCount,
  seed = 42,
  communities,
  focalEntity,
}: SyntheticGraphOptions): NeighborhoodResponse {
  const rng = mulberry32(seed)
  const communityCount = communities ?? Math.max(2, Math.round(Math.sqrt(nodeCount)))

  // Build nodes
  const nodes: GraphNode[] = []
  for (let i = 0; i < nodeCount; i++) {
    const community = `c${i % communityCount}`
    const trust = pickWeighted(TRUST_STATES, TRUST_WEIGHTS, rng)
    nodes.push({
      id: `n${i}`,
      name: `Entity ${i}`,
      type: ENTITY_TYPES[Math.floor(rng() * ENTITY_TYPES.length)],
      community,
      mention_count: mentionCountSample(rng),
      trust_state: trust,
      recency_score: rng(),
      focused: i === 0,
    })
  }

  // Build edges via power-law per-node degree. Community-affinity bias:
  // 70% of an edge's neighbor candidates come from the same community,
  // 30% from any other community. Approximates real KB graphs.
  const edgeSet = new Set<string>()
  const edges: GraphEdge[] = []
  const communityIndex = new Map<string, number[]>()
  nodes.forEach((n, idx) => {
    const list = communityIndex.get(n.community ?? "") ?? []
    list.push(idx)
    communityIndex.set(n.community ?? "", list)
  })

  for (let i = 0; i < nodeCount; i++) {
    const targetDegree = powerLawSample(rng, 2.2, 1, Math.min(50, Math.floor(nodeCount / 4)))
    let added = 0
    let attempts = 0
    const maxAttempts = targetDegree * 4
    while (added < targetDegree && attempts < maxAttempts) {
      attempts++
      let j: number
      if (rng() < 0.7) {
        const cluster = communityIndex.get(nodes[i].community ?? "") ?? []
        j = cluster[Math.floor(rng() * cluster.length)]
      } else {
        j = Math.floor(rng() * nodeCount)
      }
      if (j === i) continue
      const key = i < j ? `${i}-${j}` : `${j}-${i}`
      if (edgeSet.has(key)) continue
      edgeSet.add(key)
      const edgeType = pickWeighted(EDGE_TYPES, EDGE_TYPE_WEIGHTS, rng)
      const contradiction = rng() < 0.05
      edges.push({
        source: `n${i}`,
        target: `n${j}`,
        type: edgeType,
        weight: Math.max(0.1, rng() * 3),
        attestation: rng() < 0.9 ? "attested" : "inferred",
        contradiction,
      })
      added++
    }
  }

  return {
    focal_entity: focalEntity ?? "n0",
    nodes,
    edges,
    truncated: false,
    cached: false,
    isolated_count: 0,
  }
}

// ---------------------------------------------------------------------------
// Canonical fixture sizes — the points the perf budget is asserted at.
// Keep in sync with tests/perf/atlas-perf.spec.ts.
// ---------------------------------------------------------------------------

export const PERF_FIXTURE_SIZES = [100, 1000, 5000, 10000] as const
export type PerfFixtureSize = (typeof PERF_FIXTURE_SIZES)[number]
