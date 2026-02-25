// Chat types
export interface ChatMessage {
  id: string
  role: "user" | "assistant" | "system"
  content: string
  model?: string
  timestamp: number
}

export interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  model: string
  createdAt: number
  updatedAt: number
}

// Model types
export interface ModelOption {
  id: string
  label: string
  provider: string
  contextWindow: number
  inputCostPer1M: number   // USD per 1M input tokens
  outputCostPer1M: number  // USD per 1M output tokens
}

export const MODELS: ModelOption[] = [
  { id: "openrouter/anthropic/claude-sonnet-4", label: "Claude Sonnet", provider: "Anthropic", contextWindow: 200_000, inputCostPer1M: 3.0, outputCostPer1M: 15.0 },
  { id: "openrouter/openai/gpt-4o", label: "GPT-4o", provider: "OpenAI", contextWindow: 128_000, inputCostPer1M: 2.5, outputCostPer1M: 10.0 },
  { id: "openrouter/openai/gpt-4o-mini", label: "GPT-4o Mini", provider: "OpenAI", contextWindow: 128_000, inputCostPer1M: 0.15, outputCostPer1M: 0.6 },
  { id: "openrouter/google/gemini-2.5-flash", label: "Gemini Flash", provider: "Google", contextWindow: 1_000_000, inputCostPer1M: 0.15, outputCostPer1M: 0.6 },
  { id: "openrouter/x-ai/grok-4-fast", label: "Grok", provider: "xAI", contextWindow: 131_072, inputCostPer1M: 3.0, outputCostPer1M: 15.0 },
  { id: "openrouter/deepseek/deepseek-chat-v3-0324", label: "DeepSeek", provider: "DeepSeek", contextWindow: 128_000, inputCostPer1M: 0.27, outputCostPer1M: 1.10 },
  { id: "openrouter/meta-llama/llama-3.3-70b-instruct", label: "Llama 3.3", provider: "Meta", contextWindow: 131_072, inputCostPer1M: 0.12, outputCostPer1M: 0.3 },
]

// Health types
export interface HealthResponse {
  status: "healthy" | "degraded"
  services: {
    chromadb: "connected" | "error"
    redis: "connected" | "error"
    neo4j: "connected" | "error"
  }
}

// Theme
export type Theme = "dark" | "light"

// Domain types
export const DOMAINS = ["coding", "finance", "projects", "personal", "general", "conversations"] as const
export type Domain = (typeof DOMAINS)[number]

// Artifact types (GET /artifacts)
export interface Artifact {
  id: string
  filename: string
  domain: string
  keywords: string // JSON string array
  summary: string
  chunk_count: number
  chunk_ids: string // JSON string array
  ingested_at: string
  recategorized_at: string | null
}

// KB query result (POST /agent/query)
export interface KBQueryResult {
  content: string
  relevance: number
  artifact_id: string
  filename: string
  domain: string
  chunk_index: number
  collection: string
  ingested_at: string
  graph_source?: boolean
  relationship_type?: string
  cross_domain?: boolean
}

export interface AgentQueryResponse {
  query: string
  domains_queried: string[]
  total_results: number
  deduplicated_results: number
  results: KBQueryResult[]
  confidence: number
  reranking_used: boolean
  execution_time_ms: number
  timestamp: string
}

// Related artifacts (GET /artifacts/{id}/related)
export interface RelatedArtifact {
  id: string
  filename: string
  domain: string
  summary: string
  keywords: string
  relationship_type: string
  relationship_depth: number
  relationship_reason: string | null
}

// Collections (GET /collections)
export interface CollectionsResponse {
  total: number
  collections: string[]
}

// Maintenance / Health (POST /agent/maintain)
export interface MaintenanceHealth {
  overall: string
  services: Record<string, string>
  data: {
    collections: number
    total_chunks: number
    collection_sizes: Record<string, number>
    artifacts: number
    domains: number
    audit_log_entries: number
  }
}

export interface MaintenanceCollections {
  collections: Record<string, { chunks: number }>
  total_chunks: number
  empty_collections: string[]
  missing_collections: string[]
  extra_collections: string[]
  recommendations: string[]
}

export interface MaintenanceResponse {
  timestamp: string
  actions_run: string[]
  health?: MaintenanceHealth
  collections?: MaintenanceCollections
  stale_artifacts?: { id: string; filename: string; domain: string; ingested_at: string; chunk_count: number }[]
  orphan_cleanup?: { orphaned_chunks: number; cleaned: number }
}

// Scheduler (GET /scheduler)
export interface SchedulerStatus {
  status: "running" | "not_running"
  jobs: { id: string; name: string; next_run: string | null; trigger: string }[]
}

// Ingest log (GET /ingest_log)
export interface IngestLogEntry {
  event: string
  artifact_id: string
  domain: string
  filename: string
  timestamp: string
  [key: string]: unknown
}

export interface IngestLogResponse {
  total: number
  entries: IngestLogEntry[]
}

// Audit (POST /agent/audit)
export interface AuditActivity {
  time_window_hours: number
  total_events: number
  event_breakdown: Record<string, number>
  domain_breakdown: Record<string, number>
  hourly_timeline: Record<string, number>
  recent_failures: IngestLogEntry[]
  scanned_entries: number
}

export interface AuditIngestion {
  total_ingests: number
  total_duplicates: number
  duplicate_rate: number
  recategorizations: number
  domain_distribution: Record<string, number>
  file_type_distribution: Record<string, number>
  avg_chunks_per_file: number
}

export interface AuditCosts {
  time_window_hours: number
  operations: Record<string, number>
  estimated_tokens: Record<string, number>
  estimated_cost_usd: Record<string, number>
}

export interface AuditQueries {
  total_queries: number
  domain_frequency: Record<string, number>
  avg_results_per_query: number
}

export interface AuditResponse {
  timestamp: string
  reports_generated: string[]
  activity?: AuditActivity
  ingestion?: AuditIngestion
  costs?: AuditCosts
  queries?: AuditQueries
}
