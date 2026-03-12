// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { SettingsUpdate } from "./types"

export type PresetId = "casual" | "researcher" | "power-user"
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
}

export const USER_PRESETS: UserPreset[] = [
  {
    id: "casual",
    label: "Casual",
    emoji: "☕",
    description: "Simple chat with basic knowledge lookups — minimal UI clutter.",
    uiMode: "simple",
    settings: {
      enable_feedback_loop: false,
      enable_hallucination_check: false,
      enable_memory_extraction: false,
      enable_model_router: false,
      enable_auto_inject: true,
      auto_inject_threshold: 0.85,
      enable_self_rag: false,
      enable_contextual_chunks: false,
      enable_adaptive_retrieval: false,
      enable_query_decomposition: false,
      enable_mmr_diversity: false,
      enable_late_interaction: false,
      enable_semantic_cache: false,
    },
    local: {
      "cerid-feedback-loop": "false",
      "cerid-hallucination-check": "false",
      "cerid-memory-extraction": "false",
      "cerid-routing-mode": "manual",
      "cerid-auto-inject": "true",
      "cerid-auto-inject-threshold": "0.85",
      "cerid-show-dashboard": "false",
      "cerid-inline-markups": "false",
    },
  },
  {
    id: "researcher",
    label: "Researcher",
    emoji: "🔬",
    description: "Balanced retrieval with verification and memory — recommended for most users.",
    uiMode: "advanced",
    settings: {
      enable_feedback_loop: false,
      enable_hallucination_check: true,
      enable_memory_extraction: true,
      enable_model_router: true,
      enable_auto_inject: true,
      auto_inject_threshold: 0.80,
      enable_self_rag: true,
      enable_contextual_chunks: true,
      enable_adaptive_retrieval: true,
      enable_query_decomposition: true,
      enable_mmr_diversity: true,
      enable_late_interaction: true,
      enable_semantic_cache: true,
    },
    local: {
      "cerid-feedback-loop": "false",
      "cerid-hallucination-check": "true",
      "cerid-memory-extraction": "true",
      "cerid-routing-mode": "recommend",
      "cerid-auto-inject": "true",
      "cerid-auto-inject-threshold": "0.80",
      "cerid-show-dashboard": "false",
      "cerid-inline-markups": "true",
    },
  },
  {
    id: "power-user",
    label: "Power User",
    emoji: "🔧",
    description: "Maximum pipeline features — full feedback loop, auto routing, all analytics.",
    uiMode: "advanced",
    settings: {
      enable_feedback_loop: true,
      enable_hallucination_check: true,
      enable_memory_extraction: true,
      enable_model_router: true,
      enable_auto_inject: true,
      auto_inject_threshold: 0.70,
      enable_self_rag: true,
      enable_contextual_chunks: true,
      enable_adaptive_retrieval: true,
      enable_query_decomposition: true,
      enable_mmr_diversity: true,
      enable_intelligent_assembly: true,
      enable_late_interaction: true,
      enable_semantic_cache: true,
    },
    local: {
      "cerid-feedback-loop": "true",
      "cerid-hallucination-check": "true",
      "cerid-memory-extraction": "true",
      "cerid-routing-mode": "auto",
      "cerid-auto-inject": "true",
      "cerid-auto-inject-threshold": "0.70",
      "cerid-show-dashboard": "true",
      "cerid-inline-markups": "true",
    },
  },
]

export function getPresetById(id: PresetId): UserPreset {
  return USER_PRESETS.find((p) => p.id === id)!
}
