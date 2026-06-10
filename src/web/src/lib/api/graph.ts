// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Graph API client — wraps /graph/* backend endpoints for the Atlas/
// Constellation/Timeline visualization modes (Cerid v1.0 Phase A onward).

import type { GraphHealth, NeighborhoodResponse } from "@/lib/types/graph"
import { MCP_BASE, mcpHeaders, mcpUrl, extractError } from "./common"

/**
 * Fetch a K-hop neighborhood around a focal entity.
 *
 * The response is shaped for direct consumption by the graphology adapter
 * + sigma.js renderer. Cached server-side with TTL ~60s (configurable via
 * GRAPH_NEIGHBORHOOD_CACHE_TTL env).
 *
 * @param entity   focal entity ID (canonical_id)
 * @param hops     1..3 hop depth; default 2
 * @param filter   optional entity-type filter (Person | Project | ...)
 * @throws on non-2xx — caller handles UX (toast, retry, fallback to outline mode)
 */
export async function fetchNeighborhood(
  entity: string,
  hops: 1 | 2 | 3 = 2,
  filter?: string,
  options: { signal?: AbortSignal } = {},
): Promise<NeighborhoodResponse> {
  const url = mcpUrl("/graph/neighborhood", { entity, hops, filter })

  const res = await fetch(url.toString(), {
    headers: mcpHeaders(),
    signal: options.signal,
  })
  if (!res.ok) throw new Error(await extractError(res, "Graph neighborhood fetch failed"))
  return res.json() as Promise<NeighborhoodResponse>
}

/**
 * Liveness probe for the graph subsystem. Cheap; does not run a Cypher
 * query. Useful for Settings → Diagnostics health card.
 */
export async function fetchGraphHealth(): Promise<GraphHealth> {
  const res = await fetch(`${MCP_BASE}/graph/health`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Graph health probe failed"))
  return res.json() as Promise<GraphHealth>
}

// ---------------------------------------------------------------------------
// Timeline (Phase M)
// ---------------------------------------------------------------------------

export interface TimelineBucket {
  date: string
  mention_count: number
  entities_introduced: number
}

export interface TimelineResponse {
  entity: string | null
  from_date: string
  to_date: string
  granularity: "day" | "week" | "month"
  buckets: TimelineBucket[]
  total_mentions: number
  total_entities_introduced: number
  cached: boolean
}

export interface FetchTimelineOptions {
  entity?: string | null
  period?: "7d" | "30d" | "90d" | "365d"
  granularity?: "day" | "week" | "month"
}

export async function fetchTimeline(opts: FetchTimelineOptions = {}): Promise<TimelineResponse> {
  const url = mcpUrl("/graph/timeline", {
    entity: opts.entity,
    period: opts.period,
    granularity: opts.granularity,
  })
  const res = await fetch(url.toString(), { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to load timeline"))
  return res.json() as Promise<TimelineResponse>
}
