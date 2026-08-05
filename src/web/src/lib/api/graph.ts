// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
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
  options: { signal?: AbortSignal; includeIsolated?: boolean } = {},
): Promise<NeighborhoodResponse> {
  const url = mcpUrl("/graph/neighborhood", {
    entity,
    hops,
    filter,
    // Omit the param when false/undefined to keep default URLs cache-stable.
    ...(options.includeIsolated ? { include_isolated: "true" } : {}),
  })

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

// ---------------------------------------------------------------------------
// Timeline Strata v2 — Stratigraph (Phase M v2)
// ---------------------------------------------------------------------------

export interface StrataCommunity {
  community_id: string
  label: string
  color_slot: number
  trust_mix: { verified: number; partial: number; unverified: number }
  total_mentions: number
  is_other: boolean
}

export interface StrataSeries {
  community_id: string
  entity_type: string
  /** Primary domain of the entity — coalesced to "other" server-side when null */
  domain: string
  buckets: number[]
  /** Per-bucket count of unverified-trust entity mentions (amendment 1) */
  unverified_buckets?: number[]
}

export interface StrataTrack {
  canonical_id: string
  name: string
  entity_type: string
  community_id: string
  trust_state: "verified" | "partial" | "unverified" | "unknown"
  first_seen: string
  rank: number
  total_mentions: number
  buckets: number[]
  /** Primary domain of the entity; null when derivation has not yet run */
  primary_domain?: string | null
}

export interface StrataMarker {
  date: string
  kind: "ingest_burst" | "birth_surge" | string
  count: number
}

export interface TimelineStrataResponse {
  from_date: string
  to_date: string
  granularity: "day" | "week" | "month"
  bucket_dates: string[]
  communities: StrataCommunity[]
  series: StrataSeries[]
  tracks: StrataTrack[]
  markers: StrataMarker[]
  totals: { mentions: number; entities_introduced: number }
  cached: boolean
  lanes?: import("@/components/subjects/timeline/stratigraph/strata-types").LaneMeta[]
  events?: import("@/components/subjects/timeline/stratigraph/strata-types").StrataEvent[]
  verification_aggs?: import("@/components/subjects/timeline/stratigraph/strata-types").VerificationAggregate[]
  top_entities?: Record<string, import("@/components/subjects/timeline/stratigraph/strata-types").TopEntity[]>
  data_extent_from?: string | null
  ledger_start_date?: string | null
}

export interface FetchStrataOptions {
  /** Amendment #7: "180d" added for data-extent-clamped default window. */
  period?: "7d" | "30d" | "90d" | "180d" | "365d"
  granularity?: "day" | "week" | "month"
  from?: string
  to?: string
}

export async function fetchTimelineStrata(
  opts: FetchStrataOptions = {},
): Promise<TimelineStrataResponse> {
  const url = mcpUrl("/graph/timeline/strata", {
    period: opts.period,
    granularity: opts.granularity,
    from: opts.from,
    to: opts.to,
  })
  const res = await fetch(url.toString(), { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to load timeline strata"))
  return res.json() as Promise<TimelineStrataResponse>
}

export interface TrackEventCoMention {
  canonical_id: string
  name: string
}

export interface TrackEvent {
  ts: string
  artifact_id: string
  artifact_filename: string
  confidence: number
  summary: string
  co_mentioned: TrackEventCoMention[]
}

export interface TimelineTrackResponse {
  canonical_id: string
  name: string
  events: TrackEvent[]
  cached: boolean
  new_entities?: import("@/components/subjects/timeline/stratigraph/strata-types").NewEntity[]
  knowledge_events?: import("@/components/subjects/timeline/stratigraph/strata-types").TrackEventExtended[]
  verification?: import("@/components/subjects/timeline/stratigraph/strata-types").TrackVerification | null
  community_summary?: string | null
}

export interface FetchTrackOptions {
  from?: string
  to?: string
  bucket?: string
}

export async function fetchTimelineTrack(
  canonicalId: string,
  opts: FetchTrackOptions = {},
): Promise<TimelineTrackResponse> {
  const url = mcpUrl(`/graph/timeline/track/${encodeURIComponent(canonicalId)}`, {
    from: opts.from,
    to: opts.to,
    bucket: opts.bucket,
  })
  const res = await fetch(url.toString(), { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to load timeline track"))
  return res.json() as Promise<TimelineTrackResponse>
}
