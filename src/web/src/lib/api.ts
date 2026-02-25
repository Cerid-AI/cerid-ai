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
  SchedulerStatus,
  IngestLogResponse,
  AuditResponse,
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
): Promise<AgentQueryResponse> {
  const res = await fetch(`${MCP_BASE}/agent/query`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ query, domains: domains ?? null, top_k: topK, use_reranking: true }),
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
  return res.json()
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

// --- Monitoring & Audit ---

export async function fetchMaintenance(
  actions: string[] = ["health", "collections"],
): Promise<MaintenanceResponse> {
  const res = await fetch(`${MCP_BASE}/agent/maintain`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ actions, stale_days: 90, auto_purge: false }),
  })
  if (!res.ok) throw new Error(`Maintenance fetch failed: ${res.status}`)
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
): Promise<void> {
  const res = await fetch(`${MCP_BASE}/ingest/feedback`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      user_message: userMessage,
      assistant_response: assistantResponse,
      model,
      conversation_id: conversationId,
    }),
  })
  if (!res.ok) throw new Error(`Feedback ingest failed: ${res.status}`)
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
