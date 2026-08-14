// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { MCP_BASE, mcpHeaders, extractError } from "./common"

// ---------------------------------------------------------------------------
// Smart-routing configuration (GET /providers/routing)
//
// The backend resolves each capability tier through the weekly
// `model_auto_update` catalog overlay, so `model_registry` is the single
// source of truth for "the current model at each capability level". The chat
// UI reads the cheap tier's first slot to seed its default model, so the
// default tracks catalog refreshes without a code change (operator directive:
// default models must be tier-resolved, not pinned literals).
// ---------------------------------------------------------------------------

/** slot-name → resolved model id (e.g. { "gpt-4o-mini": "openrouter/openai/gpt-4o-mini" }). */
export type ModelTier = Record<string, string>

/** Capability tiers, cheapest-first. Every id is OpenRouter-prefixed. */
export interface ModelRegistry {
  free: ModelTier
  cheap: ModelTier
  capable: ModelTier
  research: ModelTier
  expert: ModelTier
}

export interface RoutingInfo {
  ollama_available: boolean
  ollama_models: string[]
  model_registry: ModelRegistry
  default_internal_model: string
  smart_routing_enabled: boolean
}

/** Throws on a non-2xx (error-is-not-empty rule): the caller distinguishes
 *  "endpoint unavailable → fall back to the static default table" from a real
 *  registry, so a masked empty response would silently pin the fallback id. */
export async function fetchRoutingInfo(): Promise<RoutingInfo> {
  const res = await fetch(`${MCP_BASE}/providers/routing`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Routing info fetch failed: ${res.status}`))
  return res.json()
}
