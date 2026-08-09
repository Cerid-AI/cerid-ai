// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Settings registry — single source of truth for every control rendered
 * under `components/settings/`. One `SettingDef` per control drives the
 * row rendering (label / help / scope badge / env hint / lock state),
 * the Info popover, the search index, and the `?setting=` deep-link
 * anchors. Layout stays in JSX; control semantics live here.
 *
 * Contract for category authors: see
 * `tasks/2026-06-10-settings-contract.md`.
 */

export type CategoryId =
  | "models"
  | "knowledge"
  | "retrieval"
  | "privacy"
  | "extensions"
  | "appearance"
  | "plan"
  | "system"

export type Writer =
  | { kind: "settings-patch"; key: string }
  | { kind: "preferences"; key: string }
  | { kind: "endpoint"; method: "PUT" | "POST" | "DELETE"; path: string }
  | { kind: "local"; storageKey: string }
  | { kind: "env"; envVar: string }
  | { kind: "readonly"; endpoint?: string }

/**
 * Where a change lands (J-5). `scope` is the typed enum used for badge
 * styling and tests; `display` is the human sentence rendered on the row
 * (e.g. "Global for this server — all tabs and sessions").
 */
export interface ScopeOfEffect {
  scope: "device" | "server" | "synced" | "env"
  display: string
}

/** Runtime context handed to `visibleWhen` / `searchSettings`. */
export interface SettingsCtx {
  tier: "community" | "pro" | "enterprise"
  /** Raw server settings object, when loaded. */
  serverSettings?: Record<string, unknown> | null
}

export interface SettingDef<T = unknown> {
  /** Stable dotted id, e.g. "retrieval.injection.autoInject". Doubles as the
      scroll anchor and the `?setting=` deep-link target. Must start with the
      category id. */
  id: string
  category: CategoryId
  group: string
  level: "core" | "advanced"
  label: string
  /** Renders inline under the label AND in the Info popover; searchable. */
  helpText: string
  scopeOfEffect: ScopeOfEffect
  /** Synonyms not already in label/help. Old tab/section names are
      MANDATORY for migrated controls (muscle-memory degradation). */
  keywords: string[]
  type: "boolean" | "enum" | "number" | "string" | "action" | "display"
  options?: { value: T; label: string; helpText?: string }[]
  /** Shown when overridden; enables "Reset to default". */
  default?: T
  writer: Writer
  /** Mirrors the server `@require_feature` flag; server stays authority. */
  entitlement?: "pro" | "enterprise"
  /** Server flag name, for distinct "flag off" messaging. */
  featureFlag?: string
  /** Nests/disables this row under another setting's value. */
  dependsOn?: { id: string; equals: unknown }
  /** Proportional destructive friction (ConfirmActionButton tier). */
  danger?: "confirm" | "type-to-confirm"
  /** Hidden ⇒ also unsearchable (Android non-indexable rule). */
  visibleWhen?: (ctx: SettingsCtx) => boolean
  /** J-5 — another surface also writes this value; renders "Also set by X". */
  writtenBy?: string
  /** J-5 — declared out-of-pane copies (e.g. "sidebar-footer", "chat-toolbar")
      for the v1.1 useSetting(id) mirror unification. */
  mirrors?: string[]
}

export interface SearchMatch {
  def: SettingDef
  score: number
}
