// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const MCP_BASE = import.meta.env.VITE_MCP_URL ?? "http://localhost:8888"
const BIFROST_BASE = import.meta.env.VITE_BIFROST_URL ?? "/api/bifrost"
const API_KEY = import.meta.env.VITE_CERID_API_KEY ?? ""

function mcpHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extra }
  if (API_KEY) headers["X-API-Key"] = API_KEY
  return headers
}

import type {
  HealthResponse,
  ChatMessage,
  AgentQueryResponse,
  Artifact,
  RelatedArtifact,
  CollectionsResponse,
  MaintenanceResponse,
  RectifyResponse,
  CurateResponse,
  TaxonomyResponse,
  TagInfo,
  SchedulerStatus,
  IngestLogResponse,
  AuditResponse,
  HallucinationReport,
  MemoryExtractionResult,
  ServerSettings,
  SettingsUpdate,
  Memory,
  UploadResult,
} from "./types"

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${MCP_BASE}/health`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`)
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
  if (!res.ok) throw new Error(`KB query failed: ${res.status}`)
  return res.json()
}

export async function fetchArtifacts(domain?: string, limit = 50): Promise<Artifact[]> {
  const params = new URLSearchParams()
  if (domain) params.set("domain", domain)
  params.set("limit", String(limit))
  const res = await fetch(`${MCP_BASE}/artifacts?${params}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Artifacts fetch failed: ${res.status}`)
  const artifacts: Artifact[] = await res.json()
  return artifacts.map((a) => ({
    ...a,
    tags: Array.isArray(a.tags) ? a.tags : typeof a.tags === "string" ? (() => { try { return JSON.parse(a.tags) } catch { return [] } })() : undefined,
  }))
}

export async function fetchRelatedArtifacts(
  artifactId: string,
  depth = 2,
  maxResults = 5,
): Promise<RelatedArtifact[]> {
  const params = new URLSearchParams({ depth: String(depth), max_results: String(maxResults) })
  const res = await fetch(`${MCP_BASE}/artifacts/${artifactId}/related?${params}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Related artifacts fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchCollections(): Promise<CollectionsResponse> {
  const res = await fetch(`${MCP_BASE}/collections`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Collections fetch failed: ${res.status}`)
  return res.json()
}

// --- Taxonomy & Tags ---

export async function fetchTaxonomy(): Promise<TaxonomyResponse> {
  const res = await fetch(`${MCP_BASE}/taxonomy`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Taxonomy fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchTags(limit = 200): Promise<TagInfo[]> {
  const res = await fetch(`${MCP_BASE}/tags?limit=${limit}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Tags fetch failed: ${res.status}`)
  return res.json()
}

export async function updateArtifactTaxonomy(
  artifactId: string,
  opts: { sub_category?: string; tags?: string[] },
): Promise<unknown> {
  const res = await fetch(`${MCP_BASE}/taxonomy/artifact`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ artifact_id: artifactId, ...opts }),
  })
  if (!res.ok) throw new Error(`Taxonomy update failed: ${res.status}`)
  return res.json()
}

export async function mergeTags(
  sourceTag: string,
  targetTag: string,
): Promise<{ status: string; source: string; target: string; artifacts_updated: number }> {
  const res = await fetch(`${MCP_BASE}/tags/merge`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ source_tag: sourceTag, target_tag: targetTag }),
  })
  if (!res.ok) throw new Error(`Tag merge failed: ${res.status}`)
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
  if (!res.ok) throw new Error(`Maintenance fetch failed: ${res.status}`)
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
  if (!res.ok) throw new Error(`Rectify failed: ${res.status}`)
  return res.json()
}

export async function fetchCurate(
  domains?: string[],
  maxArtifacts = 200,
): Promise<CurateResponse> {
  const res = await fetch(`${MCP_BASE}/agent/curate`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      mode: "audit",
      domains: domains ?? null,
      max_artifacts: maxArtifacts,
    }),
  })
  if (!res.ok) throw new Error(`Curate failed: ${res.status}`)
  return res.json()
}

export async function fetchSchedulerStatus(): Promise<SchedulerStatus> {
  const res = await fetch(`${MCP_BASE}/scheduler`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Scheduler status failed: ${res.status}`)
  return res.json()
}

export async function fetchIngestLog(limit = 100): Promise<IngestLogResponse> {
  const res = await fetch(`${MCP_BASE}/ingest_log?limit=${limit}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Ingest log failed: ${res.status}`)
  return res.json()
}

export async function fetchAudit(
  reports: string[] = ["activity", "ingestion", "costs", "queries"],
  hours = 24,
): Promise<AuditResponse> {
  const res = await fetch(`${MCP_BASE}/agent/audit`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ reports, hours }),
  })
  if (!res.ok) throw new Error(`Audit fetch failed: ${res.status}`)
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
  if (!res.ok) throw new Error(`Feedback ingest failed: ${res.status}`)
}

// --- Hallucination Detection ---

export async function checkHallucinations(
  responseText: string,
  conversationId: string,
  threshold?: number,
): Promise<HallucinationReport> {
  const res = await fetch(`${MCP_BASE}/agent/hallucination`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      response_text: responseText,
      conversation_id: conversationId,
      ...(threshold !== undefined && { threshold }),
    }),
  })
  if (!res.ok) throw new Error(`Hallucination check failed: ${res.status}`)
  return res.json()
}

export async function fetchHallucinationReport(
  conversationId: string,
): Promise<HallucinationReport | null> {
  const res = await fetch(`${MCP_BASE}/agent/hallucination/${conversationId}`, {
    headers: mcpHeaders(),
  })
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`Hallucination report fetch failed: ${res.status}`)
  return res.json()
}

// --- Settings ---

export async function fetchSettings(): Promise<ServerSettings> {
  const res = await fetch(`${MCP_BASE}/settings`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Settings fetch failed: ${res.status}`)
  return res.json()
}

export async function updateSettings(settings: SettingsUpdate): Promise<{ status: string; updated: Record<string, unknown> }> {
  const res = await fetch(`${MCP_BASE}/settings`, {
    method: "PATCH",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(settings),
  })
  if (!res.ok) throw new Error(`Settings update failed: ${res.status}`)
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
  if (!res.ok) throw new Error(`Memory extraction failed: ${res.status}`)
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
  if (!res.ok) throw new Error(`Memories fetch failed: ${res.status}`)
  return res.json()
}

export async function updateMemory(memoryId: string, summary: string): Promise<Memory> {
  const res = await fetch(`${MCP_BASE}/memories/${memoryId}`, {
    method: "PATCH",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ summary }),
  })
  if (!res.ok) throw new Error(`Memory update failed: ${res.status}`)
  return res.json()
}

export async function deleteMemory(memoryId: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/memories/${memoryId}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(`Memory delete failed: ${res.status}`)
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
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Upload failed: ${res.status}` }))
    throw new Error(err.detail || `Upload failed: ${res.status}`)
  }
  return res.json()
}

export async function fetchSupportedExtensions(): Promise<{ extensions: string[]; count: number }> {
  const res = await fetch(`${MCP_BASE}/upload/supported`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`Supported extensions fetch failed: ${res.status}`)
  return res.json()
}

// --- Chat ---

export async function streamChat(
  messages: Pick<ChatMessage, "role" | "content">[],
  model: string,
  onChunk: (text: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const url = `${BIFROST_BASE}/v1/chat/completions`
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
          const content = parsed.choices?.[0]?.delta?.content
          if (content) onChunk(content)
        } catch {
          console.warn("[streamChat] malformed SSE chunk:", data)
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
            const content = parsed.choices?.[0]?.delta?.content
            if (content) onChunk(content)
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