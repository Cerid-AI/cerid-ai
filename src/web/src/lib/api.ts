// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Runtime config (window.__ENV__ from docker-entrypoint.sh) takes precedence
// over build-time Vite env vars, enabling config changes without rebuild.
const _env = (globalThis as Record<string, unknown>).__ENV__ as Record<string, string> | undefined
const MCP_BASE = _env?.VITE_MCP_URL || import.meta.env.VITE_MCP_URL || "http://localhost:8888"
const API_KEY = _env?.VITE_CERID_API_KEY || import.meta.env.VITE_CERID_API_KEY || ""

import { uuid } from "@/lib/utils"

function mcpHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extra }
  headers["X-Client-ID"] = "gui"
  if (API_KEY) headers["X-API-Key"] = API_KEY
  headers["X-Request-ID"] = uuid()
  // Add JWT Bearer token if authenticated (multi-user mode)
  try {
    const token = localStorage.getItem("cerid-access-token")
    if (token) headers["Authorization"] = `Bearer ${token}`
  } catch { /* noop */ }
  return headers
}

import { parseTags } from "@/lib/utils"

async function extractError(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json()
    return body.detail ?? fallback
  } catch {
    return fallback
  }
}

import type {
  HealthResponse,
  ChatMessage,
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
  HallucinationClaim,
  HallucinationReport,
  MemoryExtractionResult,
  ServerSettings,
  SettingsUpdate,
  Memory,
  UploadResult,
  SynopsisEstimate,
  TagSuggestion,
  ChatModelInfo,
  Conversation,
} from "./types"

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${MCP_BASE}/health`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Health check failed: ${res.status}`))
  return res.json()
}

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

export async function fetchArtifacts(domain?: string, limit = 50): Promise<Artifact[]> {
  const params = new URLSearchParams()
  if (domain) params.set("domain", domain)
  params.set("limit", String(limit))
  const res = await fetch(`${MCP_BASE}/artifacts?${params}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Artifacts fetch failed: ${res.status}`))
  const artifacts: Artifact[] = await res.json()
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

// --- Feedback Loop ---

export async function ingestFeedback(
  userMessage: string,
  assistantResponse: string,
  model: string,
  conversationId: string,
  inputTokens = 0,
  outputTokens = 0,
  latencyMs = 0,
): Promise<void> {
  const res = await fetch(`${MCP_BASE}/ingest/feedback`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      user_message: userMessage,
      assistant_response: assistantResponse,
      model,
      conversation_id: conversationId,
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      latency_ms: latencyMs,
    }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Feedback ingest failed: ${res.status}`))
}

// --- Hallucination Detection ---

export async function fetchHallucinationReport(
  conversationId: string,
): Promise<HallucinationReport | null> {
  // Try Redis cache first (fast, 30-day TTL)
  const res = await fetch(`${MCP_BASE}/agent/hallucination/${conversationId}`, {
    headers: mcpHeaders(),
  })
  if (res.ok) return res.json()

  // Fall back to Neo4j persistence (long-term storage)
  if (res.status === 404) {
    try {
      const neo4jRes = await fetch(`${MCP_BASE}/verification/${conversationId}`, {
        headers: mcpHeaders(),
      })
      if (neo4jRes.ok) return neo4jRes.json()
    } catch {
      // Neo4j fallback failed — not critical
    }
    return null
  }

  throw new Error(await extractError(res, `Hallucination report fetch failed: ${res.status}`))
}

export async function saveVerificationReport(report: {
  conversation_id: string
  claims: Array<Record<string, unknown>>
  overall_score: number
  verified: number
  unverified: number
  uncertain: number
  total: number
}): Promise<void> {
  const res = await fetch(`${MCP_BASE}/verification/save`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(report),
  })
  if (!res.ok) {
    console.warn("[verification] Failed to persist report:", res.status)
  }
}

export function streamVerification(
  responseText: string,
  conversationId: string,
  threshold?: number,
  model?: string,
  userQuery?: string,
  conversationHistory?: Array<{ role: string; content: string }>,
  expertMode?: boolean,
): { response: Promise<Response>; abort: () => void } {
  const controller = new AbortController()
  const response = fetch(`${MCP_BASE}/agent/verify-stream`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      response_text: responseText,
      conversation_id: conversationId,
      ...(threshold !== undefined && { threshold }),
      ...(model && { model }),
      ...(userQuery && { user_query: userQuery }),
      ...(conversationHistory?.length && { conversation_history: conversationHistory }),
      ...(expertMode && { expert_mode: true }),
    }),
    signal: controller.signal,
  })
  return { response, abort: () => controller.abort() }
}

export async function submitClaimFeedback(
  conversationId: string,
  claimIndex: number,
  correct: boolean,
): Promise<void> {
  const res = await fetch(`${MCP_BASE}/agent/hallucination/feedback`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      conversation_id: conversationId,
      claim_index: claimIndex,
      correct,
    }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Claim feedback failed: ${res.status}`))
}

/**
 * Re-verify a single claim with expert mode (Grok 4).
 * Sends the claim text as the response, reads the SSE stream for the
 * first `claim_verified` event, and returns the updated claim data.
 */
type ClaimVerificationResult = Pick<HallucinationClaim, "status" | "similarity" | "source_filename" | "source_artifact_id" | "source_domain" | "source_snippet" | "source_urls" | "reason" | "verification_method" | "verification_model" | "verification_answer">

export async function verifySingleClaim(
  claimText: string,
  conversationId: string,
): Promise<ClaimVerificationResult | null> {
  const res = await fetch(`${MCP_BASE}/agent/verify-stream`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      response_text: claimText,
      conversation_id: conversationId,
      expert_mode: true,
    }),
  })
  if (!res.ok || !res.body) return null

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split("\n")
      buffer = lines.pop() ?? ""

      for (const line of lines) {
        const trimmed = line.trim()
        if (trimmed.startsWith(":") || !trimmed.startsWith("data:")) continue
        const jsonStr = trimmed.slice(5).trim()
        if (!jsonStr) continue
        try {
          const event = JSON.parse(jsonStr)
          if (event.type === "claim_verified") {
            reader.cancel().catch(() => {})
            return {
              status: (event.status ?? "uncertain") as HallucinationClaim["status"],
              similarity: event.confidence ?? 0,
              source_filename: event.source || undefined,
              source_artifact_id: event.source_artifact_id || undefined,
              source_domain: event.source_domain || undefined,
              source_snippet: event.source_snippet || undefined,
              source_urls: event.source_urls || [],
              reason: event.reason || undefined,
              verification_method: (event.verification_method || undefined) as HallucinationClaim["verification_method"],
              verification_model: event.verification_model || undefined,
              verification_answer: event.verification_answer || undefined,
            }
          }
        } catch { /* skip malformed JSON */ }
      }
    }
  } catch { /* stream error */ }

  return null
}

// --- Settings ---

export async function fetchSettings(): Promise<ServerSettings> {
  const res = await fetch(`${MCP_BASE}/settings`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Settings fetch failed: ${res.status}`))
  return res.json()
}

export async function updateSettings(settings: SettingsUpdate): Promise<{ status: string; updated: Record<string, unknown> }> {
  const res = await fetch(`${MCP_BASE}/settings`, {
    method: "PATCH",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(settings),
  })
  if (!res.ok) throw new Error(await extractError(res, `Settings update failed: ${res.status}`))
  return res.json()
}

// --- Memory Extraction ---

export async function extractMemories(
  responseText: string,
  conversationId: string,
  model = "",
): Promise<MemoryExtractionResult> {
  const res = await fetch(`${MCP_BASE}/agent/memory/extract`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      response_text: responseText,
      conversation_id: conversationId,
      model,
    }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Memory extraction failed: ${res.status}`))
  return res.json()
}

export async function archiveMemories(
  retentionDays = 180,
): Promise<{ archived: number; remaining: number }> {
  const res = await fetch(`${MCP_BASE}/agent/memory/archive`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ retention_days: retentionDays }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Memory archive failed: ${res.status}`))
  return res.json()
}

// --- Memories ---

export async function fetchMemories(
  opts: { type?: string; conversationId?: string; limit?: number; offset?: number } = {},
): Promise<{ memories: Memory[]; total: number }> {
  const params = new URLSearchParams()
  if (opts.type) params.set("type", opts.type)
  if (opts.conversationId) params.set("conversation_id", opts.conversationId)
  if (opts.limit !== undefined) params.set("limit", String(opts.limit))
  if (opts.offset !== undefined) params.set("offset", String(opts.offset))
  const res = await fetch(`${MCP_BASE}/memories?${params}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Memories fetch failed: ${res.status}`))
  return res.json()
}

export async function updateMemory(memoryId: string, summary: string): Promise<Memory> {
  const res = await fetch(`${MCP_BASE}/memories/${memoryId}`, {
    method: "PATCH",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ summary }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Memory update failed: ${res.status}`))
  return res.json()
}

export async function deleteMemory(memoryId: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/memories/${memoryId}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Memory delete failed: ${res.status}`))
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

// --- Chat ---

export async function streamChat(
  messages: Pick<ChatMessage, "role" | "content">[],
  model: string,
  onChunk: (text: string) => void,
  signal?: AbortSignal,
  onModelInfo?: (info: ChatModelInfo) => void,
): Promise<void> {
  const url = `${MCP_BASE}/chat/stream`
  const res = await fetch(url, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      model,
      messages: messages.map((m) => ({ role: m.role, content: m.content })),
      temperature: 0.7,
      stream: true,
    }),
    signal,
  })

  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Chat request failed (${res.status}): ${text}`)
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error("No response body")

  const decoder = new TextDecoder()
  let buffer = ""
  let lastModelInfo: ChatModelInfo | undefined

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split("\n")
      buffer = lines.pop() ?? ""

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed || !trimmed.startsWith("data: ")) continue
        const data = trimmed.slice(6)
        if (data === "[DONE]") return

        try {
          const parsed = JSON.parse(data)
          if (parsed.cerid_meta) {
            lastModelInfo = parsed.cerid_meta as ChatModelInfo
            onModelInfo?.(lastModelInfo)
            continue
          }
          // OpenRouter may substitute a different model — update if so
          if (parsed.cerid_meta_update) {
            if (parsed.cerid_meta_update.actual_model) {
              onModelInfo?.({ ...lastModelInfo!, actual_model: parsed.cerid_meta_update.actual_model })
            }
            if (parsed.cerid_meta_update.fallback_model) {
              onModelInfo?.({
                ...lastModelInfo!,
                resolved_model: parsed.cerid_meta_update.fallback_model,
                fallback_model: parsed.cerid_meta_update.fallback_model,
                original_error: parsed.cerid_meta_update.original_error,
              })
              console.warn(
                `[chat] Model fallback: original failed (${parsed.cerid_meta_update.original_error}), using ${parsed.cerid_meta_update.fallback_model}`,
              )
            }
            continue
          }
          if (parsed.error) {
            throw new Error(parsed.error.message || "Upstream error")
          }
          const content = parsed.choices?.[0]?.delta?.content
          if (content) onChunk(content)
        } catch (e) {
          if (e instanceof SyntaxError) {
            console.warn("[streamChat] malformed SSE chunk:", data)
          } else {
            throw e
          }
        }
      }
    }

    // Flush any remaining data in the buffer after stream ends
    if (buffer.trim()) {
      const trimmed = buffer.trim()
      if (trimmed.startsWith("data: ")) {
        const data = trimmed.slice(6)
        if (data !== "[DONE]") {
          try {
            const parsed = JSON.parse(data)
            if (!parsed.cerid_meta && !parsed.error) {
              const content = parsed.choices?.[0]?.delta?.content
              if (content) onChunk(content)
            }
          } catch { /* malformed trailing chunk */ }
        }
      }
    }
  } finally {
    reader.cancel()
  }
}

/**
 * Summarize conversation history using the current model via Bifrost.
 * The summary preserves key facts, decisions, code, and action items.
 */
export async function summarizeConversation(
  messages: Pick<ChatMessage, "role" | "content">[],
  model: string,
  signal?: AbortSignal,
): Promise<string> {
  const conversationText = messages
    .map((m) => `${m.role === "user" ? "User" : m.role === "assistant" ? "Assistant" : "System"}: ${m.content}`)
    .join("\n\n")

  const summaryMessages: Pick<ChatMessage, "role" | "content">[] = [
    {
      role: "system",
      content:
        "You are a conversation summarizer. Produce a concise summary of the conversation below. " +
        "Preserve all key facts, decisions, code snippets, and action items. " +
        "The summary will be used as context for a new model, so include everything needed to continue the conversation seamlessly. " +
        "Do not add commentary.",
    },
    {
      role: "user",
      content: `Summarize this conversation:\n\n${conversationText}`,
    },
  ]

  let summary = ""
  await streamChat(summaryMessages, model, (chunk) => {
    summary += chunk
  }, signal)

  return summary
}

/**
 * Compress conversation history to fit a target token budget via backend LLM.
 * Returns compressed messages and token counts.
 */
export async function compressConversation(
  messages: Pick<ChatMessage, "role" | "content">[],
  targetTokens: number,
): Promise<{ messages: { role: string; content: string }[]; original_tokens: number; compressed_tokens: number }> {
  const res = await fetch(`${MCP_BASE}/chat/compress`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ messages, target_tokens: targetTokens }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Compress failed: ${res.status}`))
  return res.json()
}

// -- Sync API (Phase 21B) ----------------------------------------------------

export async function fetchSyncStatus(): Promise<import("./types").SyncStatus> {
  const res = await fetch(`${MCP_BASE}/sync/status`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch sync status"))
  return res.json()
}

export async function triggerSyncExport(options?: {
  since?: string
  domains?: string[]
}): Promise<import("./types").SyncExportResult> {
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
}): Promise<import("./types").SyncImportResult> {
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

// -- Auth API (Phase 31 — multi-user) ----------------------------------------

import type { AuthTokens, AuthUser, UsageInfo } from "./types"

export async function authRegister(
  email: string,
  password: string,
  displayName = "",
  tenantName = "",
): Promise<AuthTokens> {
  const res = await fetch(`${MCP_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, display_name: displayName, tenant_name: tenantName }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Registration failed: ${res.status}`))
  return res.json()
}

export async function authLogin(email: string, password: string): Promise<AuthTokens> {
  const res = await fetch(`${MCP_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Login failed: ${res.status}`))
  return res.json()
}

export async function authRefresh(refreshToken: string): Promise<{ access_token: string }> {
  const res = await fetch(`${MCP_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Token refresh failed"))
  return res.json()
}

export async function authLogout(refreshToken: string): Promise<void> {
  await fetch(`${MCP_BASE}/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
}

export async function authMe(): Promise<AuthUser> {
  const res = await fetch(`${MCP_BASE}/auth/me`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Not authenticated"))
  return res.json()
}

export async function authSetApiKey(apiKey: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/auth/me/api-key`, {
    method: "PUT",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ api_key: apiKey }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to save API key"))
}

export async function authDeleteApiKey(): Promise<void> {
  const res = await fetch(`${MCP_BASE}/auth/me/api-key`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to remove API key"))
}

export async function authApiKeyStatus(): Promise<{ has_key: boolean }> {
  const res = await fetch(`${MCP_BASE}/auth/me/api-key/status`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to check API key status"))
  return res.json()
}

export async function authUsage(): Promise<UsageInfo> {
  const res = await fetch(`${MCP_BASE}/auth/me/usage`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch usage"))
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

// ── User State Sync ─────────────────────────────────────────────────────────

export async function fetchUserState(): Promise<{
  settings: Record<string, unknown>
  preferences: Record<string, unknown>
  conversation_ids: string[]
}> {
  const res = await fetch(`${MCP_BASE}/user-state`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch user state"))
  return res.json()
}

export async function fetchSyncedConversations(): Promise<Conversation[]> {
  const res = await fetch(`${MCP_BASE}/user-state/conversations`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch conversations"))
  const data = await res.json()
  return data.conversations ?? []
}

export async function syncConversation(conversation: Conversation): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/conversations`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(conversation),
  })
}

export async function syncConversationsBulk(conversations: Conversation[]): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/conversations/bulk`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(conversations),
  })
}

export async function deleteConversationSync(convId: string): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/conversations/${convId}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
}

export async function syncPreferences(prefs: Record<string, unknown>): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/preferences`, {
    method: "PATCH",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(prefs),
  })
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