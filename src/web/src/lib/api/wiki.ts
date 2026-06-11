// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Wiki API functions — Phase W.1 + RAG C3.3 / C3.4.
 *
 * Wraps the backend routes:
 *   GET  /wiki/entities?limit=N
 *   GET  /wiki/entities/{slug}
 *   GET  /wiki/contradictions
 *   POST /wiki/write_note            (RAG C3.3 — two-way vault writeback)
 *   GET  /watched-folders            (filtered to is_vault=true for the
 *                                     save-to-vault UI)
 */

import { MCP_BASE, mcpHeaders, mcpUrl, extractError } from "./common"
import type { WatchedFolder } from "./settings"
import { fetchWatchedFolders } from "./settings"
import type {
  EntitySummary,
  ExternalReference,
  WikiEntityPage,
  ContradictionFinding,
  RelatedEntity,
  SourceCitation,
  WikiLogEntry,
  EpisodicMemory,
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
    mention_count: Number(raw.mention_count ?? 0),
    recent_activity_score: Number(raw.recent_activity_score ?? 0),
    last_updated_at: raw.summary_updated_at != null ? String(raw.summary_updated_at) : null,
    primary_domain: raw.primary_domain != null ? String(raw.primary_domain) : null,
    match_rank: raw.match_rank != null ? Number(raw.match_rank) : null,
  }
}

function normalizeRelatedEntity(raw: Record<string, unknown>): RelatedEntity {
  return {
    slug: String(raw.canonical_id ?? ""),
    name: String(raw.name ?? ""),
    co_mention_strength: Number(raw.co_mention_count ?? 0),
    entity_type: String(raw.entity_type ?? "OTHER"),
    display_title: raw.display_title != null ? String(raw.display_title) : null,
    has_summary: Boolean(raw.has_summary ?? false),
    one_liner: raw.one_liner != null ? String(raw.one_liner) : null,
  }
}

function normalizeEpisodicMemory(raw: Record<string, unknown>): EpisodicMemory {
  return {
    memory_type: String(raw.memory_type ?? ""),
    valid_from: raw.valid_from != null ? String(raw.valid_from) : null,
    access_count: Number(raw.access_count ?? 0),
    content: raw.content != null ? String(raw.content) : null,
  }
}

function normalizeSourceCitation(raw: Record<string, unknown>): SourceCitation {
  return {
    artifact_id: String(raw.artifact_id ?? ""),
    // Prefer backend display_title (already coalesced: a.title ?? a.filename). Fall back
    // through title → filename so old backend responses still display something.
    title: raw.display_title != null
      ? String(raw.display_title)
      : raw.title != null
        ? String(raw.title)
        : null,
    filename: raw.filename != null ? String(raw.filename) : null,
    domain: raw.domain != null ? String(raw.domain) : null,
    source_type: raw.source_type != null ? String(raw.source_type) : null,
    confidence: raw.confidence != null ? Number(raw.confidence) : null,
    updated_at: raw.updated_at != null ? String(raw.updated_at) : null,
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
  const episodicMemories = Array.isArray(raw.episodic_memories)
    ? (raw.episodic_memories as Record<string, unknown>[]).map(normalizeEpisodicMemory)
    : []

  const rawRefreshStatus = raw.refresh_status as string | undefined
  const refresh_status: WikiEntityPage["refresh_status"] =
    rawRefreshStatus === "running" || rawRefreshStatus === "due" || rawRefreshStatus === "idle"
      ? rawRefreshStatus
      : undefined

  // domain_mix is stored as a JSON string on the backend; the backend service
  // already parses it to dict before serializing the page payload, but we
  // guard both shapes here in case an older backend sends the raw string.
  let domain_mix: Record<string, number> | null = null
  if (raw.domain_mix != null) {
    if (typeof raw.domain_mix === "object" && !Array.isArray(raw.domain_mix)) {
      domain_mix = raw.domain_mix as Record<string, number>
    } else if (typeof raw.domain_mix === "string") {
      try {
        domain_mix = JSON.parse(raw.domain_mix) as Record<string, number>
      } catch {
        domain_mix = null
      }
    }
  }

  return {
    slug: String(raw.slug ?? ""),
    name: String(raw.name ?? ""),
    entity_type: String(raw.entity_type ?? "OTHER"),
    community_id: raw.community_id != null ? String(raw.community_id) : undefined,
    community_label: raw.community_label != null ? String(raw.community_label) : undefined,
    mention_count: Number(raw.mention_count ?? 0),
    summary: raw.summary != null ? String(raw.summary) : null,
    related_entities: related,
    source_artifacts: sources,
    contradictions,
    external_references: externalRefs,
    last_updated_at: raw.last_updated_at != null ? String(raw.last_updated_at) : null,
    next_refresh_due: raw.next_refresh_due != null ? String(raw.next_refresh_due) : null,
    refresh_status,
    confidence_band: (raw.confidence_band as WikiEntityPage["confidence_band"]) ?? "unknown",
    primary_domain: raw.primary_domain != null ? String(raw.primary_domain) : null,
    domain_mix,
    primary_subcategory: raw.primary_subcategory != null ? String(raw.primary_subcategory) : null,
    episodic_memories: episodicMemories.length > 0 ? episodicMemories : undefined,
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
  q,
}: { limit?: number; q?: string } = {}): Promise<EntitySummary[]> {
  const url = mcpUrl("/wiki/entities", { limit, q: q?.trim() || undefined })
  const res = await fetch(url.toString(), { headers: mcpHeaders() })
  if (!res.ok) {
    throw new Error(`Wiki entities fetch failed (${res.status})`)
  }
  const rows = (await res.json()) as Record<string, unknown>[]
  return rows.map(normalizeEntitySummary)
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

// ---------------------------------------------------------------------------
// RAG C3.3 / C3.4 — vault writeback
// ---------------------------------------------------------------------------

export type WriteNoteMode = "create" | "append" | "overwrite"

export interface WriteNoteRequest {
  vault_id: string
  path: string
  content: string
  frontmatter?: Record<string, unknown>
  mode?: WriteNoteMode
  allow_synthesis_input?: boolean
}

export interface WriteNoteResponse {
  file_path: string
  artifact_id: string | null
  ingested: boolean
  frontmatter_written: Record<string, unknown>
  mode: string
  reingest_error: string | null
}

/**
 * POST /wiki/write_note
 *
 * Writes a markdown note into a registered vault and re-ingests it as
 * an Artifact tagged `source_type='cerid-synthesis'`.  The backend
 * rejects:
 *   - unknown / non-vault `vault_id` (400)
 *   - path-traversal attempts and templates/attachments folders (400)
 *   - mode='create' against an existing file (400)
 *
 * Frontend callers surface 4xx as the response body's `detail` via
 * `extractError`; 5xx are user-visible "vault write failed" messages.
 */
export async function writeNote(req: WriteNoteRequest): Promise<WriteNoteResponse> {
  const res = await fetch(`${MCP_BASE}/wiki/write_note`, {
    method: "POST",
    headers: { ...mcpHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) {
    throw new Error(await extractError(res, `Vault write failed (${res.status})`))
  }
  return (await res.json()) as WriteNoteResponse
}

/**
 * GET /watched-folders filtered to vaults.
 *
 * Helper used by the save-to-vault dialog to populate its vault
 * selector.  Surfaces the same `WatchedFolder` shape as
 * `fetchWatchedFolders` so callers can re-use existing typing.
 */
export async function fetchVaultsList(): Promise<WatchedFolder[]> {
  const { folders } = await fetchWatchedFolders()
  return folders.filter((f) => Boolean(f.is_vault))
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
  const rows = (await res.json()) as Record<string, unknown>[]
  return rows.map(normalizeContradiction)
}

function normalizeWikiLogEntry(raw: Record<string, unknown>): WikiLogEntry {
  return {
    log_id: String(raw.log_id ?? ""),
    ts: String(raw.ts ?? ""),
    action: String(raw.action ?? ""),
    entity_slug: String(raw.entity_slug ?? ""),
    summary: raw.summary != null ? String(raw.summary) : null,
    source_artifact_id: raw.source_artifact_id != null ? String(raw.source_artifact_id) : null,
  }
}

/**
 * GET /wiki/log?entity_slug={slug}
 *
 * Per-entity revision ledger (newest-first). Rows carry the snapshot
 * summary text so the history pane can show a collapsed diff per entry.
 */
export async function fetchWikiLog({
  entity_slug,
  limit = 50,
}: {
  entity_slug: string
  limit?: number
}): Promise<WikiLogEntry[]> {
  const url = mcpUrl("/wiki/log", { entity_slug, limit })
  const res = await fetch(url.toString(), { headers: mcpHeaders() })
  if (!res.ok) {
    throw new Error(`Wiki log fetch failed (${res.status})`)
  }
  const rows = (await res.json()) as Record<string, unknown>[]
  return rows.map(normalizeWikiLogEntry)
}
