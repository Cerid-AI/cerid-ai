// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Thin client for GET /graph/structural-gaps (Phase 5 "C2"): the knowledge
// graph's structural holes — pairs of communities that are semantically close
// but weakly linked (topics that should connect but don't yet). Advisory:
// the panel offers "Explore in chat" to bridge them.

import { mcpUrl, mcpHeaders, extractError } from "./common"

export interface GapCommunity {
  id: string
  label: string
  count: number
}

export interface GapEntity {
  id: string
  name: string
}

export interface StructuralGap {
  community_a: GapCommunity
  community_b: GapCommunity
  /** Centroid cosine similarity, 0..1. */
  semantic_similarity: number
  /** Normalized inter-community CO_MENTIONED strength, 0..1. */
  link_strength: number
  /** semantic_similarity × (1 − link_strength), 0..1 — higher = bigger hole. */
  gap_score: number
  /** Entities best positioned to bridge the two communities. */
  bridging_candidates: GapEntity[]
}

export interface StructuralGapsResponse {
  gaps: StructuralGap[]
}

/** Fetch the top-N structural gaps (server caps limit at 20). */
export async function fetchStructuralGaps(limit = 8, signal?: AbortSignal): Promise<StructuralGapsResponse> {
  // Omit the default so the URL stays cache-stable with the server's default.
  const url = mcpUrl("/graph/structural-gaps", limit !== 8 ? { limit: String(limit) } : {})
  const res = await fetch(url.toString(), { headers: mcpHeaders(), signal })
  if (!res.ok) throw new Error(await extractError(res, `Structural gaps fetch failed: ${res.status}`))
  return res.json() as Promise<StructuralGapsResponse>
}
