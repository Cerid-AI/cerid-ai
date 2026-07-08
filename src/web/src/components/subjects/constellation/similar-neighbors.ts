// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Client-side kNN over the SIMILAR_TO edges already shipped by
// /graph/embeddings/3d (B5). When a node is pinned we surface its strongest
// semantic neighbors — no backend call, the edges are in hand. Pure so the
// ranking is unit-testable without WebGL.

export interface SimilarNeighbor {
  /** Index into the entities array (for re-pinning + fly-to). */
  index: number
  id: string
  name: string
  /** Raw similarity weight from the edge. */
  score: number
  /** Score relative to the top neighbor, 0..1 — drives the ProgressBar fill. */
  normScore: number
}

/**
 * Rank the SIMILAR_TO neighbors of `pinnedIndex`, strongest first, capped at
 * `limit`. Both edge directions count; co_mention edges and self-loops are
 * ignored; multiple edges to the same neighbor collapse to the strongest.
 * normScore is relative to the top neighbor so the bars read regardless of the
 * weight's absolute scale.
 */
export function rankSimilarNeighbors(
  pinnedIndex: number,
  links: [number, number, number, string][],
  entities: { id: string; name: string }[],
  limit = 10,
): SimilarNeighbor[] {
  if (pinnedIndex < 0 || pinnedIndex >= entities.length) return []

  const best = new Map<number, number>() // neighbor index → strongest weight
  for (const [si, ti, w, kind] of links) {
    if (kind !== "similar") continue
    let other = -1
    if (si === pinnedIndex) other = ti
    else if (ti === pinnedIndex) other = si
    else continue
    if (other < 0 || other >= entities.length || other === pinnedIndex) continue
    const prev = best.get(other)
    if (prev === undefined || w > prev) best.set(other, w)
  }

  const ranked = [...best.entries()]
    .map(([index, score]) => ({ index, score, id: entities[index].id, name: entities[index].name }))
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, limit)

  const max = ranked.length ? ranked[0].score : 1
  return ranked.map((r) => ({ ...r, normScore: max > 0 ? r.score / max : 0 }))
}
