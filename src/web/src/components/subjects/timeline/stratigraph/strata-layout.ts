// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pure layout functions for the Stratigraph canvas. No DOM dependencies —
// all functions accept data + viewport geometry and return computed values.
// Unit-testable without jsdom.

import type {
  TimelineStrataResponse,
  StrataTrack,
  StrataSeries,
  StrataMarker,
} from "@/lib/api/graph"

// ---------------------------------------------------------------------------
// Types the canvas layer consumes
// ---------------------------------------------------------------------------

export type LODLevel = "era" | "bucket" | "track"

export interface LODState {
  level: LODLevel
  /** Visible span in days. Drives transition hysteresis. */
  visibleDays: number
}

export interface StratumLayout {
  communityId: string
  label: string
  colorSlot: number
  /** Stacked height in pixels from the assigned baseline */
  heightPx: number
  /** Y offset of the top edge in canvas pixels */
  topPx: number
  trustMix: { verified: number; partial: number; unverified: number }
  totalMentions: number
  isOther: boolean
  /** Per-bucket heights (sqrt-normalized), aligned to bucket_dates */
  bucketHeights: number[]
  /** Per-bucket raw mention counts — sqrt damping distorts comparisons, so
      hover surfaces always report the exact number */
  bucketCounts: number[]
  /** Indices of unverified-dominant buckets (amendment 1) */
  unverifiedBuckets: Set<number>
  /** DOI track ids allocated for this stratum (LOD >= bucket) */
  trackIds: string[]
}

export interface MarkerLayout {
  date: string
  kind: string
  count: number
  /** Fractional x position 0..1 within the time axis */
  xFrac: number
  label: string
}

// ---------------------------------------------------------------------------
// Stable hash — same as communitySlot() in community-layer.tsx
// ---------------------------------------------------------------------------

export function communitySlot(id: string): number {
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = ((hash << 5) - hash) + id.charCodeAt(i)
    hash |= 0
  }
  return Math.abs(hash) % 8
}

// ---------------------------------------------------------------------------
// LOD state machine with hysteresis (amendment 2)
// Resolve above ~5 visible events per bucket-width; fuse below ~3.
// ---------------------------------------------------------------------------

const LOD_RESOLVE_DAYS = 7   // <7 visible days → track level
const LOD_BUCKET_DAYS = 42   // <42 visible days → bucket level
const LOD_HYSTERESIS = 0.15  // 15% band to prevent oscillation

/** Compute the new LOD level given current visible days and prior state. */
export function computeLOD(visibleDays: number, prior: LODLevel): LODLevel {
  // Apply hysteresis: widen the band for reverse transitions.
  const resolveThreshold = prior === "track"
    ? LOD_RESOLVE_DAYS * (1 + LOD_HYSTERESIS)
    : LOD_RESOLVE_DAYS
  const fuseThreshold = prior === "bucket"
    ? LOD_BUCKET_DAYS * (1 - LOD_HYSTERESIS)
    : LOD_BUCKET_DAYS

  if (visibleDays < resolveThreshold) return "track"
  if (visibleDays < fuseThreshold) return "bucket"
  return "era"
}

// ---------------------------------------------------------------------------
// DOI (degree-of-interest) scoring — mirrors server heuristic client-side
// for re-ranking within the client's track budget.
// ---------------------------------------------------------------------------

export function trackDOI(
  track: StrataTrack,
  bucketDates: string[],
  pinnedIds: Set<string>,
): number {
  const n = track.total_mentions
  const logScore = Math.log(1 + n)

  // Recency bonus: is the last mention in the final third of the window?
  const finalThirdStart = Math.floor(bucketDates.length * (2 / 3))
  let hasRecentActivity = false
  for (let i = finalThirdStart; i < track.buckets.length; i++) {
    if (track.buckets[i] > 0) { hasRecentActivity = true; break }
  }
  const recencyBonus = hasRecentActivity ? 0.5 : 0

  // Trust attention bonus: unverified entities warrant analyst attention
  const trustBonus = track.trust_state === "unverified" ? 0.5 : 0

  // Pinned/selected bonus
  const pinnedBonus = pinnedIds.has(track.canonical_id) ? 2.0 : 0

  return logScore + recencyBonus + trustBonus + pinnedBonus
}

// ---------------------------------------------------------------------------
// Severity-priority trust aggregation (amendment 1)
// A bucket is "unverified-dominant" when ANY unverified-trust mention exists.
// ---------------------------------------------------------------------------

function buildUnverifiedBucketSet(
  series: StrataSeries[],
  communityId: string,
): Set<number> {
  const set = new Set<number>()
  for (const s of series) {
    if (s.community_id !== communityId) continue
    if (!s.unverified_buckets) continue
    for (let i = 0; i < s.unverified_buckets.length; i++) {
      if (s.unverified_buckets[i] > 0) set.add(i)
    }
  }
  return set
}

// ---------------------------------------------------------------------------
// Per-community mention aggregation from series
// ---------------------------------------------------------------------------

function aggregateBuckets(
  series: StrataSeries[],
  communityId: string,
  bucketCount: number,
): number[] {
  const totals = new Array<number>(bucketCount).fill(0)
  for (const s of series) {
    if (s.community_id !== communityId) continue
    for (let i = 0; i < bucketCount; i++) {
      totals[i] += s.buckets[i] ?? 0
    }
  }
  return totals
}

// ---------------------------------------------------------------------------
// Type-lens re-aggregation (client-side, no refetch needed)
// ---------------------------------------------------------------------------

export const ENTITY_TYPES = ["PERSON", "ORG", "LOC", "EVENT", "ASSET", "OTHER"] as const
export type EntityType = typeof ENTITY_TYPES[number]

/** Re-partition strata by entity_type instead of community. */
export function computeTypeLensStrata(
  response: TimelineStrataResponse,
  canvasHeight: number,
  trackBudget: number,
  pinnedIds: Set<string>,
): StratumLayout[] {
  const bucketCount = response.bucket_dates.length
  const typeMap = new Map<string, { buckets: number[]; unverifiedBuckets: Set<number>; mentions: number }>()

  for (const s of response.series) {
    const type = s.entity_type ?? "OTHER"
    if (!typeMap.has(type)) {
      typeMap.set(type, { buckets: new Array(bucketCount).fill(0), unverifiedBuckets: new Set(), mentions: 0 })
    }
    const entry = typeMap.get(type)!
    for (let i = 0; i < bucketCount; i++) {
      entry.buckets[i] += s.buckets[i] ?? 0
      if (s.unverified_buckets?.[i]) entry.unverifiedBuckets.add(i)
    }
    entry.mentions += s.buckets.reduce((a, b) => a + b, 0)
  }

  // Build synthetic community-like objects from type buckets
  const sorted = [...typeMap.entries()]
    .sort((a, b) => b[1].mentions - a[1].mentions)
    .slice(0, 8)

  if (sorted.length === 0) return []

  const totalMentions = sorted.reduce((s, [, v]) => s + v.mentions, 0)

  // Assign heights using sqrt scaling
  const MAX_STRATUM_PX = Math.floor(canvasHeight * 0.9)
  const sqrtMax = Math.sqrt(Math.max(1, totalMentions))

  let cursor = 0
  return sorted.map(([type, data], idx): StratumLayout => {
    const sqrt = Math.sqrt(Math.max(1, data.mentions))
    const heightPx = Math.max(2, Math.round((sqrt / sqrtMax) * (MAX_STRATUM_PX / sorted.length) * 3))

    const layout: StratumLayout = {
      communityId: `__type__${type}`,
      label: type,
      colorSlot: idx % 8,
      heightPx,
      topPx: cursor,
      trustMix: { verified: 0, partial: 0, unverified: 0 },
      totalMentions: data.mentions,
      isOther: false,
      bucketHeights: computeBucketHeights(data.buckets, heightPx),
      bucketCounts: data.buckets,
      unverifiedBuckets: data.unverifiedBuckets,
      trackIds: allocateTracksForType(response.tracks, type, trackBudget, response.bucket_dates, pinnedIds),
    }
    cursor += heightPx + 1
    return layout
  })
}

function allocateTracksForType(
  tracks: StrataTrack[],
  entityType: string,
  budget: number,
  bucketDates: string[],
  pinnedIds: Set<string>,
): string[] {
  const candidates = tracks.filter((t) => t.entity_type === entityType)
  candidates.sort((a, b) => trackDOI(b, bucketDates, pinnedIds) - trackDOI(a, bucketDates, pinnedIds))
  return candidates.slice(0, budget).map((t) => t.canonical_id)
}

// ---------------------------------------------------------------------------
// Bucket height computation — sqrt stacking with 2px floor
// ---------------------------------------------------------------------------

function computeBucketHeights(buckets: number[], maxPx: number): number[] {
  const max = Math.max(...buckets, 1)
  const sqrtMax = Math.sqrt(max)
  return buckets.map((v) => v === 0 ? 0 : Math.max(2, Math.round((Math.sqrt(v) / sqrtMax) * maxPx)))
}

// ---------------------------------------------------------------------------
// Main layout computation: strata ordering + track allocation
// ---------------------------------------------------------------------------

export interface ComputeStrataOptions {
  response: TimelineStrataResponse
  canvasHeight: number
  trackBudget: number
  pinnedIds: Set<string>
  /** Frozen order from prior session (amendment 5). null = compute fresh. */
  frozenOrder: string[] | null
}

export interface ComputeStrataResult {
  strata: StratumLayout[]
  /** Order to persist for amendment 5 */
  order: string[]
  /** Whether a window re-rank would change top-8 membership */
  reRankAvailable: boolean
}

export function computeStrata(opts: ComputeStrataOptions): ComputeStrataResult {
  const { response, canvasHeight, trackBudget, pinnedIds, frozenOrder } = opts
  const bucketCount = response.bucket_dates.length

  // Separate "other" rollup from ranked communities
  const ranked = response.communities.filter((c) => !c.is_other)
  const other = response.communities.find((c) => c.is_other)

  // Sort by total_mentions to determine fresh ranking
  const freshRanked = [...ranked].sort((a, b) => b.total_mentions - a.total_mentions)
  const freshOrder = freshRanked.map((c) => c.community_id)

  // Amendment 5: if frozen order exists, use it but detect drift
  let displayOrder: typeof ranked
  let reRankAvailable = false

  if (frozenOrder && frozenOrder.length > 0) {
    const frozenSet = new Set(frozenOrder)
    const freshSet = new Set(freshOrder)
    // Drift = sets differ (some community entered/exited top-8)
    reRankAvailable = !setsEqual(frozenSet, freshSet)

    // Build display order respecting frozen rank, with new entries appended
    const byId = new Map(ranked.map((c) => [c.community_id, c]))
    displayOrder = frozenOrder
      .filter((id) => byId.has(id))
      .map((id) => byId.get(id)!)
    // Append any newly promoted communities not in frozen order
    for (const c of freshRanked) {
      if (!frozenSet.has(c.community_id)) displayOrder.push(c)
    }
  } else {
    displayOrder = freshRanked
    reRankAvailable = false
  }

  // Canvas height is split among strata proportionally by sqrt(mentions)
  // Reserve last 10% for the overview strip
  const stackHeight = Math.floor(canvasHeight * 0.88)
  const totalSqrt = displayOrder.reduce((s, c) => s + Math.sqrt(Math.max(1, c.total_mentions)), 0)
  const otherSqrt = other ? Math.sqrt(Math.max(1, other.total_mentions)) : 0
  const grandSqrt = totalSqrt + otherSqrt || 1

  let cursor = 0
  const strata: StratumLayout[] = []

  for (const community of displayOrder) {
    const sqrt = Math.sqrt(Math.max(1, community.total_mentions))
    const heightPx = Math.max(2, Math.round((sqrt / grandSqrt) * stackHeight))

    const rawBuckets = aggregateBuckets(response.series, community.community_id, bucketCount)
    const unverifiedBuckets = buildUnverifiedBucketSet(response.series, community.community_id)

    const trackIds = allocateTracksForCommunity(
      response.tracks,
      community.community_id,
      trackBudget,
      response.bucket_dates,
      pinnedIds,
    )

    const trustMix = {
      verified: community.trust_mix.verified ?? 0,
      partial: community.trust_mix.partial ?? 0,
      unverified: community.trust_mix.unverified ?? 0,
    }

    strata.push({
      communityId: community.community_id,
      label: community.label,
      // Always the client hash — the server's color_slot is informative only
      // (sha1-based) and would diverge from the Cartographer hulls' hues.
      colorSlot: communitySlot(community.community_id),
      heightPx,
      topPx: cursor,
      trustMix,
      totalMentions: community.total_mentions,
      isOther: false,
      bucketCounts: rawBuckets,
      bucketHeights: computeBucketHeights(rawBuckets, heightPx),
      unverifiedBuckets,
      trackIds,
    })
    cursor += heightPx + 1
  }

  // "Other" rollup at the bottom
  if (other && other.total_mentions > 0) {
    const sqrt = Math.sqrt(Math.max(1, other.total_mentions))
    const heightPx = Math.max(2, Math.round((sqrt / grandSqrt) * stackHeight))
    const rawBuckets = aggregateBuckets(response.series, other.community_id, bucketCount)
    const unverifiedBuckets = buildUnverifiedBucketSet(response.series, other.community_id)

    strata.push({
      communityId: other.community_id,
      label: other.label || "Other",
      colorSlot: -1, // signals "use clusterOther color"
      heightPx,
      topPx: cursor,
      trustMix: {
        verified: other.trust_mix.verified ?? 0,
        partial: other.trust_mix.partial ?? 0,
        unverified: other.trust_mix.unverified ?? 0,
      },
      totalMentions: other.total_mentions,
      isOther: true,
      bucketCounts: rawBuckets,
      bucketHeights: computeBucketHeights(rawBuckets, heightPx),
      unverifiedBuckets,
      trackIds: [],
    })
  }

  return {
    strata,
    order: freshOrder,
    reRankAvailable,
  }
}

function allocateTracksForCommunity(
  tracks: StrataTrack[],
  communityId: string,
  budget: number,
  bucketDates: string[],
  pinnedIds: Set<string>,
): string[] {
  const candidates = tracks.filter((t) => t.community_id === communityId)
  // DOI sort: server rank is informative but we re-apply client bonuses
  candidates.sort((a, b) => trackDOI(b, bucketDates, pinnedIds) - trackDOI(a, bucketDates, pinnedIds))
  return candidates.slice(0, budget).map((t) => t.canonical_id)
}

function setsEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false
  for (const item of a) if (!b.has(item)) return false
  return true
}

// ---------------------------------------------------------------------------
// Marker viewport clustering (≤6 per viewport + overflow badge)
// ---------------------------------------------------------------------------

export function clusterMarkers(
  markers: StrataMarker[],
  fromDate: string,
  toDate: string,
  maxVisible: number = 6,
): MarkerLayout[] {
  if (markers.length === 0) return []

  const t0 = new Date(fromDate).getTime()
  const t1 = new Date(toDate).getTime()
  const span = t1 - t0 || 1

  // Position each marker
  const positioned = markers.map((m) => {
    const t = new Date(m.date).getTime()
    const xFrac = Math.max(0, Math.min(1, (t - t0) / span))
    const label = m.kind === "ingest_burst"
      ? `Ingest burst (${m.count})`
      : `Birth surge (${m.count})`
    return { ...m, xFrac, label }
  })

  if (positioned.length <= maxVisible) return positioned

  // Sort by count desc, keep the most significant; cluster the rest
  const sorted = [...positioned].sort((a, b) => b.count - a.count)
  const kept = sorted.slice(0, maxVisible - 1)
  const overflow = sorted.slice(maxVisible - 1)

  // Create a synthetic cluster badge near the median of the overflowed markers
  const medianX = overflow.reduce((s, m) => s + m.xFrac, 0) / overflow.length
  const totalCount = overflow.reduce((s, m) => s + m.count, 0)
  kept.push({
    date: overflow[0].date,
    kind: "cluster",
    count: totalCount,
    xFrac: medianX,
    label: `+${overflow.length} more (${totalCount} events)`,
  })

  return kept.sort((a, b) => a.xFrac - b.xFrac)
}

// ---------------------------------------------------------------------------
// Trust blend for Trust lens — amendment 1 severity priority
// Returns the CSS color token name suffix to use for a bucket.
// "unverified" always wins if ANY unverified mention is present.
// ---------------------------------------------------------------------------

export function bucketTrustSuffix(
  bucketIdx: number,
  unverifiedBuckets: Set<number>,
  trustMix: { verified: number; partial: number; unverified: number },
): "verified" | "partial" | "unverified" {
  if (unverifiedBuckets.has(bucketIdx)) return "unverified"
  if (trustMix.unverified > 0.1) return "unverified"
  if (trustMix.partial > 0.3) return "partial"
  return "verified"
}
