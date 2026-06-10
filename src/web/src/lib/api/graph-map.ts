// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Client for GET /api/mcp/graph/map — the Cartographer knowledge-map
// endpoint. Returns precomputed ForceAtlas2 positions + Leiden community
// artifacts for the flat 2D map view.

import { mcpUrl, mcpHeaders, extractError } from "./common"
import type { EntityEmbedding3D } from "./embeddings-3d"

// Re-export the entity type so map consumers don't have to import two modules.
export type { EntityEmbedding3D }

export interface CommunityHull {
  /** Leiden community id */
  id: string
  /** Number of member entities */
  count: number
  /** Chaikin-smoothed alpha-shape hull polygon in map coordinates */
  hull: [number, number][]
  /** Density-argmax label anchor point in map coordinates */
  anchor: [number, number]
  /** Human-readable community name (top-mention entity or LLM-named) */
  label: string
  /** Top-degree hub entities in this community */
  top_hubs: { id: string; name: string; degree: number }[]
  /** Trust-state distribution: { verified: 0.4, partial: 0.3, ... } */
  trust_mix: Record<string, number>
}

export interface GraphMapResponse {
  count: number
  /** Entities with x/y as precomputed map coordinates (z ignored in 2D) */
  entities: EntityEmbedding3D[]
  /** [sourceIdx, targetIdx, weight] — index into entities array */
  links: [number, number, number][]
  communities: CommunityHull[]
  /** silhouette score from the quality gate (null if not yet computed) */
  silhouette: number | null
  /** ISO timestamp of last compute run */
  computed_at: string | null
  cached: boolean
}

export async function fetchGraphMap(signal?: AbortSignal): Promise<GraphMapResponse> {
  const url = mcpUrl("/graph/map")
  const res = await fetch(url.toString(), {
    headers: mcpHeaders(),
    signal,
  })
  if (!res.ok) {
    throw new Error(await extractError(res, `Graph map fetch failed: ${res.status}`))
  }
  return res.json() as Promise<GraphMapResponse>
}
