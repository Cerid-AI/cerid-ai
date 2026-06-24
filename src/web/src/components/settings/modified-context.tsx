// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Modified-settings channel (ST2). The shell derives the set of setting ids
 * whose live value differs from its registry default (`modifiedSettingIds`)
 * and publishes it here, alongside an optional `reset(def)` that writes the
 * default back. `SettingRow` consumes both to flag a changed control and
 * offer a one-click reset — making "what have I changed?" visible in place,
 * the row-level complement to the Overview's active-configuration summary.
 */

import { createContext, useContext, type ReactNode } from "react"
import type { SettingDef } from "@/lib/settings-registry"

export interface ModifiedSettingsValue {
  ids: Set<string>
  /** Reset a single setting to its registry default. */
  reset?: (def: SettingDef) => void | Promise<void>
}

const EMPTY: ModifiedSettingsValue = { ids: new Set() }
const ModifiedContext = createContext<ModifiedSettingsValue>(EMPTY)

export function ModifiedSettingsProvider({
  value,
  children,
}: {
  value: ModifiedSettingsValue
  children: ReactNode
}) {
  return <ModifiedContext value={value}>{children}</ModifiedContext>
}

// eslint-disable-next-line react-refresh/only-export-components -- co-located context hook
export function useIsModified(id: string): boolean {
  return useContext(ModifiedContext).ids.has(id)
}

// eslint-disable-next-line react-refresh/only-export-components -- co-located context hook
export function useResetSetting(): ModifiedSettingsValue["reset"] {
  return useContext(ModifiedContext).reset
}
