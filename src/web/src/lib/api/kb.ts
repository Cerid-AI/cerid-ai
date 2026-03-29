// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { MCP_BASE, mcpHeaders, extractError } from "./common"
import { parseTags } from "@/lib/utils"

import type {
  AgentQueryResponse,
  Artifact,
  ArtifactDetail,
  RelatedArtifact,
  MaintenanceResponse,
  RectifyResponse,
  CurateResponse,
  TaxonomyResponse,
  SchedulerStatus,
  IngestLogResponse,
  AuditResponse,
  DigestResponse,
  SynopsisEstimate,
  TagSuggestion,
  UploadResult,
  ParserCapability,
  ArtifactFilterParams,
  RagMode,
  MemoryRecallResult,
} from "../types"

// --- Knowledge Base ---

export async function queryKB(
  query: string,
  domains?: string[],
  topK = 10,
  conversationMessages?: { role: string; content: string }[],
): Promise<AgentQueryResponse> {
  const res = await fetch(`${MCP_BASE}/agent/query`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      query,
      domains: domains ?? null,
      top_k: topK,
      use_reranking: true,
      conversation_messages: conversationMessages ?? null,
    }),
  })
  if (!res.ok) throw new Error(await extractError(res, `KB query failed: ${res.status}`))
  return res.json()
}

export async function queryKBOrchestrated(
  query: string,
  ragMode: RagMode,
  domains?: string[],
  topK = 10,
  conversationMessages?: { role: string; content: string }[],
  sourceConfig?: Record<string, unknown>,
): Promise<AgentQueryResponse> {
  const res = await fetch(`${MCP_BASE}/agent/query`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      query,
      rag_mode: ragMode,
      domains: domains ?? null,
      top_k: topK,
      use_reranking: true,
      conversation_messages: conversationMessages ?? null,
      source_config: sourceConfig ?? null,
    }),
  })
  if (!res.ok) throw new Error(await extractError(res, `KB query failed: ${res.status}`))
  return res.json()
}

export async function recallMemories(
  query: string,
  topK = 5,
  minScore = 0.4,
): Promise<MemoryRecallResult[]> {
  const res = await fetch(`${MCP_BASE}/agent/memory/recall`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ query, top_k: topK, min_score: minScore }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Memory recall failed: ${res.status}`))
  return res.json()
}

export async function fetchArtifacts(domain?: string, limit = 50): Promise<Artifact[]> {
  const params = new URLSearchParams()
  if (domain) params.set("domain", domain)
  params.set("limit", String(limit))
  const res = await fetch(`${MCP_BASE}/artifacts?${params}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Artifacts fetch failed: ${res.status}`))
  const raw = await res.json()
  const artifacts: Artifact[] = Array.isArray(raw) ? raw : []
  return artifacts.map((a) => ({ ...a, tags: parseTags(a.tags) }))
}

export async function fetchRelatedArtifacts(
  artifactId: string,
  depth = 2,
  maxResults = 5,
): Promise<RelatedArtifact[]> {
  const params = new URLSearchParams({ depth: String(depth), max_results: String(maxResults) })
  const res = await fetch(`${MCP_BASE}/artifacts/${artifactId}/related?${params}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Related artifacts fetch failed: ${res.status}`))
  return res.json()
}

export async function fetchArtifactDetail(artifactId: string): Promise<ArtifactDetail> {
  const res = await fetch(`${MCP_BASE}/artifacts/${artifactId}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Artifact detail fetch failed: ${res.status}`))
  return res.json()
}

// --- Taxonomy & Tags ---

export async function fetchTaxonomy(): Promise<TaxonomyResponse> {
  const res = await fetch(`${MCP_BASE}/taxonomy`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Taxonomy fetch failed: ${res.status}`))
  return res.json()
}

export async function createDomain(
  name: string,
  description = "",
  icon = "file",
  subCategories: string[] = ["general"],
): Promise<{ status: string; domain: string }> {
  const res = await fetch(`${MCP_BASE}/taxonomy/domain`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name, description, icon, sub_categories: subCategories }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Create domain failed: ${res.status}`))
  return res.json()
}

export async function createSubCategory(
  domain: string,
  name: string,
): Promise<{ status: string; domain: string; sub_category: string }> {
  const res = await fetch(`${MCP_BASE}/taxonomy/subcategory`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ domain, name }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Create sub-category failed: ${res.status}`))
  return res.json()
}

export async function recategorizeArtifact(
  artifactId: string,
  newDomain: string,
  subCategory = "",
  tags = "",
): Promise<{ status: string; artifact_id: string; old_domain: string; new_domain: string; chunks_moved: number }> {
  const res = await fetch(`${MCP_BASE}/recategorize`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ artifact_id: artifactId, new_domain: newDomain, sub_category: subCategory, tags }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Recategorize failed: ${res.status}`))
  return res.json()
}

export async function fetchTagSuggestions(
  domain?: string,
  prefix = "",
  limit = 30,
): Promise<TagSuggestion[]> {
  const params = new URLSearchParams()
  if (domain) params.set("domain", domain)
  if (prefix) params.set("prefix", prefix)
  params.set("limit", String(limit))
  const res = await fetch(`${MCP_BASE}/tags/suggest?${params}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Tag suggestions failed: ${res.status}`))
  return res.json()
}

export interface TagInfo {
  name: string
  usage_count: number
}

export async function fetchAllTags(limit = 500): Promise<TagInfo[]> {
  const res = await fetch(`${MCP_BASE}/tags?limit=${limit}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch tags"))
  return res.json()
}

export async function mergeTags(sourceTag: string, targetTag: string): Promise<{ status: string; artifacts_updated: number }> {
  const res = await fetch(`${MCP_BASE}/tags/merge`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ source_tag: sourceTag, target_tag: targetTag }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to merge tags"))
  return res.json()
}

export async function updateArtifactTags(artifactId: string, tags: string[]): Promise<{ status: string }> {
  const res = await fetch(`${MCP_BASE}/taxonomy/artifact`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ artifact_id: artifactId, tags }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to update tags"))
  return res.json()
}

// --- Monitoring & Audit ---

export async function fetchMaintenance(
  actions: string[] = ["health", "collections"],
  opts: { stale_days?: number; auto_purge?: boolean } = {},
): Promise<MaintenanceResponse> {
  const res = await fetch(`${MCP_BASE}/agent/maintain`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      actions,
      stale_days: opts.stale_days ?? 90,
      auto_purge: opts.auto_purge ?? false,
    }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Maintenance fetch failed: ${res.status}`))
  return res.json()
}

export async function fetchDigest(hours = 24): Promise<DigestResponse> {
  const res = await fetch(`${MCP_BASE}/digest?hours=${hours}`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Digest fetch failed: ${res.status}`))
  return res.json()
}

export async function fetchRectify(
  checks: string[] = ["duplicates", "stale", "orphans", "distribution"],
  opts: { auto_fix?: boolean; stale_days?: number } = {},
): Promise<RectifyResponse> {
  const res = await fetch(`${MCP_BASE}/agent/rectify`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      checks,
      auto_fix: opts.auto_fix ?? false,
      stale_days: opts.stale_days ?? 90,
    }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Rectify failed: ${res.status}`))
  return res.json()
}

export async function fetchCurate(
  domains?: string[],
  maxArtifacts = 200,
  generateSynopses = false,
  synopsisModel?: string,
): Promise<CurateResponse> {
  // Long-running operation: 120s for scoring only, 10min with synopsis generation
  const timeoutMs = generateSynopses ? 600_000 : 120_000
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(`${MCP_BASE}/agent/curate`, {
      method: "POST",
      headers: mcpHeaders({ "Content-Type": "application/json" }),
      signal: controller.signal,
      body: JSON.stringify({
        mode: "audit",
        domains: domains ?? null,
        max_artifacts: maxArtifacts,
        generate_synopses: generateSynopses,
        ...(synopsisModel && { synopsis_model: synopsisModel }),
      }),
    })
    if (!res.ok) throw new Error(await extractError(res, `Curate failed: ${res.status}`))
    return res.json()
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(`Quality audit timed out after ${timeoutMs / 1000}s — try reducing artifact count or disabling synopsis generation`)
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }
}

export async function fetchSynopsisEstimate(
  synopsisModel: string,
  domains?: string[],
  maxArtifacts = 200,
): Promise<SynopsisEstimate> {
  const res = await fetch(`${MCP_BASE}/agent/curate/estimate`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      synopsis_model: synopsisModel,
      domains: domains ?? null,
      max_artifacts: maxArtifacts,
    }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Synopsis estimate failed: ${res.status}`))
  return res.json()
}

export async function fetchSchedulerStatus(): Promise<SchedulerStatus> {
  const res = await fetch(`${MCP_BASE}/scheduler`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Scheduler status failed: ${res.status}`))
  return res.json()
}

export async function fetchIngestLog(limit = 100): Promise<IngestLogResponse> {
  const res = await fetch(`${MCP_BASE}/ingest_log?limit=${limit}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Ingest log failed: ${res.status}`))
  return res.json()
}

export async function fetchAudit(
  reports: string[] = ["activity", "ingestion", "costs", "queries", "verification"],
  hours = 24,
): Promise<AuditResponse> {
  const res = await fetch(`${MCP_BASE}/agent/audit`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ reports, hours }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Audit fetch failed: ${res.status}`))
  return res.json()
}

// --- File Upload ---

export async function uploadFile(
  file: File,
  opts: { domain?: string; subCategory?: string; tags?: string; categorizeMode?: string } = {},
): Promise<UploadResult> {
  const formData = new FormData()
  formData.append("file", file)
  const params = new URLSearchParams()
  if (opts.domain) params.set("domain", opts.domain)
  if (opts.subCategory) params.set("sub_category", opts.subCategory)
  if (opts.tags) params.set("tags", opts.tags)
  if (opts.categorizeMode) params.set("categorize_mode", opts.categorizeMode)

  const res = await fetch(`${MCP_BASE}/upload?${params}`, {
    method: "POST",
    headers: mcpHeaders(),
    body: formData,
  })
  if (!res.ok) throw new Error(await extractError(res, `Upload failed: ${res.status}`))
  return res.json()
}

// -- Sync API (Phase 21B) ----------------------------------------------------

export async function fetchSyncStatus(): Promise<import("../types").SyncStatus> {
  const res = await fetch(`${MCP_BASE}/sync/status`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch sync status"))
  return res.json()
}

export async function triggerSyncExport(options?: {
  since?: string
  domains?: string[]
}): Promise<import("../types").SyncExportResult> {
  const res = await fetch(`${MCP_BASE}/sync/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...mcpHeaders() },
    body: JSON.stringify(options ?? {}),
  })
  if (!res.ok) throw new Error(await extractError(res, "Sync export failed"))
  return res.json()
}

export async function triggerSyncImport(options?: {
  force?: boolean
  conflict_strategy?: string
}): Promise<import("../types").SyncImportResult> {
  const res = await fetch(`${MCP_BASE}/sync/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...mcpHeaders() },
    body: JSON.stringify(options ?? {}),
  })
  if (!res.ok) throw new Error(await extractError(res, "Sync import failed"))
  return res.json()
}

// -- Archive API (Phase 21D) — @internal: no UI consumer, tested in api.test.ts

/** @internal — no frontend consumer. Retained for test coverage. */
export interface ArchiveFile {
  filename: string
  domain: string
  size: number
  path: string
}

/** @internal — no frontend consumer. Retained for test coverage. */
export interface ArchiveFilesResponse {
  files: ArchiveFile[]
  total: number
  storage_mode: string
}

/** @internal — no frontend consumer. Retained for test coverage. */
export async function fetchArchiveFiles(domain?: string): Promise<ArchiveFilesResponse> {
  const params = domain ? `?domain=${encodeURIComponent(domain)}` : ""
  const res = await fetch(`${MCP_BASE}/archive/files${params}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to list archive files"))
  return res.json()
}

// ---------------------------------------------------------------------------
// KB Admin
// ---------------------------------------------------------------------------

export interface KBStats {
  total_artifacts: number
  total_chunks: number
  domains: Record<string, { artifacts: number; chunks: number; avg_quality: number; synopsis_candidates: number }>
}

export async function fetchKBStats(): Promise<KBStats> {
  const res = await fetch(`${MCP_BASE}/admin/kb/stats`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch KB stats"))
  return res.json()
}

export async function adminRebuildIndexes(): Promise<{ domains_rebuilt: number; message: string }> {
  const res = await fetch(`${MCP_BASE}/admin/kb/rebuild-index`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to rebuild indexes"))
  return res.json()
}

export async function adminRescore(domains?: string[]): Promise<{ artifacts_scored: number; avg_quality_score: number; message: string }> {
  const res = await fetch(`${MCP_BASE}/admin/kb/rescore`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(domains ? { domains } : {}),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to rescore"))
  return res.json()
}

export async function adminRegenerateSummaries(domains?: string[], force = true): Promise<{ synopses_generated: number; message: string }> {
  const res = await fetch(`${MCP_BASE}/admin/kb/regenerate-summaries`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ ...(domains ? { domains } : {}), force }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to regenerate summaries"))
  return res.json()
}

export async function adminClearDomain(domain: string): Promise<{ artifacts_deleted: number; message: string }> {
  const res = await fetch(`${MCP_BASE}/admin/kb/clear-domain/${encodeURIComponent(domain)}`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ confirm: true }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to clear domain"))
  return res.json()
}

export async function adminDeleteArtifact(artifactId: string): Promise<{ deleted: boolean; message: string }> {
  const res = await fetch(`${MCP_BASE}/admin/artifacts/${encodeURIComponent(artifactId)}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete artifact"))
  return res.json()
}

// --- KB Parser Capabilities ---

export async function fetchParserCapabilities(): Promise<{ capabilities: ParserCapability[]; tier: string }> {
  const res = await fetch(`${MCP_BASE}/admin/kb/capabilities`, { headers: mcpHeaders() })
  if (!res.ok) return { capabilities: [], tier: "community" }
  return res.json()
}

export async function reIngestArtifact(artifactId: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/admin/artifacts/${artifactId}/reingest`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Re-ingest failed"))
}

export async function fetchArtifactsFiltered(params: ArtifactFilterParams): Promise<Artifact[]> {
  const qs = new URLSearchParams()
  if (params.domain) qs.set("domain", params.domain)
  if (params.client_source) qs.set("client_source", params.client_source)
  if (params.since) qs.set("since", params.since)
  if (params.min_quality != null) qs.set("min_quality", String(params.min_quality))
  qs.set("limit", String(params.limit ?? 50))
  qs.set("offset", String(params.offset ?? 0))
  const res = await fetch(`${MCP_BASE}/artifacts?${qs}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch artifacts"))
  const raw = await res.json()
  const artifacts: Artifact[] = Array.isArray(raw) ? raw : []
  return artifacts.map((a) => ({ ...a, tags: parseTags(a.tags) }))
}

// ---------------------------------------------------------------------------
// Trading proxy (routed through MCP server's /api/trading/* proxy)
// ---------------------------------------------------------------------------

export async function fetchTradingAggregate(): Promise<Record<string, unknown>> {
  const res = await fetch(`${MCP_BASE}/api/trading/aggregate/portfolio`, {
    headers: mcpHeaders(),
  })
  return res.ok ? res.json() : {}
}

export async function fetchTradingSessions(): Promise<unknown[]> {
  const res = await fetch(`${MCP_BASE}/api/trading/sessions`, {
    headers: mcpHeaders(),
  })
  return res.ok ? res.json() : []
}
