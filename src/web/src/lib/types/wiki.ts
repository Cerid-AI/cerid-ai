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
// Article completeness class (WK3)
// ---------------------------------------------------------------------------

/** WK3 curation class. "stub" = no summary; "start" = short or few mentions; "full" = both thresholds met. */
export type Completeness = "stub" | "start" | "full"

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
  /** Corpus mention count (backend field ``mention_count`` — was incorrectly labeled ``related_count``). */
  mention_count: number
  /** Backend recent_activity_score — higher = more recently active. */
  recent_activity_score: number
  /** ISO-8601 timestamp, or null. */
  last_updated_at: string | null
  /**
   * Primary domain derived by the DeriveDomainsJob (e.g. "research", "coding").
   * Null for orphan entities (no MENTIONS path) or pre-job state.
   */
  primary_domain: string | null
  /**
   * Top controlled-vocabulary tags (Slice 6.3), salience-ordered, capped at 5.
   * Null/empty until DeriveDomainsJob runs. Surfaces list tag sort/filter.
   */
  top_tags?: string[] | null
  /**
   * Search relevance rank from the backend (0=exact, 1=prefix, 2=substring, 3=canonical-only).
   * Present only in search results (q non-empty); absent in browse results.
   */
  match_rank?: number | null
}

// ---------------------------------------------------------------------------
// Wiki log entry (GET /wiki/log)
// ---------------------------------------------------------------------------

export interface WikiLogEntry {
  log_id: string
  /** ISO-8601 timestamp. */
  ts: string
  /** Action verb: "refresh" | "enrich" | "contradict" | string */
  action: string
  entity_slug: string
  /** Summary snapshot at the time of the log entry. */
  summary: string | null
  /** Source artifact that triggered the entry, if present. */
  source_artifact_id: string | null
}

// ---------------------------------------------------------------------------
// Episodic memory (assembled by wiki_pages.py — render deferred to v1.1)
// ---------------------------------------------------------------------------

export interface EpisodicMemory {
  memory_type: string
  valid_from: string | null
  access_count: number
  content: string | null
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
  /** Entity type (e.g. "ORG", "OTHER"). Dropped by the old normalizer; now preserved. */
  entity_type: string
  /** Backend coalesced display title. Preserved from normalizer for wikilink hover cards. */
  display_title: string | null
  /** True when the related entity already has a generated summary. Enables three-state wikilink styling. */
  has_summary: boolean
  /** First 160 chars of the related entity's summary, for HoverCard previews. Null when has_summary is false. */
  one_liner: string | null
}

// ---------------------------------------------------------------------------
// Source citation
// ---------------------------------------------------------------------------

export interface SourceCitation {
  artifact_id: string
  /**
   * Display title: coalesce(a.title, a.filename) from the backend.
   * Non-null whenever the artifact has a filename.
   */
  title: string | null
  /** Filename from the artifact node — used as fallback display label. */
  filename: string | null
  /** Domain derived from artifact metadata (e.g. "notes", "finance"). */
  domain: string | null
  /** Artifact source type (e.g. "file", "vault", "url"). */
  source_type: string | null
  /** Confidence of the MENTIONS edge (0..1). */
  confidence: number | null
  /** ISO-8601 — when this artifact was last updated. */
  updated_at: string | null
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
  /**
   * Actual refresh-job state from the backend scheduler.
   *   "idle"    — no refresh scheduled or needed.
   *   "due"     — a refresh is scheduled but not yet started.
   *   "running" — a wiki-refresh job is actively in flight for this entity.
   * Absent when the backend has not yet shipped this field (treated as "idle").
   */
  refresh_status?: "idle" | "due" | "running"
  confidence_band: ConfidenceBand
  /** Leiden community ID this entity belongs to, or null if unassigned. */
  community_id?: string | null
  /** Human label for the community (from the cartographic map artifact). */
  community_label?: string | null
  /** Total corpus mentions (for display in the identity header capsule). */
  mention_count?: number
  /**
   * Primary domain derived by DeriveDomainsJob (e.g. "research").
   * Null for orphans or pre-job state.
   */
  primary_domain?: string | null
  /**
   * Full domain distribution — JSON-parsed dict of domain → mention count,
   * sorted by count desc. Keys are domain names; values are distinct
   * artifact mention counts. Null when domain data is not yet derived.
   * Example: { "research": 5, "coding": 2 }
   */
  domain_mix?: Record<string, number> | null
  /**
   * Salience-ordered domain weights (Slice 6.1) — domain → salience score,
   * ordered by salience desc. Salience reweights raw counts by specificity,
   * distinctiveness, quality, and recency, so this ordering can differ from
   * domain_mix's raw-count ordering. Null until DeriveDomainsJob runs.
   * Example: { "finance": 45.0, "general": 11.25 }
   */
  domain_salience?: Record<string, number> | null
  /**
   * Top controlled-vocabulary tags for this entity (Slice 6.3), salience-
   * ordered, capped at 5. Vocabulary-only — free-form tags never appear here.
   * Null/empty until DeriveDomainsJob runs.
   */
  top_tags?: string[] | null
  /**
   * Most common sub_category among artifacts in the primary domain.
   * Null when no signal (all artifacts carry the default subcategory).
   */
  primary_subcategory?: string | null
  /**
   * Episodic memories assembled by wiki_pages.py (≤5 rows).
   * Normalized in v1; section render deferred to v1.1 pending non-empty live data.
   */
  episodic_memories?: EpisodicMemory[]
  /**
   * WK3 article completeness class.
   * "stub"  = no summary generated yet.
   * "start" = summary present but below length or mention thresholds.
   * "full"  = both length and mention thresholds met.
   * Absent on older backend versions (treat as "stub").
   */
  completeness?: Completeness
}

// ---------------------------------------------------------------------------
// WK1 — Backlinks ("what links here")
// ---------------------------------------------------------------------------

/** How a source entity links to the target. */
export type BacklinkVia = "wikilink" | "mention" | "related"

/** One row in the backlinks list. */
export interface BacklinkItem {
  slug: string
  name: string
  entity_type: string
  /**
   * "wikilink"  — the source entity's summary contains a [[…]] wikilink to this entity.
   * "mention"   — both entities appear in the same source artifact.
   * "related"   — a direct CO_MENTIONED graph edge connects them.
   */
  via: BacklinkVia
}

/** Response shape from GET /wiki/entities/{slug}/backlinks */
export interface BacklinksResponse {
  backlinks: BacklinkItem[]
}
