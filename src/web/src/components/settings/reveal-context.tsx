// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Reveal channel for `?setting=` deep links and search-result clicks.
 *
 * The shell publishes a target setting id (+ monotonic nonce so repeat
 * reveals of the same id still fire). Consumers:
 *  - `AdvancedDisclosure` force-opens when the target id is inside it,
 *    in either Simple or Advanced mode (a hidden match is a broken promise).
 *  - `SettingRow` scrolls itself to center and flashes a transient ring
 *    when it IS the target.
 */

import { createContext, useContext, type ReactNode } from "react"

export interface RevealTarget {
  id: string
  nonce: number
}

const RevealContext = createContext<RevealTarget | null>(null)

export function SettingsRevealProvider({
  target,
  children,
}: {
  target: RevealTarget | null
  children: ReactNode
}) {
  return <RevealContext value={target}>{children}</RevealContext>
}

// eslint-disable-next-line react-refresh/only-export-components -- co-located context hook
export function useSettingsReveal(): RevealTarget | null {
  return useContext(RevealContext)
}
