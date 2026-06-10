// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * TypeScript types mirroring the backend Pydantic models in
 * src/mcp/app/services/wiki_pages.py and
 * src/mcp/app/services/contradiction_log.py.
 *
 * Phase W.1 — entity wiki pages.
 */

// ---------------------------------------------------------------------------
// Confidence band
// ---------------------------------------------------------------------------

export type ConfidenceBand = "high" | "medium" | "low" | "unknown"

// ---------------------------------------------------------------------------
// Contradiction severity
// ---------------------------------------------------------------------------

export type ContradictionSeverity = "low" | "medium" | "high"

// ---------------------------------------------------------------------------
// Contradiction finding (mirrors ContradictionFinding in contradiction_log.py)
// ---------------------------------------------------------------------------

export interface ContradictionFinding {
  finding_id: string
  claim_a_id: string
  claim_b_id: string
  claim_a_text: string
  claim_b_text: string
  entity_slug: string | null
  severity: ContradictionSeverity
  detected_at: string
  query_ctx_id: string | null
  source_artifacts: string[]
}

// ---------------------------------------------------------------------------
// Entity summary (list endpoint)
// ---------------------------------------------------------------------------

/**
 * Lightweight row returned by GET /wiki/entities.
 *
 * Note: the backend model uses ``canonical_id`` as the slug field and
 * ``summary`` / ``summary_updated_at`` for recency.  The frontend
 * normalises these to a stable ``slug`` + ``last_updated_at`` surface so
 * components are decoupled from the backend naming convention.
 */
export interface EntitySummary {
  /** Stable slug (canonical_id from backend). */
  slug: string
  name: string
  entity_type: string
  /** Truncated summary text for list preview (may be null if not yet computed). */
  summary_preview: string | null
  /** Number of entities co-mentioned with this one (approximated from mention_count). */
  related_count: number
  /** Backend recent_activity_score — higher = more recently active. */
  recent_activity_score: number
  /** ISO-8601 timestamp, or null. */
  last_updated_at: string | null
}

// ---------------------------------------------------------------------------
// Related entity (inside a full entity page)
// ---------------------------------------------------------------------------

export interface RelatedEntity {
  /** canonical_id / slug of the related entity. */
  slug: string
  name: string
  /** Co-mention count — strength of the relationship. */
  co_mention_strength: number
}

// ---------------------------------------------------------------------------
// Source citation
// ---------------------------------------------------------------------------

export interface SourceCitation {
  artifact_id: string
  title: string | null
  /** chunk_ids from the backend; used as a chunk-hash chip. */
  chunk_hash: string
  /** Domain derived from artifact metadata (may be empty string). */
  domain: string
}

// ---------------------------------------------------------------------------
// External reference (Phase API.3)
// ---------------------------------------------------------------------------

/**
 * A reference fetched from an external public API (Wikipedia, GitHub, etc.).
 *
 * Structural invariant: external references are ALWAYS visually and structurally
 * distinct from internal-corpus ``source_artifacts``.  They live in a separate
 * ``external_references`` field and are rendered in their own UI section.
 * Never blend them with internal sources.
 */
export interface ExternalReference {
  /** Stable adapter slug, e.g. "wikipedia". */
  source: string
  /** Human-readable label, e.g. "Wikipedia". */
  source_display: string
  /** Canonical title of the resource at the external source. */
  title: string
  /** Short excerpt — 200 chars max. */
  snippet: string
  /** Link to the resource at the external source, or null. */
  url: string | null
  /** ISO-8601 timestamp of when this reference was fetched. */
  fetched_at: string
  /** Adapter-specific extra fields (not shown by default). */
  metadata: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// Full entity page
// ---------------------------------------------------------------------------

export interface WikiEntityPage {
  slug: string
  name: string
  entity_type: string
  summary: string | null
  related_entities: RelatedEntity[]
  source_artifacts: SourceCitation[]
  contradictions: ContradictionFinding[]
  /** External API references (Phase API.3). Empty when enrichment is disabled. */
  external_references: ExternalReference[]
  last_updated_at: string | null
  next_refresh_due: string | null
  confidence_band: ConfidenceBand
  /** Leiden community ID this entity belongs to, or null if unassigned. */
  community_id?: string | null
  /** Human label for the community (from the cartographic map artifact). */
  community_label?: string | null
  /** Total corpus mentions (for display in the identity header capsule). */
  mention_count?: number
  /** Trust state from the graph layer, aligned with GraphNode.trust_state. */
  trust_state?: "verified" | "partial" | "unverified" | "unknown"
}
