// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  CreditCard,
  Cpu,
  Database,
  Palette,
  Puzzle,
  Server,
  Shield,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react"
import type { CategoryId, SearchMatch, SettingDef, SettingsCtx } from "./types"
import { APPEARANCE_DEFS } from "./appearance"
import { MODELS_DEFS } from "./models"
import { RETRIEVAL_DEFS } from "./retrieval"
import { KNOWLEDGE_DEFS } from "./knowledge"
import { PRIVACY_DEFS } from "./privacy"
import { EXTENSIONS_DEFS } from "./extensions"
import { PLAN_DEFS } from "./plan"
import { SYSTEM_DEFS } from "./system"

export type {
  CategoryId,
  ScopeOfEffect,
  SearchMatch,
  SettingDef,
  SettingsCtx,
  Writer,
} from "./types"

export {
  isSettingModified,
  modifiedSettings,
  modifiedSettingIds,
  settingCurrentValue,
  type ModifiedSetting,
} from "./active"

/** Category display order = sidebar order (frequency descending, dangerous last). */
export const SETTINGS_REGISTRY: SettingDef[] = [
  ...MODELS_DEFS,
  ...KNOWLEDGE_DEFS,
  ...RETRIEVAL_DEFS,
  ...PRIVACY_DEFS,
  ...EXTENSIONS_DEFS,
  ...APPEARANCE_DEFS,
  ...PLAN_DEFS,
  ...SYSTEM_DEFS,
]

export interface CategoryMeta {
  id: CategoryId
  label: string
  description: string
  icon: LucideIcon
}

/** Sidebar order — frequency descending, dangerous (System) last. */
export const CATEGORY_META: CategoryMeta[] = [
  { id: "models", label: "Models", description: "Connect models", icon: Cpu },
  { id: "knowledge", label: "Knowledge", description: "Feed Cerid your sources", icon: Database },
  { id: "retrieval", label: "Retrieval & Answers", description: "Tune how Cerid finds and verifies answers", icon: SlidersHorizontal },
  { id: "privacy", label: "Privacy", description: "Control privacy & data boundaries", icon: Shield },
  { id: "extensions", label: "Extensions", description: "Extend Cerid", icon: Puzzle },
  { id: "appearance", label: "Appearance", description: "Make it yours", icon: Palette },
  { id: "plan", label: "Plan & Billing", description: "Manage your plan", icon: CreditCard },
  { id: "system", label: "System", description: "Operate the server", icon: Server },
]

export function categoryLabel(id: string): string {
  return CATEGORY_META.find((c) => c.id === id)?.label ?? id
}

const BY_ID = new Map(SETTINGS_REGISTRY.map((d) => [d.id, d]))

export function getDef(id: string): SettingDef | undefined {
  return BY_ID.get(id)
}

export function defsForGroup(category: string, group: string): SettingDef[] {
  return SETTINGS_REGISTRY.filter((d) => d.category === category && d.group === group)
}

/**
 * Weighted token-AND substring search (no fuzzy — VS Code's issue history
 * shows fuzzy matching frustrates in settings contexts). Every query token
 * must hit at least one field; fields carry weights, label-prefix matches
 * get a bonus. `visibleWhen`-hidden defs are excluded (non-indexable rule).
 */
export function searchSettings(
  defs: SettingDef[],
  query: string,
  ctx: SettingsCtx,
): SearchMatch[] {
  const tokens = query.toLowerCase().trim().split(/\s+/).filter(Boolean)
  if (tokens.length === 0) return []
  const matches: SearchMatch[] = []
  for (const d of defs) {
    if (d.visibleWhen?.(ctx) === false) continue
    const fields: [string, number][] = [
      [d.label, 4],
      [d.keywords.join(" "), 3],
      [(d.options ?? []).map((o) => o.label).join(" "), 2],
      [d.helpText, 1],
      [d.id, 1],
    ]
    let score = 0
    let missed = false
    for (const t of tokens) {
      const hit = fields.find(([f]) => f.toLowerCase().includes(t))
      if (!hit) {
        missed = true
        break
      }
      score += hit[1] + (d.label.toLowerCase().startsWith(t) ? 2 : 0)
    }
    if (!missed) matches.push({ def: d, score })
  }
  return matches.sort((a, b) => b.score - a.score)
}
