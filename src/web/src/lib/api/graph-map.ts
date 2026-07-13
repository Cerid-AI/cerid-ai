// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Client for GET /api/mcp/graph/map — the Cartographer knowledge-map
// endpoint. Returns precomputed ForceAtlas2 positions + Leiden community
// artifacts for the flat 2D map view.

import { mcpUrl, mcpHeaders, extractError } from "./common"
import { withRequestTimeout, timeoutToError } from "./embeddings-3d"
import type { EntityEmbedding3D } from "./embeddings-3d"
import type { MapLayoutV2 as MapLayout } from "@/lib/graph/cycle4-contracts"

// Re-export the entity type so map consumers don't have to import two modules.
export type { EntityEmbedding3D }
export type { MapLayout }

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
  /** [sourceIdx, targetIdx, weight, kind] — index into entities array; kind is "co_mention" or "similar" */
  links: [number, number, number, string][]
  communities: CommunityHull[]
  /** silhouette score from the quality gate (null if not yet computed) */
  silhouette: number | null
  /** ISO timestamp of last compute run */
  computed_at: string | null
  cached: boolean
  /**
   * Layout that was actually served. Present when ?layout= was passed.
   * Absent (undefined) on responses from the legacy no-param endpoint.
   */
  layout?: MapLayout
  /**
   * True when the requested non-default layout artifact was not yet computed
   * and the server fell back to "force". Absent/undefined when not applicable.
   */
  layout_fallback?: boolean
  /** Number of isolated (degree-0) entities excluded when include_isolated=false */
  isolated_count: number
}

export async function fetchGraphMap(
  layout?: MapLayout,
  includeIsolated?: boolean,
  signal?: AbortSignal,
): Promise<GraphMapResponse> {
  const params: Record<string, string> = {}
  if (layout && layout !== "force") params.layout = layout
  // Omit the param when false/undefined to keep default URLs cache-stable.
  if (includeIsolated) params.include_isolated = "true"

  const url = mcpUrl("/graph/map", params)
  let res: Response
  try {
    res = await fetch(url.toString(), {
      headers: mcpHeaders(),
      signal: withRequestTimeout(signal),
    })
  } catch (err) {
    throw timeoutToError(err, "Graph map fetch")
  }
  if (!res.ok) {
    throw new Error(await extractError(res, `Graph map fetch failed: ${res.status}`))
  }
  return res.json() as Promise<GraphMapResponse>
}
