// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export interface ChatMessage {
  id: string
  role: "user" | "assistant" | "system"
  content: string
  model?: string
  timestamp: number
  sourcesUsed?: SourceRef[]
  /** Non-empty when retrieval breached its time budget and the answer is ungrounded.
   * Populated from the backend `AgentQueryResponse.degraded_reason` field. */
  degradedReason?: string
}

export type RagMode = "manual" | "smart" | "custom_smart"

/** Absolute on/off gates for each context source. Disabled sources skip retrieval entirely. */
export interface ContextSources {
  kb: boolean
  memory: boolean
  external: boolean
}

export interface SourceRef {
  artifact_id: string
  filename: string
  domain: string
  sub_category?: string
  relevance: number
  chunk_index: number
  tags?: string[]
  quality_score?: number
  source_type?: "kb" | "memory" | "external"
  /** Populated for external-source items (e.g. Wikipedia, DuckDuckGo) so the
   *  sources pane can render a link to the origin page. Empty/undefined for
   *  KB and memory items. */
  source_url?: string
}

export interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  model: string
  createdAt: number
  updatedAt: number
  archived?: boolean
  /** Per-message verification reports — keyed by assistant message ID. */
  verificationReports?: Record<string, HallucinationReport>
}

export interface ModelCapabilities {
  reasoning: number     // 0-100: logical reasoning, math, analysis
  coding: number        // 0-100: code generation, debugging, review
  creative: number      // 0-100: writing, brainstorming, storytelling
  factual: number       // 0-100: knowledge accuracy, recall
  webSearch: boolean    // native web search (e.g. Grok :online)
  vision: boolean       // image understanding
  knowledgeCutoff: string  // YYYY-MM approximate training data cutoff
}

export interface ModelOption {
  id: string
  label: string
  provider: string
  contextWindow: number
  effectiveContextWindow: number  // 80% of advertised (accounts for output + safety margin)
  maxOutputTokens: number
  inputCostPer1M: number   // USD per 1M input tokens
  outputCostPer1M: number  // USD per 1M output tokens
  capabilities?: ModelCapabilities
}

export const MODELS: ModelOption[] = [
  // --- Tier S: Frontier models ---
  { id: "openrouter/anthropic/claude-sonnet-4.6", label: "Claude Sonnet 4.6", provider: "Anthropic", contextWindow: 1_000_000, effectiveContextWindow: 800_000, maxOutputTokens: 64_000, inputCostPer1M: 3.0, outputCostPer1M: 15.0,
    capabilities: { reasoning: 90, coding: 95, creative: 85, factual: 88, webSearch: false, vision: true, knowledgeCutoff: "2026-01" } },
  { id: "openrouter/anthropic/claude-opus-4.6", label: "Claude Opus 4.6", provider: "Anthropic", contextWindow: 1_000_000, effectiveContextWindow: 800_000, maxOutputTokens: 32_000, inputCostPer1M: 5.0, outputCostPer1M: 25.0,
    capabilities: { reasoning: 95, coding: 93, creative: 92, factual: 93, webSearch: false, vision: true, knowledgeCutoff: "2026-01" } },
  { id: "openrouter/x-ai/grok-4.1-fast", label: "Grok 4.1", provider: "xAI", contextWindow: 2_000_000, effectiveContextWindow: 1_600_000, maxOutputTokens: 100_000, inputCostPer1M: 0.20, outputCostPer1M: 0.50,
    capabilities: { reasoning: 88, coding: 82, creative: 78, factual: 90, webSearch: true, vision: true, knowledgeCutoff: "2026-03" } },
  // --- Tier A: Strong general-purpose ---
  { id: "openrouter/openai/o3-mini", label: "o3-mini", provider: "OpenAI", contextWindow: 200_000, effectiveContextWindow: 160_000, maxOutputTokens: 100_000, inputCostPer1M: 1.10, outputCostPer1M: 4.40,
    capabilities: { reasoning: 92, coding: 85, creative: 65, factual: 80, webSearch: false, vision: false, knowledgeCutoff: "2025-10" } },
  { id: "openrouter/google/gemini-3-flash-preview", label: "Gemini 3 Flash", provider: "Google", contextWindow: 1_048_576, effectiveContextWindow: 838_860, maxOutputTokens: 65_536, inputCostPer1M: 0.50, outputCostPer1M: 3.0,
    capabilities: { reasoning: 82, coding: 85, creative: 78, factual: 84, webSearch: false, vision: true, knowledgeCutoff: "2025-11" } },
  // --- Tier B: Budget / legacy ---
  { id: "openrouter/openai/gpt-4o-mini", label: "GPT-4o Mini", provider: "OpenAI", contextWindow: 128_000, effectiveContextWindow: 102_400, maxOutputTokens: 16_384, inputCostPer1M: 0.15, outputCostPer1M: 0.60,
    capabilities: { reasoning: 70, coding: 72, creative: 75, factual: 78, webSearch: false, vision: true, knowledgeCutoff: "2024-10" } },
  { id: "openrouter/google/gemini-2.5-flash", label: "Gemini 2.5 Flash", provider: "Google", contextWindow: 1_048_576, effectiveContextWindow: 838_860, maxOutputTokens: 65_536, inputCostPer1M: 0.30, outputCostPer1M: 2.50,
    capabilities: { reasoning: 78, coding: 80, creative: 75, factual: 82, webSearch: false, vision: true, knowledgeCutoff: "2025-06" } },
  { id: "openrouter/meta-llama/llama-3.3-70b-instruct", label: "Llama 3.3", provider: "Meta", contextWindow: 131_072, effectiveContextWindow: 104_857, maxOutputTokens: 8_192, inputCostPer1M: 0.10, outputCostPer1M: 0.32,
    capabilities: { reasoning: 75, coding: 78, creative: 72, factual: 75, webSearch: false, vision: false, knowledgeCutoff: "2024-12" } },
]

export const PROVIDER_COLORS: Record<string, string> = {
  Anthropic: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  OpenAI: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
  Google: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  xAI: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
  Meta: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-400",
}

export function findModel(modelId: string): ModelOption | undefined {
  return MODELS.find((m) => m.id === modelId)
}

export interface ArtifactDetail {
  artifact_id: string
  title: string
  domain: string
  filename: string
  source_type: string
  chunk_count: number
  total_content: string
  chunks: { index: number; text: string }[]
  metadata: Record<string, unknown>
}

export interface HealthResponse {
  status: "healthy" | "degraded"
  /** Backend semver (e.g. "0.83.0") — surfaced in the sidebar footer. */
  version?: string
  services: {
    chromadb: "connected" | "error"
    redis: "connected" | "error"
    neo4j: "connected" | "error"
  }
  openrouter_credits_exhausted?: boolean
  openrouter_auth_ok?: boolean | null
  circuit_breakers?: Record<string, string>
}

// ---------------------------------------------------------------------------
// Degradation tiers, pipeline routing, structured errors
// ---------------------------------------------------------------------------

export type DegradationTier = "full" | "lite" | "direct" | "cached" | "offline"

export type PipelineStage =
  | "claim_extraction"
  | "query_decomposition"
  | "topic_extraction"
  | "memory_resolution"
  | "verification_simple"
  | "verification_complex"
  | "reranking"
  | "chat_generation"

export type PipelineProviders = Partial<Record<PipelineStage, "ollama" | "bifrost">>

/** Inference provider status from tiered detection system. */
export type InferenceTier = "optimal" | "good" | "degraded" | "unknown"

export interface InferenceStatus {
  provider: string
  tier: InferenceTier
  gpu: boolean
  gpu_name: string
  platform: string
  onnx_providers: string[]
  ollama_available: boolean
  sidecar_available: boolean
  embed_latency_ms: number
  rerank_latency_ms: number
  message: string
}

/** Extended health from GET /health/status — includes degradation + pipeline routing. */
export interface HealthStatusResponse extends HealthResponse {
  degradation_tier?: DegradationTier
  can_retrieve?: boolean
  can_verify?: boolean
  can_generate?: boolean
  feature_tier?: string
  pipeline_providers?: PipelineProviders
  internal_llm_provider?: string
  internal_llm_model?: string
  inference?: InferenceStatus
}

/** Structured error response from CeridError hierarchy. */
export interface CeridErrorResponse {
  error_code: string
  message: string
  details: Record<string, unknown> | null
}

/** Metamorphic verification score (Pro tier). */
export interface MetamorphicScore {
  score: number
  factoid_count: number
  suspicious_count: number
  details: Array<{
    factoid: string
    synonym_entailed: boolean
    antonym_entailed: boolean
    status: "ok" | "suspicious" | "likely_hallucinated"
  }>
  skipped?: boolean
}

export interface OpenRouterCredits {
  available: boolean
  credits_remaining?: number | null
  credits_used?: number | null
  is_free_tier?: boolean
  top_up_url?: string
  warning?: string
  error?: string
}

export interface ProviderCredits {
  configured: boolean
  provider?: string
  balance?: number
  total_credits?: number
  total_usage?: number
  usage_daily?: number
  usage_weekly?: number
  usage_monthly?: number
  is_free_tier?: boolean
  status?: "ok" | "low" | "exhausted" | "error"
  warning?: string
  top_up_url?: string
  account_url?: string
  signup_url?: string
  error?: string
}

export type Theme = "dark" | "light"

export const DOMAINS = ["coding", "finance", "projects", "personal", "general", "conversations"] as const

export interface TaxonomySubCategory {
  name: string
  artifact_count: number
}

export interface TaxonomyDomain {
  description: string
  icon: string
  sub_categories: TaxonomySubCategory[]
  artifact_count: number
}

export interface TaxonomyResponse {
  domains: Record<string, TaxonomyDomain>
}

export interface TagSuggestion {
  name: string
  source: "vocabulary" | "existing"
  usage_count: number
}

export type ArtifactSourceType =
  | "upload"
  | "watcher"
  | "webhook"
  | "email"
  | "rss"
  | "bookmark"
  | "clipboard"

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
  quality_score?: number
  source_type?: ArtifactSourceType
  client_source?: string
  total_tokens?: number
}

// --- Ingestion progress ---

export type IngestionStep = "parsing" | "chunking" | "embedding" | "indexing"

export interface IngestionFileProgress {
  filename: string
  step: IngestionStep
  progress: number // 0-100
  status: "pending" | "processing" | "done" | "error"
  error?: string
}

export interface IngestionProgress {
  files: IngestionFileProgress[]
  total_files: number
  completed_files: number
}

// --- Near-duplicate detection ---

export interface DuplicateArtifact {
  id: string
  filename: string
  domain: string
  summary: string
  quality_score?: number
  ingested_at: string
  chunk_count: number
}

export interface DuplicateGroup {
  content_hash_prefix: string
  similarity: number
  artifacts: DuplicateArtifact[]
}

export interface DuplicatesResponse {
  groups: DuplicateGroup[]
  total_groups: number
}

export interface KBQueryResult {
  content: string
  relevance: number
  artifact_id: string
  filename: string
  domain: string
  sub_category?: string
  tags?: string[]
  keywords?: string[]
  chunk_index: number
  chunk_count?: number
  collection: string
  ingested_at: string
  graph_source?: boolean
  relationship_type?: string
  cross_domain?: boolean
  quality_score?: number
  quality_scores?: Record<string, number>
  summary?: string
  source_url?: string
  source_name?: string
  source_type?: "kb" | "memory" | "external"
  starred?: boolean
  evergreen?: boolean
  retrieval_count?: number
}

export interface MemoryRecallResult {
  content: string
  relevance: number
  memory_type: string
  age_days: number
  summary: string
  memory_id: string
  source_authority: number
  base_similarity: number
  access_count: number
  source_type: "memory"
  /** Provenance: "verification" for promoted verified facts, undefined for standard memories */
  memory_source_type?: string
}

export interface ExternalSourceResult {
  content: string
  relevance: number
  source_url: string
  source_name?: string
  source_type: "external"
}

export interface SourceBreakdown {
  kb: KBQueryResult[]
  memory: MemoryRecallResult[]
  external: ExternalSourceResult[]
}

export interface AgentQueryResponse {
  context?: string
  sources?: SourceRef[]
  confidence: number
  domains_searched?: string[]
  total_results: number
  token_budget_used?: number
  graph_results?: number
  results: KBQueryResult[]
  /** Elapsed wall-clock time for the query pipeline (ms). */
  execution_time_ms?: number
  source_breakdown?: SourceBreakdown
  rag_mode?: RagMode
  /** Non-empty string when the retrieval pipeline exceeded its time budget and
   *  returned an ungrounded answer. Empty string when not degraded. */
  degraded_reason?: string
  /** Timing breakdown (only present when X-Debug-Timing header is sent). */
  _timings?: Record<string, number>
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

export interface DigestArtifact {
  id: string
  filename: string
  domain: string
  summary: string
  ingested_at: string
}

export interface DigestErrorItem {
  timestamp?: string | null
  filename?: string | null
  artifact_id?: string | null
  domain?: string | null
  detail?: string | null
}

export interface DigestResponse {
  period_hours: number
  generated_at: string
  artifacts: {
    count: number
    items: DigestArtifact[]
    by_domain: Record<string, number>
  }
  relationships: { new_count: number }
  health: MaintenanceHealth
  recent_events: number
  errors?: {
    count: number
    items: DigestErrorItem[]
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
  hourly_by_type?: Record<string, Record<string, number>>
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

export interface AuditVerification {
  total_checks: number
  avg_accuracy: number
  by_model: Record<string, {
    checks: number
    accuracy: number
    verified: number
    unverified: number
    uncertain: number
  }>
  hourly_accuracy: Record<string, number>
}

export interface AuditResponse {
  timestamp: string
  reports_generated: string[]
  activity?: AuditActivity
  ingestion?: AuditIngestion
  costs?: AuditCosts
  queries?: AuditQueries
  conversations?: AuditConversations
  verification?: AuditVerification
}

export interface CurateResponse {
  timestamp: string
  mode: string
  artifacts_scored: number
  artifacts_stored: number
  synopses_generated: number
  avg_quality_score: number
  score_distribution: Record<string, number>
  domains_scored: string[]
  low_quality_artifacts: {
    artifact_id: string
    filename: string
    domain: string
    quality_score: number
    breakdown: Record<string, number>
    issues: string[]
  }[]
}

/** Shared fields across streaming and completed claim types. */
export interface BaseClaim {
  claim: string
  claim_type?: "factual" | "evasion" | "ignorance" | "citation"
  source_artifact_id?: string
  source_domain?: string
  source_snippet?: string
  source_urls?: string[]
  reason?: string
  verification_method?: "kb" | "cross_model" | "cross_model_failed" | "web_search" | "web_search_failed" | "kb_nli" | "timeout" | "kb_only_timeout" | "none"
  verification_model?: string
  verification_answer?: string
  consistency_issue?: string
  /** True when the claim was verified against KB artifacts that were injected into the LLM prompt (circular evidence). */
  circular_source?: boolean
  nli_entailment?: number      // NLI entailment score (0-1)
  nli_contradiction?: number   // NLI contradiction score (0-1)
  memory_source?: boolean      // Whether verified against user memory
}

export interface HallucinationClaim extends BaseClaim {
  status: "verified" | "unverified" | "uncertain" | "error" | "skipped"
  similarity: number
  source_filename?: string
  user_feedback?: "correct" | "incorrect"
}

export interface StreamingClaim extends BaseClaim {
  index: number
  status?: "verified" | "unverified" | "uncertain" | "error" | "pending" | "skipped"
  similarity?: number
  source?: string
  metamorphic_score?: MetamorphicScore
}

export interface HallucinationReport {
  conversation_id: string
  timestamp: string
  skipped: boolean
  threshold?: number
  extraction_method?: string
  claims: HallucinationClaim[]
  summary: {
    total: number
    verified: number
    unverified: number
    uncertain: number
    error?: number
    skipped?: number
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

export type RoutingMode = "manual" | "recommend" | "auto"

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
  enable_model_router: boolean
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
  storage_mode: string
  sync_backend: string
  machine_id: string
  version: string
  memory_config?: {
    min_response_length: number
    memory_types: string[]
    retention_days: number
    storage_domain: string
    extraction_model: string
  }
  enable_self_rag?: boolean
  multi_user?: boolean
  // Infrastructure (read-only)
  bifrost_url?: string
  bifrost_timeout?: number
  chroma_url?: string
  neo4j_uri?: string
  redis_url?: string
  archive_path?: string
  chunking_mode?: string
  rag_mode?: string  // KB injection mode: "smart" | "always" | "manual"
  // Search tuning (read-write)
  hybrid_vector_weight?: number
  hybrid_keyword_weight?: number
  rerank_llm_weight?: number
  rerank_original_weight?: number
  temporal_half_life_days?: number
  temporal_recency_weight?: number
  // Advanced RAG pipeline (read-write)
  enable_contextual_chunks?: boolean
  enable_adaptive_retrieval?: boolean
  adaptive_retrieval_light_top_k?: number
  enable_query_decomposition?: boolean
  query_decomposition_max_subqueries?: number
  enable_mmr_diversity?: boolean
  mmr_lambda?: number
  enable_intelligent_assembly?: boolean
  enable_late_interaction?: boolean
  late_interaction_top_n?: number
  late_interaction_blend_weight?: number
  enable_semantic_cache?: boolean
  semantic_cache_threshold?: number
  enable_memory_consolidation?: boolean
  enable_context_compression?: boolean
  // Context source gates
  context_sources?: ContextSources
  // Ollama add-on (local LLM for pipeline tasks)
  pipeline_providers?: PipelineProviders
  ollama_enabled?: boolean
  ollama_url?: string
  internal_llm_provider?: string
  internal_llm_model?: string
}

/** Ollama add-on status from GET /providers/ollama/status */
export interface OllamaStatus {
  enabled: boolean
  url: string
  reachable: boolean
  models: string[]
  default_model: string
  default_model_installed: boolean
}

/** Model recommendation from GET /providers/ollama/recommendations */
export interface OllamaModelOption {
  id: string
  name: string
  origin: string
  size_gb: number
  min_ram_gb: number
  description: string
  strengths: string
  tier: "lightweight" | "balanced" | "performance"
  compatible: boolean
  recommended: boolean
}

export interface OllamaRecommendations {
  hardware: {
    ram_gb: number
    cpu: string
    gpu: string
    platform: string
  }
  models: OllamaModelOption[]
  recommended: string
}

export interface SettingsUpdate {
  categorize_mode?: string
  enable_feedback_loop?: boolean
  enable_hallucination_check?: boolean
  enable_memory_extraction?: boolean
  enable_model_router?: boolean
  hallucination_threshold?: number
  cost_sensitivity?: string
  enable_auto_inject?: boolean
  auto_inject_threshold?: number
  enable_self_rag?: boolean
  storage_mode?: string
  hybrid_vector_weight?: number
  hybrid_keyword_weight?: number
  rerank_llm_weight?: number
  rerank_original_weight?: number
  // Advanced RAG pipeline
  enable_contextual_chunks?: boolean
  enable_adaptive_retrieval?: boolean
  adaptive_retrieval_light_top_k?: number
  enable_query_decomposition?: boolean
  query_decomposition_max_subqueries?: number
  enable_mmr_diversity?: boolean
  mmr_lambda?: number
  enable_intelligent_assembly?: boolean
  enable_late_interaction?: boolean
  late_interaction_top_n?: number
  late_interaction_blend_weight?: number
  enable_semantic_cache?: boolean
  semantic_cache_threshold?: number
  enable_memory_consolidation?: boolean
  enable_context_compression?: boolean
  rag_mode?: string  // KB injection mode in SettingsUpdate
  // Context source gates
  context_sources?: ContextSources
  // Ollama add-on
  internal_llm_provider?: string
  internal_llm_model?: string
}

export interface Memory {
  id: string
  type: string // empirical, decision, preference, project_context, temporal, conversational
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

export interface SynopsisEstimate {
  candidate_count: number
  model: string
  model_label: string
  estimated_cost_usd: number
  estimated_time_display: string
  rpm_limit: number
  is_free_model: boolean
}

export const SYNOPSIS_MODELS = [
  { id: "openrouter/meta-llama/llama-3.3-70b-instruct:free", label: "Llama 3.3 (Free)" },
  { id: "openrouter/openai/gpt-4o-mini", label: "GPT-4o Mini" },
  { id: "openrouter/google/gemini-2.5-flash", label: "Gemini 2.5 Flash" },
  { id: "openrouter/anthropic/claude-sonnet-4.6", label: "Claude Sonnet 4.6" },
] as const

export interface LiveMetrics {
  inputTokens: number
  outputTokens: number
  contextPct: number
  sessionCost: number
  messageCost: number
  messagesCount: number
}

// -- Sync types --------------------------------------------------------------

export interface SyncManifest {
  machine_id: string
  timestamp: string
  sync_format_version: number
  last_exported_at: string
  is_incremental: boolean
  domains: string[]
  files: Record<string, { exists: boolean; count?: number; sha256: string }>
}

export interface SyncCounts {
  neo4j_artifacts: number
  neo4j_domains: number
  neo4j_relationships: number
  chroma_chunks: Record<string, number>
  redis_entries: number
}

export interface SyncStatus {
  sync_dir: string
  manifest: SyncManifest | null
  local: SyncCounts
  sync: SyncCounts
  diff: SyncCounts
}

export interface SyncExportResult {
  neo4j: { artifacts: number; domains: number; relationships: number }
  chroma: Record<string, number>
  bm25: Record<string, number>
  redis: number
  tombstones: number
  manifest: SyncManifest
}

export interface SyncImportResult {
  neo4j: {
    artifacts_created: number
    artifacts_updated: number
    artifacts_skipped: number
    artifacts_conflict: number
    conflicts: Array<{
      artifact_id: string
      filename: string
      domain: string
      resolution: string
    }>
  }
  chroma: Record<string, number>
  bm25: Record<string, number>
  redis: number
  tombstones: number
  consistency_warnings: string[]
}

// -- Auth types --------------------------------------------------------------

export interface AuthUser {
  id: string
  email: string
  display_name: string
  role: string
  tenant_id: string
  has_api_key: boolean
  usage_queries: number
  usage_ingestions: number
  created_at: string
  last_login: string | null
}

export interface AuthTokens {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: AuthUser
}

export interface UsageInfo {
  queries: number
  ingestions: number
  month: string
}

export type ConflictStrategy = "remote_wins" | "local_wins" | "keep_both" | "manual_review"

export const CONFLICT_STRATEGIES: { value: ConflictStrategy; label: string; description: string }[] = [
  { value: "remote_wins", label: "Remote Wins", description: "Remote version replaces local on conflict" },
  { value: "local_wins", label: "Local Wins", description: "Keep local version on conflict" },
  { value: "keep_both", label: "Keep Both", description: "Create duplicate entries for conflicts" },
  { value: "manual_review", label: "Manual Review", description: "Flag conflicts for manual resolution" },
]

export interface ChatSettings {
  temperature?: number
  top_p?: number
}

export interface ChatModelInfo {
  requested_model: string
  resolved_model: string
  actual_model?: string
  fallback_model?: string
  original_error?: number
}

// -- Setup Wizard types (first-run) -------------------------------------------

export interface SetupStatus {
  configured: boolean
  setup_required: boolean
  missing_keys: string[]
  optional_keys: string[]
  configured_providers: string[]
  provider_status?: Record<string, { configured: boolean; key_env_var: string; key_present: boolean }>
  services?: Record<string, string>
}

export interface KeyValidation {
  valid: boolean
  provider: string
  error?: string
  error_type?: string
  suggestion?: string
  provider_name?: string
}

export interface SetupConfig {
  keys: Record<string, string>
  archive_path?: string
  domains?: string[]
  lightweight_mode?: boolean
  watch_folder?: boolean
  ollama_enabled?: boolean
  ollama_model?: string
}

export interface SetupConfigRequest {
  keys?: Record<string, string>
  archive_path?: string
  domains?: string[]
  lightweight_mode?: boolean
  watch_folder?: boolean
  ollama_enabled?: boolean
  ollama_model?: string
}

export interface SystemCheckResponse {
  ram_gb: number
  docker_running: boolean
  env_exists: boolean
  env_keys_present: string[]
  ollama_detected: boolean
  ollama_url: string | null
  ollama_models: string[]
  lightweight_recommended: boolean
  archive_path_exists: boolean
  default_archive_path: string
  os: string
  cpu: string
  cpu_cores: number | null
  gpu: string
  gpu_acceleration: string
}

export interface SetupServiceHealth {
  name: string
  status: "healthy" | "degraded" | "error" | "connected"
}

export interface SetupHealth {
  all_healthy: boolean
  services: SetupServiceHealth[]
}

// -- Automations types --------------------------------------------------------

export interface Automation {
  id: string
  name: string
  description: string
  prompt: string
  schedule: string
  action: "notify" | "digest" | "ingest"
  domains: string[]
  enabled: boolean
  created_at: string
  updated_at: string
  last_run_at: string | null
  last_status: string | null
  run_count: number
}

export interface AutomationCreate {
  name: string
  description?: string
  prompt: string
  schedule: string
  action: "notify" | "digest" | "ingest"
  domains?: string[]
  enabled?: boolean
}

export interface AutomationRun {
  automation_id: string
  run_id: string
  started_at: string
  completed_at: string | null
  status: "running" | "success" | "error"
  result: Record<string, unknown> | null
  error: string | null
}

// ── Observability ────────────────────────────────────────────────────────────

export interface MetricAggregation {
  avg: number | null
  p50: number | null
  p95: number | null
  p99: number | null
  min: number | null
  max: number | null
  count: number
}

export interface AggregatedMetricsResponse {
  window_minutes: number
  timestamp: string
  metrics: Record<string, MetricAggregation>
}

export interface TimeSeriesPoint {
  timestamp: number
  value: number
  tags: Record<string, string>
}

export interface TimeSeriesResponse {
  name: string
  window_minutes: number
  points: TimeSeriesPoint[]
  count: number
}

export interface HealthScoreResponse {
  score: number
  grade: string
  factors: Record<string, Record<string, number | string | null>>
  timestamp: string
}

export interface CostBreakdownResponse {
  window_minutes: number
  total_cost_usd: number
  by_model: Record<string, number>
  timestamp: string
}

export interface QualityMetricsResponse {
  window_minutes: number
  retrieval_ndcg: MetricAggregation
  verification_accuracy: MetricAggregation
  cache_hit_rate: MetricAggregation
  timestamp: string
}

// ── Plugins ──────────────────────────────────────────────────────────────────

export type PluginStatus = "installed" | "active" | "error" | "disabled"

export interface Plugin {
  name: string
  version: string
  description: string
  tier_required: string
  enabled: boolean
  status: PluginStatus
  file_types: string[]
  config_schema: Record<string, unknown> | null
  capabilities: string[]
}

export interface PluginConfig {
  values: Record<string, unknown>
}

export interface PluginListResponse {
  plugins: Plugin[]
  total: number
}

// ── Workflows ───────────────────────────────────────────────────────────────

export type WorkflowNodeType = "agent" | "parser" | "tool" | "condition"
export type WorkflowRunStatus = "pending" | "running" | "completed" | "failed"

export interface WorkflowNodePosition {
  x: number
  y: number
}

export interface WorkflowNode {
  id: string
  type: WorkflowNodeType
  name: string
  config: Record<string, unknown>
  position: WorkflowNodePosition
}

export interface WorkflowEdge {
  source_id: string
  target_id: string
  label: string | null
  condition: string | null
}

export interface Workflow {
  id: string
  name: string
  description: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  created_at: string
  updated_at: string
  enabled: boolean
}

export interface WorkflowCreate {
  name: string
  description?: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  enabled?: boolean
}

export interface WorkflowRun {
  id: string
  workflow_id: string
  status: WorkflowRunStatus
  started_at: string
  finished_at: string | null
  results: Record<string, unknown>
  error: string | null
}

export interface WorkflowListResponse {
  workflows: Workflow[]
  total: number
}

export interface WorkflowTemplate {
  id: string
  name: string
  description: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

// KB parser capabilities
export interface ParserCapability {
  extension: string
  parser: string
  tier: "community" | "pro" | "enterprise"
  available?: boolean
}

// Artifact with client source
export interface ArtifactFilterParams {
  domain?: string
  client_source?: string
  since?: string
  min_quality?: number
  limit?: number
  offset?: number
}

// ---------------------------------------------------------------------------
// Storage Monitoring
// ---------------------------------------------------------------------------

export interface ServiceStorageMetrics {
  disk_mb: number
  error?: string
}

export interface ChromaDBStorage extends ServiceStorageMetrics {
  collections: number
  chunks: number
}

export interface Neo4jStorage extends ServiceStorageMetrics {
  nodes: number
  relationships: number
  status?: string
}

export interface RedisStorage {
  memory_mb: number
  peak_mb: number
  keys: number
  error?: string
}

export interface BM25Storage {
  disk_mb: number
  index_count: number
}

export interface StorageMetrics {
  chromadb: ChromaDBStorage
  neo4j: Neo4jStorage
  redis: RedisStorage
  bm25: BM25Storage
  total_mb: number
  limit_mb: number
  usage_pct: number
  warn_pct: number
  critical_pct: number
  status: "healthy" | "warning" | "critical"
  timestamp: number
}

export interface IngestHistoryEntry {
  id: string
  filename: string
  source_type: string
  domain: string
  status: "success" | "failed" | "skipped"
  timestamp: string
  chunks: number
  error: string
}

export interface IngestHistoryResponse {
  items: IngestHistoryEntry[]
  total: number
  next_cursor: string | null
}

// === Agent Communication Console ===

export interface AgentConsoleEvent {
  id: string
  agent: string
  message: string
  level: "info" | "success" | "warning" | "error"
  timestamp: number
  metadata: Record<string, unknown>
}

// === Dead Letter Queue ===

export interface DLQEntry {
  id: string
  payload: Record<string, unknown>
  error: string
  attempt: number
  next_retry_at: string
  created_at: string
}

// === Storage Dashboard ===

export interface StorageServiceUsage {
  disk_mb: number
  collections?: number
  chunks?: number
  nodes?: number
  relationships?: number
  memory_mb?: number
  peak_mb?: number
  keys?: number
  index_count?: number
}

export interface StorageUsage {
  chromadb: StorageServiceUsage
  neo4j: StorageServiceUsage
  redis: StorageServiceUsage
  bm25: StorageServiceUsage
  total_mb: number
}

// === Private Mode ===

export interface PrivateModeStatus {
  enabled: boolean
  level: 1 | 2 | 3 | 4
}
