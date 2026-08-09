// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * U-1 settings-scoped Simple | Advanced mode. Persisted per device as
 * `cerid-settings-mode`; default "simple".
 *
 * Consumption discipline (binding, from the design authority): the mode is
 * consumed ONLY by `AdvancedDisclosure` default state — simple ⇒ expanders
 * default collapsed, advanced ⇒ default open. No other conditional may read
 * it; nothing relocates or unmounts. Search hits and `?setting=` deep links
 * force-open the containing expander in either mode.
 */

import { useSyncExternalStore } from "react"
import { logSwallowedError } from "@/lib/log-swallowed"

export type SettingsMode = "simple" | "advanced"

const KEY = "cerid-settings-mode"
const listeners = new Set<() => void>()

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

export function getSettingsMode(): SettingsMode {
  try {
    return localStorage.getItem(KEY) === "advanced" ? "advanced" : "simple"
  } catch {
    return "simple"
  }
}

export function setSettingsMode(mode: SettingsMode) {
  try {
    localStorage.setItem(KEY, mode)
  } catch (err) {
    logSwallowedError(err, "localStorage.setItem", { key: KEY })
  }
  for (const l of listeners) l()
}

export function useSettingsMode(): SettingsMode {
  return useSyncExternalStore(subscribe, getSettingsMode, getSettingsMode)
}
