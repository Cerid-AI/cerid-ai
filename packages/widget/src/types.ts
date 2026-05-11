// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Types mirroring the /sdk/v1/query backend contract.
 *
 * Source of truth: src/mcp/app/models/sdk.py::SDKQueryResponse
 *                  src/mcp/app/routers/agents.py::AgentQueryRequest
 *                  src/mcp/core/agents/hallucination/models.py::ClaimVerification
 *
 * Do NOT import from src/web/ — the widget is self-contained.
 */

// ---------------------------------------------------------------------------
// /sdk/v1/query request
// ---------------------------------------------------------------------------

export interface SDKQueryRequest {
  query: string;
  domains?: string[];
  top_k?: number;
  use_reranking?: boolean;
  conversation_messages?: Array<{ role: string; content: string }>;
  response_text?: string;
  model?: string;
  enable_self_rag?: boolean;
  cost_sensitivity?: "low" | "medium" | "high";
  query_scope?: "document" | "domain" | "kb";
}

// ---------------------------------------------------------------------------
// Source chunk (element of SDKQueryResponse.sources / .results)
// ---------------------------------------------------------------------------

export interface SDKSourceChunk {
  content?: string;
  text?: string;
  score?: number;
  relevance?: number;
  domain?: string;
  filename?: string;
  source?: string;
  metadata?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// /sdk/v1/query response
// ---------------------------------------------------------------------------

export interface SDKQueryResponse {
  context: string;
  sources: SDKSourceChunk[];
  confidence: number;
  domains_searched: string[];
  total_results: number;
  token_budget_used: number;
  graph_results: number;
  results: SDKSourceChunk[];
  /** Agent answer text (may be present when Self-RAG is enabled). */
  answer?: string;
  /** Streaming tokens array (when streaming is enabled). */
  tokens?: string[];
  /** Per-claim verification from the Self-RAG pipeline. */
  claims?: ClaimVerification[];
}

// ---------------------------------------------------------------------------
// Claim verification (mirrors ClaimVerificationFE from src/web/src/components/verification/types.ts)
// ---------------------------------------------------------------------------

export type ClaimStatus =
  | "verified"
  | "unverified"
  | "uncertain"
  | "skipped"
  | "error";

export type ClaimType = "factual" | "evasion" | "ignorance" | "citation";

/** The three linguistic bands rendered by ClaimBadge. */
export type VerificationBand = "verified" | "partial" | "unverified";

export interface ClaimVerification {
  claim: string;
  claim_type?: ClaimType;
  status: ClaimStatus;
  confidence: number;
  similarity?: number;
  reason?: string;

  // KB provenance
  source_artifact_id?: string;
  source_filename?: string;
  source_domain?: string;
  source_snippet?: string;

  // Web provenance
  source_urls?: string[];

  // Verifier metadata
  verification_method?: string;
  verification_model?: string;
  verification_answer?: string;

  // NLI scores
  nli_entailment?: number;
  nli_contradiction?: number;

  // Flags
  memory_source?: boolean;
  circular_source?: boolean;
}

/**
 * Derive the three linguistic bands from a ClaimVerification.
 * Mirrors deriveBand() in src/web/src/components/verification/types.ts.
 */
export function deriveBand(claim: ClaimVerification): VerificationBand {
  const hasSource =
    !!(claim.source_artifact_id || (claim.source_urls?.length ?? 0) > 0);

  if (claim.status === "verified") {
    return hasSource ? "verified" : "partial";
  }
  if (claim.status === "uncertain") {
    return "partial";
  }
  return "unverified";
}

/** Count sources for a claim. */
export function sourceCount(claim: ClaimVerification): number {
  const urlCount = claim.source_urls?.length ?? 0;
  const artifactCount = claim.source_artifact_id ? 1 : 0;
  return Math.max(urlCount, artifactCount);
}

// ---------------------------------------------------------------------------
// Widget state
// ---------------------------------------------------------------------------

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  streaming?: boolean;
  claims?: ClaimVerification[];
  error?: boolean;
}

export type WidgetTheme = "light" | "dark" | "auto";
