// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export interface ChatMessage {
  id: string
  role: "user" | "assistant" | "system"
  content: string
  model?: string
  timestamp: number
  sourcesUsed?: SourceRef[]
}

export interface SourceRef {
  artifact_id: string
  filename: string
  domain: string
  sub_category?: string
  relevance: number
  chunk_index: number
  tags?: string[]
}

export interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  model: string
  createdAt: number
  updatedAt: number
}

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

export const PROVIDER_COLORS: Record<string, string> = {
  Anthropic: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  OpenAI: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
  Google: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  xAI: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
  DeepSeek: "bg-cyan-500/15 text-cyan-700 dark:text-cyan-400",
  Meta: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-400",
}

export function findModel(modelId: string): ModelOption | undefined {
  return MODELS.find((m) => m.id === modelId)
}

export interface HealthResponse {
  status: "healthy" | "degraded"
  services: {
    chromadb: "connected" | "error"
    redis: "connected" | "error"
    neo4j: "connected" | "error"
  }
}

export type Theme = "dark" | "light"

export const DOMAINS = ["coding", "finance", "projects", "personal", "general", "conversations"] as const
export type Domain = (typeof DOMAINS)[number]

export interface TaxonomyDomain {
  description: string
  icon: string
  sub_categories: string[]
}

export interface TaxonomyResponse {
  domains: Record<string, TaxonomyDomain>
}

export interface TagInfo {
  name: string
  count: number
}

export interface Artifact {
  id: string
  filename: string
  domain: string
  sub_category?: string
  tags?: string[]
  keywords: string // JSON string array
  summary: string
  chunk_count: number
  chunk_ids: string // JSON string array
  ingested_at: string
  recategorized_at: string | null
}

export interface KBQueryResult {
  content: string
  relevance: number
  artifact_id: string
  filename: string
  domain: string
  sub_category?: string
  tags?: string[]
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

export interface CollectionsResponse {
  total: number
  collections: string[]
}

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

export interface RectifyResponse {
  timestamp: string
  checks_run: string[]
  auto_fix: boolean
  findings: {
    duplicates?: { count: number; details: { content_hash: string; artifacts: { id: string; filename: string; domain: string }[] }[] }
    stale?: { count: number; threshold_days: number; artifacts: { id: string; filename: string; domain: string; ingested_at: string; chunk_count: number }[] }
    orphans?: { count: number; by_domain: Record<string, number> }
    distribution?: { distribution: Record<string, { artifacts: number; chunks: number }>; total_artifacts: number; total_chunks: number; domain_count: number }
  }
  actions: { type: string; [key: string]: unknown }[]
}

export interface SchedulerStatus {
  status: "running" | "not_running"
  jobs: { id: string; name: string; next_run: string | null; trigger: string }[]
}

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

export interface AuditConversations {
  total_conversations: number
  total_turns: number
  models: Record<string, {
    turns: number
    input_tokens: number
    output_tokens: number
    avg_latency_ms: number
    cost_usd: number
  }>
  total_cost_usd: number
}

export interface AuditResponse {
  timestamp: string
  reports_generated: string[]
  activity?: AuditActivity
  ingestion?: AuditIngestion
  costs?: AuditCosts
  queries?: AuditQueries
  conversations?: AuditConversations
}

export interface HallucinationClaim {
  claim: string
  status: "verified" | "unverified" | "uncertain" | "error"
  similarity: number
  source_artifact_id?: string
  source_filename?: string
  source_domain?: string
  source_snippet?: string
  reason?: string
}

export interface HallucinationReport {
  conversation_id: string
  timestamp: string
  skipped: boolean
  threshold?: number
  claims: HallucinationClaim[]
  summary: {
    total: number
    verified: number
    unverified: number
    uncertain: number
    error?: number
  }
  reason?: string
}

export interface MemoryExtractionResult {
  conversation_id: string
  timestamp: string
  memories_extracted: number
  memories_stored: number
  results: {
    memory_type: string
    summary: string
    status: string
    artifact_id?: string
    error?: string
  }[]
}

export interface ModelRecommendation {
  model: ModelOption
  estimatedCost: number
  reasoning: string
  savingsVsCurrent: number
}

export type SwitchStrategy = "continue" | "summarize" | "fresh"

export interface SwitchCostEstimate {
  replayCost: number           // Cost to replay full history on target model
  currentNextTurnCost: number  // Cost of next turn on current model (comparison)
  summarizeCost: number        // Cost to summarize + switch
  historyTokens: number        // Token count of full history
  summarizedTokens: number     // Estimated tokens after summarization
  exceedsTargetContext: boolean // True if history > 80% of target's window
}

export interface ModelSwitchOptions {
  targetModel: ModelOption
  costEstimate: SwitchCostEstimate
  strategies: SwitchStrategy[]
  recommended: SwitchStrategy
}

export interface ServerSettings {
  categorize_mode: string
  chunk_max_tokens: number
  chunk_overlap: number
  cost_sensitivity: string
  enable_encryption: boolean
  enable_feedback_loop: boolean
  enable_hallucination_check: boolean
  enable_memory_extraction: boolean
  hallucination_threshold: number
  enable_auto_inject: boolean
  auto_inject_threshold: number
  feature_tier: string
  feature_flags: Record<string, boolean>
  domains: string[]
  taxonomy: Record<string, {
    description: string
    icon: string
    sub_categories: string[]
  }>
  sync_backend: string
  machine_id: string
  version: string
}

export interface SettingsUpdate {
  categorize_mode?: string
  enable_feedback_loop?: boolean
  enable_hallucination_check?: boolean
  enable_memory_extraction?: boolean
  hallucination_threshold?: number
  cost_sensitivity?: string
  enable_auto_inject?: boolean
  auto_inject_threshold?: number
}

export interface Memory {
  id: string
  type: string // facts, decisions, preferences, action-items
  content: string
  conversation_id: string
  created_at: string
  source_filename: string
}

export interface UploadResult {
  status: string
  artifact_id: string
  domain: string
  filename: string
  chunks: number
  categorize_mode: string
  metadata?: Record<string, string>
}

export interface LiveMetrics {
  inputTokens: number
  outputTokens: number
  contextPct: number
  sessionCost: number
  messageCost: number
  messagesCount: number
}

