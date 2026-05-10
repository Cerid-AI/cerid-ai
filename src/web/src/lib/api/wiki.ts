// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Wiki API functions — Phase W.1.
 *
 * Wraps the backend routes:
 *   GET /wiki/entities?limit=N
 *   GET /wiki/entities/{slug}
 *   GET /wiki/contradictions
 */

import { MCP_BASE, mcpHeaders } from "./common"
import type {
  EntitySummary,
  ExternalReference,
  WikiEntityPage,
  ContradictionFinding,
  RelatedEntity,
  SourceCitation,
} from "@/lib/types/wiki"

// ---------------------------------------------------------------------------
// Internal normalizers — adapt backend snake_case shapes to frontend types
// ---------------------------------------------------------------------------

function normalizeEntitySummary(raw: Record<string, unknown>): EntitySummary {
  return {
    slug: String(raw.canonical_id ?? ""),
    name: String(raw.name ?? ""),
    entity_type: String(raw.entity_type ?? "OTHER"),
    summary_preview: raw.summary != null ? String(raw.summary) : null,
    related_count: Number(raw.mention_count ?? 0),
    recent_activity_score: Number(raw.recent_activity_score ?? 0),
    last_updated_at: raw.summary_updated_at != null ? String(raw.summary_updated_at) : null,
  }
}

function normalizeRelatedEntity(raw: Record<string, unknown>): RelatedEntity {
  return {
    slug: String(raw.canonical_id ?? ""),
    name: String(raw.name ?? ""),
    co_mention_strength: Number(raw.co_mention_count ?? 0),
  }
}

function normalizeSourceCitation(raw: Record<string, unknown>): SourceCitation {
  const chunkIds = Array.isArray(raw.chunk_ids) ? (raw.chunk_ids as string[]) : []
  return {
    artifact_id: String(raw.artifact_id ?? ""),
    title: raw.title != null ? String(raw.title) : null,
    chunk_hash: chunkIds[0] ?? "",
    domain: "",
  }
}

function normalizeContradiction(raw: Record<string, unknown>): ContradictionFinding {
  const severity = raw.severity as ContradictionFinding["severity"] | undefined
  return {
    finding_id: String(raw.finding_id ?? ""),
    claim_a_id: String(raw.claim_a_id ?? ""),
    claim_b_id: String(raw.claim_b_id ?? ""),
    claim_a_text: String(raw.claim_a_text ?? ""),
    claim_b_text: String(raw.claim_b_text ?? ""),
    entity_slug: raw.entity_slug != null ? String(raw.entity_slug) : null,
    severity: severity ?? "medium",
    detected_at: String(raw.detected_at ?? ""),
    query_ctx_id: raw.query_ctx_id != null ? String(raw.query_ctx_id) : null,
    source_artifacts: Array.isArray(raw.source_artifacts)
      ? (raw.source_artifacts as string[])
      : [],
  }
}

function normalizeExternalReference(raw: Record<string, unknown>): ExternalReference {
  return {
    source: String(raw.source ?? ""),
    source_display: String(raw.source_display ?? ""),
    title: String(raw.title ?? ""),
    snippet: String(raw.snippet ?? ""),
    url: raw.url != null ? String(raw.url) : null,
    fetched_at: String(raw.fetched_at ?? ""),
    metadata: (raw.metadata != null && typeof raw.metadata === "object" && !Array.isArray(raw.metadata))
      ? (raw.metadata as Record<string, unknown>)
      : {},
  }
}

function normalizeEntityPage(raw: Record<string, unknown>): WikiEntityPage {
  const related = Array.isArray(raw.related_entities)
    ? (raw.related_entities as Record<string, unknown>[]).map(normalizeRelatedEntity)
    : []
  const sources = Array.isArray(raw.source_artifacts)
    ? (raw.source_artifacts as Record<string, unknown>[]).map(normalizeSourceCitation)
    : []
  const contradictions = Array.isArray(raw.contradictions)
    ? (raw.contradictions as Record<string, unknown>[]).map(normalizeContradiction)
    : []
  const externalRefs = Array.isArray(raw.external_references)
    ? (raw.external_references as Record<string, unknown>[]).map(normalizeExternalReference)
    : []

  return {
    slug: String(raw.slug ?? ""),
    name: String(raw.name ?? ""),
    entity_type: String(raw.entity_type ?? "OTHER"),
    summary: raw.summary != null ? String(raw.summary) : null,
    related_entities: related,
    source_artifacts: sources,
    contradictions,
    external_references: externalRefs,
    last_updated_at: raw.last_updated_at != null ? String(raw.last_updated_at) : null,
    next_refresh_due: raw.next_refresh_due != null ? String(raw.next_refresh_due) : null,
    confidence_band: (raw.confidence_band as WikiEntityPage["confidence_band"]) ?? "unknown",
  }
}

// ---------------------------------------------------------------------------
// Public API functions
// ---------------------------------------------------------------------------

/**
 * GET /wiki/entities?limit=N
 *
 * Returns up to `limit` entity summaries ordered by recent activity.
 */
export async function fetchWikiEntities({
  limit = 30,
}: { limit?: number } = {}): Promise<EntitySummary[]> {
  const url = `${MCP_BASE}/wiki/entities?limit=${limit}`
  const res = await fetch(url, { headers: mcpHeaders() })
  if (!res.ok) {
    throw new Error(`Wiki entities fetch failed (${res.status})`)
  }
  const data = (await res.json()) as Record<string, unknown>[]
  return data.map(normalizeEntitySummary)
}

/**
 * GET /wiki/entities/{slug}
 *
 * Returns the full entity page, or null on 404.
 */
export async function fetchWikiEntity(slug: string): Promise<WikiEntityPage | null> {
  const res = await fetch(`${MCP_BASE}/wiki/entities/${encodeURIComponent(slug)}`, {
    headers: mcpHeaders(),
  })
  if (res.status === 404) return null
  if (!res.ok) {
    throw new Error(`Wiki entity fetch failed (${res.status})`)
  }
  const data = (await res.json()) as Record<string, unknown>
  return normalizeEntityPage(data)
}

/**
 * GET /wiki/contradictions
 *
 * Used by both the entity detail page (entity_slug filter) and the
 * standalone contradictions view. Export so callers can reuse.
 */
export async function fetchContradictions({
  entity_slug,
  since,
  limit = 100,
}: {
  entity_slug?: string
  since?: string
  limit?: number
} = {}): Promise<ContradictionFinding[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (entity_slug) params.set("entity_slug", entity_slug)
  if (since) params.set("since", since)

  const res = await fetch(`${MCP_BASE}/wiki/contradictions?${params.toString()}`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) {
    throw new Error(`Contradictions fetch failed (${res.status})`)
  }
  const data = (await res.json()) as Record<string, unknown>[]
  return data.map(normalizeContradiction)
}
