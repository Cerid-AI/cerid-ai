// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Browsability API fetchers — Agent C's exclusive module.
 *
 * Covers the three unconsumed wiki endpoints:
 *   GET /wiki/index           — A-Z entity catalog (has_summary, one_liner)
 *   GET /wiki/concepts/{id}   — community concept page
 *   GET /wiki/log             — knowledge log (global or per-entity)
 *
 * Intentionally separate from lib/api/wiki.ts (owned by Agent B) so the
 * two agents' file surfaces are fully disjoint.
 */

import { MCP_BASE, mcpUrl, mcpHeaders } from "./common"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Catalog entry from GET /wiki/index */
export interface WikiIndexEntry {
  slug: string
  name: string
  entity_type: string
  /** 160-char lead sentence; null when no summary has been generated yet. */
  one_liner: string | null
  last_updated_at: string | null
  activity_score: number
  has_summary: boolean
  /** WK3 article completeness class ("stub" | "start" | "full"). Absent on older backends — treat as "stub". */
  completeness?: "stub" | "start" | "full"
}

/** Response envelope from GET /wiki/index */
export interface WikiIndexResponse {
  entries: WikiIndexEntry[]
  /**
   * Total entity count from the backend (when available).
   * May differ from entries.length when the endpoint is paginated or
   * subject to a server-side limit. Absent on older backend versions.
   */
  total: number | null
}

/** Member entity inside a concept page */
export interface ConceptMember {
  slug: string
  name: string
  entity_type: string
}

/** Concept page returned by GET /wiki/concepts/{id} */
export interface WikiConceptPage {
  /** Normalized slug, e.g. "concept:0:2625" */
  slug: string
  /**
   * Resolved human label (e.g. "Python").
   * Falls back to the raw placeholder when Agent A's label fix is not yet
   * deployed — callers must handle "Concept 0:2625"-style strings.
   */
  name: string
  summary: string | null
  member_count: number
  /** Leiden level (0 = coarse community, higher = finer sub-community). */
  level: number
  last_updated_at: string | null
  members: ConceptMember[]
}

/** Single log entry from GET /wiki/log */
export interface WikiLogEntry {
  log_id: string
  ts: string
  /** Verb describing the action: "refresh" | "enrich" | "contradict" */
  action: string
  entity_slug: string
  /** Snapshot summary text at the time of the action; may be null. */
  summary: string | null
  source_artifact_id: string | null
}

// ---------------------------------------------------------------------------
// Normalizers
// ---------------------------------------------------------------------------

function normalizeIndexEntry(raw: Record<string, unknown>): WikiIndexEntry {
  return {
    slug: String(raw.slug ?? raw.canonical_id ?? ""),
    name: String(raw.name ?? ""),
    entity_type: String(raw.entity_type ?? "OTHER"),
    one_liner: raw.one_liner != null ? String(raw.one_liner) : null,
    last_updated_at: raw.last_updated_at != null ? String(raw.last_updated_at) : null,
    activity_score: Number(raw.activity_score ?? raw.recent_activity_score ?? 0),
    has_summary: Boolean(raw.has_summary),
    completeness:
      raw.completeness === "stub" || raw.completeness === "start" || raw.completeness === "full"
        ? raw.completeness
        : undefined,
  }
}

function normalizeConceptMember(raw: Record<string, unknown>): ConceptMember {
  return {
    slug: String(raw.canonical_id ?? raw.slug ?? ""),
    name: String(raw.name ?? ""),
    entity_type: String(raw.entity_type ?? "OTHER"),
  }
}

function normalizeConceptPage(raw: Record<string, unknown>): WikiConceptPage {
  const members = Array.isArray(raw.members)
    ? (raw.members as Record<string, unknown>[]).map(normalizeConceptMember)
    : []
  return {
    slug: String(raw.slug ?? ""),
    name: String(raw.name ?? ""),
    summary: raw.summary != null ? String(raw.summary) : null,
    member_count: Number(raw.member_count ?? members.length),
    level: Number(raw.level ?? 0),
    last_updated_at: raw.last_updated_at != null ? String(raw.last_updated_at) : null,
    members,
  }
}

function normalizeLogEntry(raw: Record<string, unknown>): WikiLogEntry {
  return {
    log_id: String(raw.log_id ?? ""),
    ts: String(raw.ts ?? ""),
    action: String(raw.action ?? ""),
    entity_slug: String(raw.entity_slug ?? ""),
    summary: raw.summary != null ? String(raw.summary) : null,
    source_artifact_id: raw.source_artifact_id != null ? String(raw.source_artifact_id) : null,
  }
}

// ---------------------------------------------------------------------------
// Fetchers
// ---------------------------------------------------------------------------

/**
 * GET /wiki/index
 *
 * Returns the full entity catalog with has_summary and one_liner.
 * Pass `q` for server-side name search (pre-limit, post Agent A's fix).
 * Pass `order="name"` for alphabetical ordering.
 *
 * NOTE (Amendment #7 guard): callers MUST NOT derive per-entity state used
 * elsewhere from this response until the backend is confirmed to return the
 * whole catalog (no limit truncation). The index view renders what the
 * endpoint returns and shows an "N of M" warning when totals indicate truncation.
 */
export async function fetchWikiIndex({
  q,
  order,
  limit,
  includeInternal = false,
}: {
  q?: string
  order?: "name" | "activity"
  limit?: number
  includeInternal?: boolean
} = {}): Promise<WikiIndexResponse> {
  const url = mcpUrl("/wiki/index", {
    q: q?.trim() || undefined,
    order: order ?? undefined,
    limit: limit ?? undefined,
    // WK2: only send when the advanced toggle is on; default omits it so the
    // server excludes the client-data domains.
    include_internal: includeInternal ? "true" : undefined,
  })
  const res = await fetch(url.toString(), { headers: mcpHeaders() })
  if (!res.ok) {
    throw new Error(`Wiki index fetch failed (${res.status})`)
  }
  const body = (await res.json()) as unknown
  // The backend may return a plain array or an object with { items, total }.
  if (Array.isArray(body)) {
    const entries = (body as Record<string, unknown>[]).map(normalizeIndexEntry)
    return { entries, total: null }
  }
  const obj = body as Record<string, unknown>
  const rawItems = Array.isArray(obj.items)
    ? (obj.items as Record<string, unknown>[])
    : Array.isArray(obj.entries)
      ? (obj.entries as Record<string, unknown>[])
      : []
  const entries = rawItems.map(normalizeIndexEntry)
  const total = obj.total != null ? Number(obj.total) : null
  return { entries, total }
}

/**
 * GET /wiki/concepts/{community_id}
 *
 * Returns the concept page for a Leiden community.
 * `communityId` may be in the form "concept:0:2625" or bare "0:2625".
 * Returns null on 404.
 */
export async function fetchWikiConcept(communityId: string): Promise<WikiConceptPage | null> {
  const encoded = encodeURIComponent(communityId)
  const res = await fetch(`${MCP_BASE}/wiki/concepts/${encoded}`, {
    headers: mcpHeaders(),
  })
  if (res.status === 404) return null
  if (!res.ok) {
    throw new Error(`Wiki concept fetch failed (${res.status})`)
  }
  const data = (await res.json()) as Record<string, unknown>
  return normalizeConceptPage(data)
}

/**
 * GET /wiki/log
 *
 * Returns the knowledge log, newest-first.
 * Pass `entitySlug` to scope to a single entity; omit for the global feed.
 */
export async function fetchWikiLog({
  entitySlug,
  limit = 50,
}: {
  entitySlug?: string
  limit?: number
} = {}): Promise<WikiLogEntry[]> {
  const url = mcpUrl("/wiki/log", {
    entity_slug: entitySlug ?? undefined,
    limit,
  })
  const res = await fetch(url.toString(), { headers: mcpHeaders() })
  if (!res.ok) {
    throw new Error(`Wiki log fetch failed (${res.status})`)
  }
  const body = (await res.json()) as unknown
  // The backend returns { entries: [...], total: N }; tolerate a bare array too.
  const rows = Array.isArray(body)
    ? (body as Record<string, unknown>[])
    : Array.isArray((body as Record<string, unknown>).entries)
      ? ((body as Record<string, unknown>).entries as Record<string, unknown>[])
      : []
  return rows.map(normalizeLogEntry)
}
