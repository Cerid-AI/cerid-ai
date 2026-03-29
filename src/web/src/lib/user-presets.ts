// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { SettingsUpdate } from "./types"

export type PresetId = "quick" | "balanced" | "maximum"
export type UIMode = "simple" | "advanced"

export interface UserPreset {
  id: PresetId
  label: string
  emoji: string
  description: string
  uiMode: UIMode
  settings: SettingsUpdate
  /** localStorage overrides applied alongside the server settings */
  local: Record<string, string>
  /** When true, this preset requires Pro tier to activate */
  requiresPro?: boolean
}

export const USER_PRESETS: UserPreset[] = [
  {
    id: "quick",
    label: "Quick",
    emoji: "⚡",
    description: "Fast responses with basic verification. Best for quick questions.",
    uiMode: "simple",
    settings: {
      enable_feedback_loop: false,
      enable_hallucination_check: true,
      enable_memory_extraction: false,
      enable_model_router: false,
      enable_auto_inject: true,
      auto_inject_threshold: 0.15,
      enable_self_rag: true,
      enable_contextual_chunks: false,
      enable_adaptive_retrieval: false,
      enable_query_decomposition: false,
      enable_mmr_diversity: false,
      enable_late_interaction: false,
      enable_semantic_cache: false,
    },
    local: {
      "cerid-feedback-loop": "false",
      "cerid-hallucination-check": "true",
      "cerid-memory-extraction": "false",
      "cerid-routing-mode": "manual",
      "cerid-auto-inject": "true",
      "cerid-auto-inject-threshold": "0.15",
      "cerid-show-dashboard": "false",
      "cerid-inline-markups": "false",
    },
  },
  {
    id: "balanced",
    label: "Balanced",
    emoji: "🔬",
    description: "Thorough retrieval with full verification pipeline.",
    uiMode: "advanced",
    settings: {
      enable_feedback_loop: false,
      enable_hallucination_check: true,
      enable_memory_extraction: true,
      enable_model_router: true,
      enable_auto_inject: true,
      auto_inject_threshold: 0.15,
      enable_self_rag: true,
      enable_contextual_chunks: true,
      enable_adaptive_retrieval: true,
      enable_query_decomposition: true,
      enable_mmr_diversity: true,
      enable_late_interaction: true,
      enable_semantic_cache: true,
      enable_memory_consolidation: true,
      enable_context_compression: true,
    },
    local: {
      "cerid-feedback-loop": "false",
      "cerid-hallucination-check": "true",
      "cerid-memory-extraction": "true",
      "cerid-routing-mode": "recommend",
      "cerid-auto-inject": "true",
      "cerid-auto-inject-threshold": "0.15",
      "cerid-show-dashboard": "false",
      "cerid-inline-markups": "true",
    },
  },
  {
    id: "maximum",
    label: "Maximum",
    emoji: "🔧",
    description: "All features enabled. Maximum quality, higher latency.",
    uiMode: "advanced",
    requiresPro: true,
    settings: {
      enable_feedback_loop: true,
      enable_hallucination_check: true,
      enable_memory_extraction: true,
      enable_model_router: true,
      enable_auto_inject: true,
      auto_inject_threshold: 0.10,
      enable_self_rag: true,
      enable_contextual_chunks: true,
      enable_adaptive_retrieval: true,
      enable_query_decomposition: true,
      enable_mmr_diversity: true,
      enable_intelligent_assembly: true,
      enable_late_interaction: true,
      enable_semantic_cache: true,
      enable_memory_consolidation: true,
      enable_context_compression: true,
    },
    local: {
      "cerid-feedback-loop": "true",
      "cerid-hallucination-check": "true",
      "cerid-memory-extraction": "true",
      "cerid-routing-mode": "auto",
      "cerid-auto-inject": "true",
      "cerid-auto-inject-threshold": "0.10",
      "cerid-show-dashboard": "true",
      "cerid-inline-markups": "true",
    },
  },
]

export function getPresetById(id: PresetId): UserPreset {
  return USER_PRESETS.find((p) => p.id === id)!
}
