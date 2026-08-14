// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * derive-defaults — pure helpers that compute UI defaults from the user's
 * configured providers, so we never hand a new user a model they don't have
 * credits for.
 *
 * Previously the chat panel defaulted to `openrouter/anthropic/claude-sonnet-4.6`
 * regardless of which providers the user actually configured. A user who
 * added only an OpenAI key saw the Claude default and got a "provider not
 * available" error on their first message. This module picks a model the
 * user can actually use.
 *
 * Priority within a provider is cheapest-capable-first — the user can
 * upgrade to a heavier model manually, but the default shouldn't torch their
 * credits.
 *
 * The concrete OpenRouter default id is no longer pinned here: when the live
 * model registry (GET /providers/routing) is available it supplies the current
 * cheap-tier id, which the weekly `model_auto_update` catalog overlay keeps
 * fresh. The static PROVIDER_DEFAULTS table below is a FALLBACK only, used when
 * that endpoint is unavailable or a non-OpenRouter provider is selected.
 */

import type { ModelRegistry } from "./api/routing"

export interface ProviderStatus {
  /** Provider slug: "openrouter" | "openai" | "anthropic" | "xai" | "ollama" */
  id: string
  configured: boolean
  /** Optional list of models the provider reports as available. */
  availableModels?: string[]
}

/**
 * FALLBACK priority table — used only when the live model registry
 * (GET /providers/routing) is unavailable. First entry in each provider list
 * is the default when that provider is selected. Models here must also exist
 * in MODELS (types.ts) so the chat model selector can find + display them.
 *
 * For the OpenRouter default the registry's cheap tier is authoritative; this
 * table's `openrouter` first entry is the stale-but-safe backstop.
 */
const PROVIDER_DEFAULTS: Record<string, string[]> = {
  // OpenRouter is preferred because it fans out to everything; within
  // OpenRouter, the cheapest capable model wins the default slot.
  openrouter: [
    "openrouter/openai/gpt-4o-mini",        // cheapest capable ~$0.15/1M
    "openrouter/anthropic/claude-haiku-4.5",
    "openrouter/google/gemini-2.5-flash",
    "openrouter/openai/gpt-4o",
    "openrouter/anthropic/claude-sonnet-4.6",
  ],
  openai: [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
  ],
  anthropic: [
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4.6",
  ],
  xai: [
    "xai/grok-4-fast",
    "xai/grok-4",
  ],
  ollama: [
    "ollama/llama3.2:3b",
    "ollama/llama3.1:8b",
  ],
}

// Preferred provider order when multiple are configured — OpenRouter first
// because it usually has the widest model catalog and most predictable
// billing; Ollama last because the default model (3B Llama) is quality-
// limited for general chat.
const PROVIDER_PREFERENCE: readonly string[] = [
  "openrouter", "openai", "anthropic", "xai", "ollama",
]

/**
 * The cheap tier is the default capability level. Its first slot is the
 * cheapest — object insertion order mirrors the backend's tier dict, which is
 * declared cheapest-first. Returns null when the registry has no cheap tier.
 */
export function pickCheapTierDefault(
  registry: ModelRegistry | null | undefined,
): string | null {
  const ids = registry?.cheap ? Object.values(registry.cheap) : []
  return ids.length > 0 ? ids[0] : null
}

/**
 * Pick the best default chat model for a user given their configured
 * providers. Returns null when the user has nothing configured so the
 * caller can render an "add a provider" hint instead of silently picking
 * a model that will fail.
 *
 * Resolution order: live registry (cheap tier, first slot) → static
 * PROVIDER_DEFAULTS table. The registry is authoritative for OpenRouter — its
 * ids already carry the `openrouter/` prefix and are catalog-refreshed — so
 * when OpenRouter is configured and the registry is present, the default is
 * the current cheap-tier id rather than the pinned fallback. Every other case
 * (registry absent, or a non-OpenRouter provider selected) uses the table.
 */
export function deriveDefaultModel(
  providers: ProviderStatus[],
  registry?: ModelRegistry | null,
): string | null {
  const configured = new Set(
    providers.filter((p) => p.configured).map((p) => p.id),
  )
  if (configured.size === 0) return null

  // Registry path: OpenRouter is the top-preference provider, so when it's
  // configured the tier-resolved cheap id is the future-proof default.
  if (configured.has("openrouter")) {
    const cheapDefault = pickCheapTierDefault(registry)
    if (cheapDefault) return cheapDefault
  }

  for (const provider of PROVIDER_PREFERENCE) {
    if (!configured.has(provider)) continue
    const list = PROVIDER_DEFAULTS[provider]
    if (list && list.length > 0) return list[0]
  }
  return null
}

export type CreditsState =
  | { kind: "unconfigured"; message: string }
  | { kind: "untested"; message: string }
  | { kind: "zero"; message: string; amount: number }
  | { kind: "positive"; amount: number }
  | { kind: "error"; message: string }

/**
 * Derive user-facing copy for the Provider Credits panel. Previously the
 * panel showed `$—` / `$—` / `$—` with zero context — users had no way to
 * know whether they hadn't set up OpenRouter, or it hadn't responded, or
 * they truly had $0.
 */
export function deriveCreditsCopy(
  providers: ProviderStatus[],
  credits: { balance: number | null; lastRefreshMs: number | null; errorMessage?: string },
): CreditsState {
  if (credits.errorMessage) {
    return { kind: "error", message: credits.errorMessage }
  }
  const hasOpenRouter = providers.some(
    (p) => p.id === "openrouter" && p.configured,
  )
  if (!hasOpenRouter) {
    return {
      kind: "unconfigured",
      message: "Add an OpenRouter API key to enable credit tracking",
    }
  }
  if (credits.balance === null) {
    return {
      kind: "untested",
      message: "Test your OpenRouter key in Providers to fetch balance",
    }
  }
  if (credits.balance <= 0) {
    return {
      kind: "zero",
      message: "$0.00 — Add credits at openrouter.ai/credits",
      amount: credits.balance,
    }
  }
  return { kind: "positive", amount: credits.balance }
}
