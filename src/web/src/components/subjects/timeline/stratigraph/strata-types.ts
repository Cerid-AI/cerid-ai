// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// FROZEN payload contract for the Tephra Cycle-2 timeline re-think.
//
// THIS FILE IS READ-ONLY AFTER INITIAL COMMIT.
// Agents A (backend), B (canvas), and C (panel) all import from here.
// Backend mirrors these shapes in Pydantic response models.
// Additive changes only — never remove or rename a field without a
// full-sprint migration.

// ---------------------------------------------------------------------------
// Event layer — KnowledgeLog, ContradictionFinding, VerificationReport
// ---------------------------------------------------------------------------

/** The kind discriminator for each event class. */
export type EventKind =
  | "refresh"      // KnowledgeLog action=refresh
  | "enrich"       // KnowledgeLog action=enrich
  | "contradict"   // KnowledgeLog action=contradict → merged into diamond treatment
  | "contradiction_finding"  // ContradictionFinding node
  | "verification_report"    // VerificationReport aggregate tick

/** Severity level for contradiction events. Maps to the existing
 *  verification-band color scale: high=red, med=amber, low=muted amber. */
export type ContradictionSeverity = "high" | "medium" | "low"

/** A single knowledge event, capped at ≤200/window and ≤8/bucket
 *  server-side. The `overflow_count` sibling carries the remainder. */
export interface StrataEvent {
  /** ISO-8601 timestamp */
  ts: string
  kind: EventKind
  /** Lane this event belongs to (entity_slug → primary_domain mapping) */
  lane_id: string
  /** Canonical entity slug, if applicable */
  entity_slug?: string | null
  /** Human-readable entity name */
  entity_name?: string | null
  /** Short excerpt or summary (≤140 chars) from the source artifact */
  summary?: string | null
  /** Source artifact ID for L2 deep-link */
  source_artifact_id?: string | null
  /** Contradiction-specific fields — present when kind is "contradiction_finding" */
  severity?: ContradictionSeverity | null
  /** Denormalized claim texts for contradiction events (no extra fetch needed) */
  claim_a?: string | null
  claim_b?: string | null
  /** Verification report aggregate — present when kind is "verification_report".
   *  NOT a per-glyph count — one aggregate tick per (lane, bucket). */
  verification_count?: number | null
  /** Spike flag: this bucket's report count exceeds max(10, 3×median) */
  is_spike?: boolean | null
}

/** Events are capped at ≤200/window total and ≤8/bucket.
 *  Any clipped events are reflected in overflow_count. */
export interface StrataEventsBlock {
  events: StrataEvent[]
  /** Count of events omitted by the server cap — for overflow badge. */
  overflow_count: number
}

// ---------------------------------------------------------------------------
// Verification aggregates — per-(lane, bucket) rolled up, never per-glyph
//
// Amendment #2: suppressed when report_count < 3.
// ---------------------------------------------------------------------------

export interface VerificationAggregate {
  /** Total VerificationReports in this (lane, bucket) */
  report_count: number
  verified: number
  unverified: number
  uncertain: number
  /** 0..1 average overall_score across reports */
  overall_score_avg: number
  /** True when report_count > max(10, 3×median) in this lane's series */
  is_spike: boolean
}

// ---------------------------------------------------------------------------
// Lane meta — replaces the File-icon fallback and hub-name-only labels
// ---------------------------------------------------------------------------

/** Per-lane metadata carried in the strata response lanes[] block.
 *  Agent B uses this to kill the File-icon fallback (StratigraphCanvas:1071)
 *  and render real icons + labels. Agent C uses it for the gutter/legend. */
export interface LaneMeta {
  /** The lane identifier — matches StrataEvent.lane_id and StratumLayout.communityId */
  lane_id: string
  /** Display label (short, ≤32 chars).
   *  Domain lens: taxonomy name (e.g. "research", "coding").
   *  Community lens: Community.summary first clause OR top_hubs[0].name fallback. */
  label: string
  /** Lucide icon name for this lane (e.g. "BookOpen", "Code2", "Database").
   *  null means use the hub-name fallback + auto-label badge (amendment #6). */
  icon: string | null
  /** Whether this lane's label was derived from a Community.summary (true)
   *  or fell back to hub-name (false). Drives the CircleDashed auto-label badge
   *  in the DOM legend/gutter for unsummarized community lanes (amendment #6). */
  is_auto_label: boolean
  /** Short community summary text for HoverCard (community lens only). null otherwise. */
  summary_short?: string | null
  /** Full community summary for L2 card (community lens only). null otherwise. */
  summary_full?: string | null
}

// ---------------------------------------------------------------------------
// Extended StrataMarker — carries lane_id per the v1_scope spec
// ---------------------------------------------------------------------------

/** Extends the existing StrataMarker with an optional lane_id so per-lane
 *  burst/surge markers can be attributed to a specific stratum.
 *  lane_id is null/undefined for corpus-wide composite markers (the existing global rail). */
export interface StrataMarkerExtended {
  date: string
  kind: string
  count: number
  /** null/undefined = corpus-wide; non-null = per-lane attribution (Tephra: hatching on owning lane) */
  lane_id?: string | null
}

// ---------------------------------------------------------------------------
// Extended strata response payload — the full Tephra payload shape
// ---------------------------------------------------------------------------

/** The additive Tephra extension to TimelineStrataResponse.
 *  Agent A appends these fields to the existing strata endpoint RETURN shape.
 *  Agent C's use-timeline-strata.ts hook augments TimelineStrataResponse with this. */
export interface StrataExtension {
  /** Per-(lane, bucket) events block. Key = `${lane_id}:${bucket_date}`. */
  events_by_lane_bucket: Record<string, StrataEventsBlock>
  /** Per-(lane, bucket) verification aggregates (suppressed when count < 3).
   *  Key = `${lane_id}:${bucket_date}`. */
  verification_by_lane_bucket: Record<string, VerificationAggregate>
  /** Per-lane metadata block (kills File fallback, carries icons + labels). */
  lanes: LaneMeta[]
  /** Top entities per (lane, bucket) — ≤3 names for L1 hover tooltip.
   *  Key = `${lane_id}:${bucket_date}`. */
  top_entities_by_lane_bucket: Record<string, TopEntity[]>
  /** ISO-8601 date the event ledger began (KnowledgeLog first write).
   *  Used to render the pre-ledger honesty hairline. */
  ledger_start_date: string | null
}

/** A top entity for L1 hover tooltip. */
export interface TopEntity {
  name: string
  slug: string
}

// ---------------------------------------------------------------------------
// Extended track response — additive fields on TimelineTrackResponse
// ---------------------------------------------------------------------------

/** New entity introduced in this track's window. */
export interface NewEntity {
  name: string
  slug: string
  /** ISO-8601 creation timestamp */
  created_at: string
}

/** Extended track-level event for the L2 bucket-detail card. */
export interface TrackEventExtended {
  kind: EventKind
  /** ISO-8601 timestamp */
  ts: string
  entity_slug: string
  entity_name: string
  summary: string | null
  source_artifact_id: string | null
  severity: ContradictionSeverity | null
  /** Denormalized contradiction claim texts — no extra fetch needed at click time. */
  claim_a: string | null
  claim_b: string | null
}

/** Verification summary for the track's current window. */
export interface TrackVerification {
  reports: number
  verified: number
  unverified: number
  uncertain: number
  overall_score_avg: number
}

/** Additive fields on the /graph/timeline/track/{id}?bucket= response.
 *  Agent A appends these; Agent C consumes them for the detail card. */
export interface TimelineTrackExtension {
  /** Entities first seen in this bucket/window. */
  new_entities: NewEntity[]
  /** Knowledge events in this bucket/window (wire name avoids colliding
   *  with the legacy mention-events `events` field on the same response). */
  knowledge_events: TrackEventExtended[]
  /** Verification aggregate for this window. */
  verification: TrackVerification
  /** Community summary text (community lens only). null in domain/type lenses. */
  community_summary: string | null
}

// ---------------------------------------------------------------------------
// Since-marker contract — shared between Agent B (canvas hairline) and
// Agent C (panel prop / lastViewedAt persistence)
// ---------------------------------------------------------------------------

/** The "since you last looked" band contract.
 *  Agent C persists lastViewedAt and passes it as a prop.
 *  Agent B renders the vertical hairline + per-lane delta chips. */
export interface SinceMarker {
  /** ISO-8601 timestamp of last pane view. null = never viewed (no band rendered). */
  lastViewedAt: string | null
  /** Per-lane delta counts since lastViewedAt.
   *  Key = lane_id. Absent entries = zero delta. */
  deltaByLane: Record<string, LaneDelta>
}

export interface LaneDelta {
  /** New entity mentions since lastViewedAt */
  mentions: number
  /** New refresh/enrich events since lastViewedAt */
  refreshes: number
  /** New contradiction findings since lastViewedAt */
  contradictions: number
}

// ---------------------------------------------------------------------------
// Canvas prop extensions for Agent B — new callbacks + props Agent C must wire
// ---------------------------------------------------------------------------

/** The event click callback Agent B fires, deferred to Agent C's panel.
 *  Agent C wires this to open the event detail card.
 *
 *  Exact signature for Agent C:
 *    onEventClick: (event: StrataEvent) => void
 */
export type OnEventClickFn = (event: StrataEvent) => void

// ---------------------------------------------------------------------------
// MARKER_KIND_META registry — replaces the hardcoded two-kind tables in:
//   - strata-layout.ts (clusterMarkers label block, ~:578-580)
//   - StratigraphCanvas.tsx (DOM marker label block, ~:974)
// ---------------------------------------------------------------------------

/** MapTokens key names used for canvas rendering.
 *  Subset of the full MapTokens interface — only the color keys relevant to markers. */
export type MarkerColorTokenKey =
  | "interaction"
  | "trustVerified"
  | "trustPartial"
  | "trustUnverified"
  | "clusterOther"
  | "domainOther"
  | "edge"
  | "foreground"

export interface MarkerKindMeta {
  /** Human-readable label prefix, e.g. "Ingest burst" */
  label: string
  /** Short label for DOM strip (e.g. "ingest", "birth") */
  shortLabel: string
  /** Resolved token key in MapTokens for canvas rendering.
   *  Uses the existing tokens.* naming convention. */
  colorTokenKey: MarkerColorTokenKey
  /** Whether this kind can be clustered (contradictions must NOT be clustered). */
  clusterable: boolean
}

export const MARKER_KIND_META: Record<string, MarkerKindMeta> = {
  ingest_burst: {
    label: "Ingest burst",
    shortLabel: "ingest",
    colorTokenKey: "interaction",
    clusterable: true,
  },
  birth_surge: {
    label: "Birth surge",
    shortLabel: "birth",
    colorTokenKey: "interaction",
    clusterable: true,
  },
  // Contradiction markers are never clustered — each one gets full prominence.
  contradiction_finding: {
    label: "Contradiction",
    shortLabel: "⚠",
    colorTokenKey: "trustUnverified",
    clusterable: false,
  },
  verification_spike: {
    label: "Verification spike",
    shortLabel: "verify↑",
    colorTokenKey: "trustPartial",
    clusterable: true,
  },
}
