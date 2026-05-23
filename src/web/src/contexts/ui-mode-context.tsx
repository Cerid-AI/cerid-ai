// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Phase C Day 3 — Simple/Advanced mode toggle removed. The Provider
 * now returns a constant "advanced" mode for all consumers, the
 * `setMode` / `toggle` callbacks become no-ops, and `isSimple` is
 * always false. Components that previously gated their UI behind
 * `isSimple` now reveal it unconditionally.
 *
 * The hook + Provider are kept (rather than deleted) so the ~7
 * existing call sites (settings-pane, essentials-section, sidebar,
 * chat-panel, setup-wizard, advanced-mode wrapper, system-section)
 * don't all need touching in one commit. Their useUIMode() reads
 * still succeed, just always see "advanced". Follow-up cleanup
 * removes the imports + the Provider/hook entirely when the
 * cerid-ui-mode localStorage key cleanup ships (tracked for v1.1).
 */

import { createContext, useContext, type ReactNode } from "react"

type UIMode = "simple" | "advanced"

interface UIModeContextType {
  mode: UIMode
  setMode: (m: UIMode) => void
  toggle: () => void
  isSimple: boolean
}

const ALWAYS_ADVANCED: UIModeContextType = {
  mode: "advanced",
  setMode: () => {},
  toggle: () => {},
  isSimple: false,
}

const UIModeContext = createContext<UIModeContextType>(ALWAYS_ADVANCED)

export function UIModeProvider({ children }: { children: ReactNode }) {
  return <UIModeContext value={ALWAYS_ADVANCED}>{children}</UIModeContext>
}

// eslint-disable-next-line react-refresh/only-export-components -- co-located context hook
export function useUIMode(): UIModeContextType {
  return useContext(UIModeContext)
}
