// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * TypeScript interfaces matching the server-side SDK response models
 * defined in src/mcp/app/models/sdk.py.
 *
 * All response types allow extra fields (index signature) for forward
 * compatibility — the server uses `extra="allow"` on its Pydantic models.
 */

// ---------------------------------------------------------------------------
// Client configuration
// ---------------------------------------------------------------------------

export interface CeridClientOptions {
  /** Base URL of the Cerid MCP server, e.g. "http://localhost:8888" */
  baseUrl: string;
  /** X-Client-ID header for per-client rate limiting and domain scoping */
  clientId: string;
  /** Optional API key (X-API-Key header). Only required when server has CERID_API_KEY set. */
  apiKey?: string;
  /** Optional custom fetch implementation (defaults to globalThis.fetch). */
  fetch?: typeof globalThis.fetch;
  /** Default per-request timeout in milliseconds. */
  timeoutMs?: number;
}

export interface RequestOptions {
  timeoutMs?: number;
  idempotencyKey?: string;
}

// ---------------------------------------------------------------------------
// Request types
// ---------------------------------------------------------------------------

export interface QueryRequest {
  query: string;
  domains?: string[] | null;
  top_k?: number;
  use_reranking?: boolean;
  conversation_messages?: Array<{ role: string; content: string }> | null;
  response_text?: string | null;
  model?: string | null;
  enable_self_rag?: boolean | null;
  strict_domains?: boolean | null;
  /**
   * Gates whole retrieval surfaces, e.g. `{ kb: true, memory: true, external: false }`
   * to keep open-web results out. The only request field that does:
   * `strict_domains` narrows the KB's own domain bleed and never touches the web,
   * and `source_config` only tunes weights for `rag_mode: "custom_smart"`.
   */
  context_sources?: { kb?: boolean; memory?: boolean; external?: boolean } | null;
  rag_mode?: string | null;
  source_config?: Record<string, unknown> | null;
}

export interface HallucinationCheckRequest {
  response_text: string;
  conversation_id: string;
  threshold?: number | null;
  model?: string | null;
}

export interface MemoryExtractRequest {
  response_text: string;
  conversation_id: string;
  model?: string;
}

export interface MemoryRecallRequest {
  query: string;
  top_k?: number;
  min_score?: number;
}

export interface MemoryRecallResponse {
  memories: Array<Record<string, unknown>>;
  total: number;
  /** True when recall failed server-side: the empty list is an outage, not an empty memory. */
  degraded?: boolean;
  [key: string]: unknown;
}

export interface DeleteArtifactResponse {
  deleted: boolean;
  artifact_id: string;
  filename: string;
  chunks_removed: number;
  message: string;
  [key: string]: unknown;
}

export interface IngestRequest {
  content: string;
  domain?: string;
  tags?: string;
  /**
   * Arbitrary provenance/attribution metadata stored with the artifact and
   * queryable (e.g. { title, provenance, source_file }). Preserved alongside
   * `tags`. (External-client backend support.)
   */
  metadata?: Record<string, unknown>;
}

export interface IngestFileRequest {
  file_path: string;
  domain?: string;
  tags?: string;
  /** Categorization tier, e.g. "manual", "smart" or "pro". Empty uses the server default. */
  categorize_mode?: string;
}

export interface SearchRequest {
  query: string;
  domain?: string;
  top_k?: number;
  /**
   * Drop bundled knowledge-pack chunks and search only the operator's own
   * content (personal-first retrieval). Defaults to false server-side.
   */
  exclude_packs?: boolean;
}

// ---------------------------------------------------------------------------
// Response types
// ---------------------------------------------------------------------------

export interface QueryResponse {
  context: string;
  sources: Array<Record<string, unknown>>;
  confidence: number;
  domains_searched: string[];
  total_results: number;
  token_budget_used: number;
  graph_results: number;
  results: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface HallucinationResponse {
  conversation_id: string;
  timestamp: string;
  skipped: boolean;
  reason: string | null;
  claims: Array<Record<string, unknown>>;
  /**
   * Integer per-status counts (`total`, `verified`, `unverified`,
   * `uncertain`, `assessed`) plus the float `overall_confidence`. Mirrors the
   * server's `dict[str, float | int]` — a fixed counts-only shape here made
   * every real response a type error at the call site.
   */
  summary: Record<string, number>;
  /**
   * Verification depth actually applied: `fast` returns extracted claims with
   * `status: "uncertain"` and `nli_skipped: true`; `thorough` runs the full
   * cross-model pipeline.
   */
  mode: string;
  /**
   * True when cross-model NLI verification was skipped (fast mode) — render a
   * hedged warning rather than an authoritative verdict.
   */
  nli_skipped: boolean;
  [key: string]: unknown;
}

export interface MemoryExtractResponse {
  conversation_id: string;
  timestamp: string;
  memories_extracted: number;
  memories_stored: number;
  skipped_duplicates: number;
  results: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface HealthResponse {
  status: string;
  version: string;
  services: Record<string, string>;
  features: Record<string, boolean>;
  [key: string]: unknown;
}

export interface DetailedHealthResponse extends HealthResponse {
  circuit_breakers: Record<string, string>;
  degradation_tier: string;
  uptime_seconds: number;
}

export interface IngestResponse {
  status: string;
  artifact_id: string;
  chunks: number;
  domain: string;
  [key: string]: unknown;
}

export interface CollectionsResponse {
  collections: string[];
  total: number;
  [key: string]: unknown;
}

export interface TaxonomyResponse {
  domains: string[];
  taxonomy: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SettingsResponse {
  version: string;
  tier: string;
  features: Record<string, boolean>;
  [key: string]: unknown;
}

export interface SearchResponse {
  results: Array<Record<string, unknown>>;
  total_results: number;
  confidence: number;
  [key: string]: unknown;
}

export interface PluginListResponse {
  plugins: Array<Record<string, unknown>>;
  total: number;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Async memory extract (POST /sdk/v1/memory/extract → 200 or 202)
// ---------------------------------------------------------------------------

/**
 * 202 Accepted envelope returned when the server enqueued the extraction
 * (`MEMORY_QUEUE_MODE=async`, the default on local-inference installs).
 * Poll `client.memory.getJob(job_id)` for the result.
 */
export interface MemoryExtractAcceptedResponse {
  job_id: string;
  /** Always "queued" on accept. */
  status: string;
  /** Path to GET for the job result, relative to the SDK base URL. */
  status_url: string;
  conversation_id?: string;
  [key: string]: unknown;
}

/** Narrow a `memory.extract()` result to the queued (202) envelope. */
export function isMemoryExtractAccepted(
  result: MemoryExtractResponse | MemoryExtractAcceptedResponse,
): result is MemoryExtractAcceptedResponse {
  return typeof (result as MemoryExtractAcceptedResponse).job_id === "string"
    && typeof (result as MemoryExtractAcceptedResponse).status_url === "string";
}

// ---------------------------------------------------------------------------
// Async memory extract job polling (GET /sdk/v1/memory/extract/jobs/{job_id})
// ---------------------------------------------------------------------------

export interface MemoryExtractJobStatus {
  job_id: string;
  /** queued | started | finished | failed | deferred | scheduled | canceled | unknown */
  status: string;
  enqueued_at?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  /** Populated only when status === "finished" */
  result?: MemoryExtractResponse | null;
  /** Populated only when status === "failed" */
  error?: string | null;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Smart-routed LLM completion (POST /sdk/v1/llm/complete)
// ---------------------------------------------------------------------------

export interface LLMCompleteRequest {
  /** OpenAI-format messages: [{role, content}, ...] */
  messages: Array<{ role: string; content: string }>;
  /** chat | internal | verification | classification | research */
  task_type?: string;
  /** Optional query summary for the router's complexity classifier */
  query?: string;
  /**
   * How sensitive you are to spend, not what you want to spend:
   * `high` routes to the cheapest tier that can do the job, `low` to the most
   * capable model. Defaults to `medium`.
   */
  cost_sensitivity?: string;
  temperature?: number;
  max_tokens?: number;
  /** OpenAI-compatible response format spec (e.g., {"type": "json_object"}) */
  response_format?: Record<string, unknown>;
  /** Wall-clock budget in ms; filters tiers by empirical p95 latency. */
  slo_budget_ms?: number;
}

export interface LLMCompleteResponse {
  content: string;
  model: string;
  /** ollama | quenchforge | openrouter_paid */
  provider: string;
  reason: string;
  estimated_cost_per_1k: number;
  /** Empirical p95 wall-clock for the routed tier; 0 when no profile yet. */
  tier_p95_ms: number;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Generic external ingest (POST /sdk/v1/ingest/external)
// ---------------------------------------------------------------------------

export interface IngestExternalRequest {
  /** Free-form label (e.g. "readwise", "pocket", "telegram-bot"). */
  source_type: string;
  /** Raw JSON payload from the external service. */
  payload: Record<string, unknown>;
  /** Mapping config extracting canonical fields from `payload`. */
  field_mappings: Record<string, unknown>;
}

export interface IngestExternalResponse {
  accepted: number;
  skipped: number;
  errors: Array<Record<string, unknown>>;
  source_type: string;
  [key: string]: unknown;
}
