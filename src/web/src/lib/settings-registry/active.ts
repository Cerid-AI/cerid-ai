// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * "Active configuration" derivation for the Settings Overview (ST1/ST2).
 *
 * A setting counts as *modified* when its registry def declares a `default`,
 * writes through `settings-patch` (so its `writer.key` names a live
 * `ServerSettings` field), and the server value differs from that default.
 * Only `settings-patch` writers are considered — `preferences` / `local` /
 * `env` / `endpoint` / `readonly` writers don't expose a comparable
 * server-side default through the settings object.
 */

import { SETTINGS_REGISTRY } from "./index"
import type { SettingDef, SettingsCtx } from "./types"

type ServerSettingsLike = Record<string, unknown> | null | undefined

export interface ModifiedSetting {
  def: SettingDef
  current: unknown
  default: unknown
}

/** Live value for a def's `settings-patch` key, or `undefined` when absent. */
export function settingCurrentValue(def: SettingDef, settings: ServerSettingsLike): unknown {
  if (!settings || def.writer.kind !== "settings-patch") return undefined
  return settings[def.writer.key]
}

function valuesEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true
  if (typeof a === "object" && typeof b === "object" && a !== null && b !== null) {
    return JSON.stringify(a) === JSON.stringify(b)
  }
  return false
}

/**
 * True when a `settings-patch` def has a `default` and the server value is
 * present and differs from it. Absent server key ⇒ not modified (unknown ≠
 * changed); a def without a `default` can never be flagged.
 */
export function isSettingModified(def: SettingDef, settings: ServerSettingsLike): boolean {
  if (def.writer.kind !== "settings-patch" || def.default === undefined) return false
  const current = settingCurrentValue(def, settings)
  if (current === undefined) return false
  return !valuesEqual(current, def.default)
}

/**
 * The modified defs (current + default), in registry order. `ctx` (when
 * given) drops `visibleWhen`-hidden defs so the summary never surfaces a
 * setting the active tier can't see.
 */
export function modifiedSettings(settings: ServerSettingsLike, ctx?: SettingsCtx): ModifiedSetting[] {
  const out: ModifiedSetting[] = []
  for (const def of SETTINGS_REGISTRY) {
    if (ctx && def.visibleWhen?.(ctx) === false) continue
    if (!isSettingModified(def, settings)) continue
    out.push({ def, current: settingCurrentValue(def, settings), default: def.default })
  }
  return out
}

export function modifiedSettingIds(settings: ServerSettingsLike, ctx?: SettingsCtx): Set<string> {
  return new Set(modifiedSettings(settings, ctx).map((m) => m.def.id))
}
