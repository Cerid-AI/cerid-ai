// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { SettingsUpdate } from "./types"

export interface SettingsPreset {
  label: string
  description: string
  values: SettingsUpdate
}

export const PRESETS: Record<string, SettingsPreset> = {
  essential: {
    label: "Balanced",
    description: "Smart defaults — good quality with reasonable speed",
    values: {
      enable_self_rag: true,
      enable_hallucination_check: true,
      enable_auto_inject: false,
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
  recommended: {
    label: "Recommended",
    description: "Best balance of quality and speed",
    values: {
      enable_self_rag: true,
      enable_hallucination_check: true,
      enable_auto_inject: true,
      auto_inject_threshold: 0.82,
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
    label: "Maximum Quality",
    description: "All pipeline stages — best results, higher latency",
    values: {
      enable_self_rag: true,
      enable_hallucination_check: true,
      enable_auto_inject: true,
      auto_inject_threshold: 0.75,
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

/** Keys checked when determining which preset is active. */
const PRESET_TOGGLE_KEYS = [
  "enable_self_rag",
  "enable_hallucination_check",
  "enable_auto_inject",
  "enable_adaptive_retrieval",
  "enable_query_decomposition",
  "enable_mmr_diversity",
  "enable_intelligent_assembly",
  "enable_late_interaction",
  "enable_semantic_cache",
  "enable_contextual_chunks",
  "enable_memory_consolidation",
  "enable_context_compression",
] as const

/**
 * Determine which preset (if any) matches the current settings.
 * Returns the preset key or `null` if settings are custom.
 */
export function detectActivePreset(
  settings: Record<string, unknown>,
): string | null {
  for (const [key, preset] of Object.entries(PRESETS)) {
    const match = PRESET_TOGGLE_KEYS.every((k) => {
      const expected = preset.values[k as keyof SettingsUpdate]
      if (expected === undefined) return true
      return settings[k] === expected
    })
    if (match) return key
  }
  return null
}
