// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * TypeScript interfaces matching the server-side SDK response models
 * defined in src/mcp/models/sdk.py.
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

export interface IngestRequest {
  content: string;
  domain?: string;
  tags?: string;
}

export interface IngestFileRequest {
  file_path: string;
  domain?: string;
  tags?: string;
  categorize_mode?: string;
}

export interface SearchRequest {
  query: string;
  domain?: string;
  top_k?: number;
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
  summary: {
    total: number;
    verified: number;
    unverified: number;
    uncertain: number;
  };
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
