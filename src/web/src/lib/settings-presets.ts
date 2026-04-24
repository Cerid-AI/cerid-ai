// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { SettingsUpdate } from "./types"

export interface SettingsPreset {
  label: string
  description: string
  values: SettingsUpdate
}

export const PRESETS: Record<string, SettingsPreset> = {
  efficient: {
    label: "Efficient",
    description: "Fast retrieval with smart defaults",
    values: {
      enable_self_rag: true,
      enable_hallucination_check: true,
      enable_auto_inject: true,
      enable_adaptive_retrieval: true,
      enable_query_decomposition: true,
      enable_mmr_diversity: true,
      enable_intelligent_assembly: true,
      enable_late_interaction: false,
      enable_semantic_cache: true,
      enable_contextual_chunks: false,
      enable_memory_consolidation: true,
      enable_context_compression: true,
    },
  },
  balanced: {
    label: "Balanced",
    description: "Best balance of quality and speed",
    values: {
      enable_self_rag: true,
      enable_hallucination_check: true,
      enable_auto_inject: true,
      auto_inject_threshold: 0.15,
      enable_adaptive_retrieval: true,
      enable_mmr_diversity: true,
      mmr_lambda: 0.7,
      enable_semantic_cache: true,
      semantic_cache_threshold: 0.92,
      enable_query_decomposition: true,
      enable_intelligent_assembly: true,
      enable_late_interaction: false,
      enable_memory_consolidation: true,
      enable_context_compression: true,
    },
  },
  maximum: {
    label: "Maximum",
    description: "All pipeline stages enabled — requires Pro tier",
    values: {
      enable_self_rag: true,
      enable_hallucination_check: true,
      enable_auto_inject: true,
      auto_inject_threshold: 0.10,
      enable_adaptive_retrieval: true,
      enable_query_decomposition: true,
      query_decomposition_max_subqueries: 4,
      enable_mmr_diversity: true,
      mmr_lambda: 0.7,
      enable_intelligent_assembly: true,
      enable_late_interaction: true,
      late_interaction_top_n: 8,
      late_interaction_blend_weight: 0.15,
      enable_semantic_cache: true,
      semantic_cache_threshold: 0.92,
      enable_memory_consolidation: true,
      enable_context_compression: true,
    },
  },
}

/**
 * Determine which preset (if any) matches the current settings.
 *
 * Match rule: a preset matches when *every* key it explicitly sets equals
 * the same key on ``settings``. When two presets both match, the more
 * specific one (the one that sets more fields) wins. This prevents the
 * pre-2026-04-24 bug where applying *Balanced* left *Efficient* highlighted:
 * Efficient and Balanced share the same boolean toggles, but Efficient
 * explicitly sets ``enable_contextual_chunks: false`` and Balanced sets
 * ``auto_inject_threshold: 0.15`` + ``mmr_lambda: 0.7`` + others. The
 * specificity tiebreaker steers the highlight to whichever preset the
 * user just clicked rather than to whichever the iteration finds first.
 *
 * Returns the preset key or ``null`` if no preset matches exactly.
 */
export function detectActivePreset(
  settings: Record<string, unknown>,
): string | null {
  const matches: { key: string; specificity: number }[] = []
  for (const [key, preset] of Object.entries(PRESETS)) {
    const allValuesMatch = Object.entries(preset.values).every(
      ([k, expected]) => settings[k] === expected,
    )
    if (allValuesMatch) {
      matches.push({ key, specificity: Object.keys(preset.values).length })
    }
  }
  if (matches.length === 0) return null
  matches.sort((a, b) => b.specificity - a.specificity)
  return matches[0].key
}
