// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import type { SettingsUpdate } from "./types"
import { Crown, FlaskConical, Shield, Sparkles, Zap, type LucideIcon } from "lucide-react"

export type PresetId = "quick" | "balanced" | "maximum" | "privacy_first" | "power_user"

/**
 * Per-automation preset configuration. Applied alongside the server
 * settings + localStorage overrides when a preset is selected.
 *
 * `enabled` flips the per-feature runtime gate (Redis-backed via
 * `/settings/pro-automations`). `schedule` is a cron expression —
 * empty disables the scheduler job entirely.
 */
export interface AutomationPreset {
  feature: "inbox_triage" | "daily_digest"
  enabled: boolean
  schedule: string
}

export interface UserPreset {
  id: PresetId
  label: string
  /** Lucide icon component. Replaces the prior emoji string field — emoji
   *  characters render at unpredictable sizes across platforms and were
   *  flagged by the UI audit as the emoji-as-icon anti-pattern. */
  Icon: LucideIcon
  description: string
  settings: SettingsUpdate
  /** localStorage overrides applied alongside the server settings */
  local: Record<string, string>
  /** When true, this preset requires Pro tier to activate */
  requiresPro?: boolean
  /** Per-automation defaults applied via /settings/pro-automations.
   *  Optional — presets that don't touch automations leave existing
   *  schedules in place. */
  automations?: AutomationPreset[]
}

export const USER_PRESETS: UserPreset[] = [
  {
    id: "quick",
    label: "Quick",
    Icon: Zap,
    description: "Fast responses with basic verification. Best for quick questions.",
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
    Icon: FlaskConical,
    description: "Thorough retrieval with full verification pipeline.",
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
    id: "privacy_first",
    label: "Privacy-first",
    Icon: Shield,
    description: "Hallucination guard on. All automations off. iMessage and cloud connectors stay disabled until you opt in per source.",
    settings: {
      enable_feedback_loop: false,
      enable_hallucination_check: true,
      enable_memory_extraction: false,
      enable_model_router: false,
      enable_auto_inject: false,
    },
    local: {
      "cerid-feedback-loop": "false",
      "cerid-hallucination-check": "true",
      "cerid-memory-extraction": "false",
      "cerid-routing-mode": "manual",
      "cerid-auto-inject": "false",
      "cerid-show-dashboard": "false",
      "cerid-inline-markups": "false",
    },
    automations: [
      { feature: "inbox_triage", enabled: false, schedule: "" },
      { feature: "daily_digest", enabled: false, schedule: "" },
    ],
  },
  {
    id: "power_user",
    label: "Power-user",
    Icon: Crown,
    description: "Pro-tier full automation. Daily digest at 7 AM UTC, inbox triage every 15 minutes, full retrieval pipeline.",
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
    automations: [
      { feature: "inbox_triage", enabled: true, schedule: "*/15 * * * *" },
      { feature: "daily_digest", enabled: true, schedule: "0 7 * * *" },
    ],
  },
  {
    id: "maximum",
    label: "Maximum",
    Icon: Sparkles,
    description: "All features enabled. Maximum quality, higher latency.",
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
