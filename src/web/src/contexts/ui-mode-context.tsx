// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { createContext, useContext, useState, useCallback, type ReactNode } from "react"
import { syncPreferences } from "@/lib/api"

type UIMode = "simple" | "advanced"

interface UIModeContextType {
  mode: UIMode
  setMode: (m: UIMode) => void
  toggle: () => void
  isSimple: boolean
}

const UIModeContext = createContext<UIModeContextType | null>(null)

function readMode(): UIMode {
  try {
    const v = localStorage.getItem("cerid-ui-mode")
    if (v === "simple" || v === "advanced") return v
    // Default: simple for new users (no onboarding complete), advanced for returning
    const onboarded = localStorage.getItem("cerid-onboarding-complete")
    return onboarded ? "advanced" : "simple"
  } catch {
    return "simple"
  }
}

export function UIModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<UIMode>(readMode)

  const setMode = useCallback((m: UIMode) => {
    setModeState(m)
    try { localStorage.setItem("cerid-ui-mode", m) } catch { /* noop */ }
    syncPreferences({ ui_mode: m }).catch(() => { /* fire-and-forget */ })
  }, [])

  const toggle = useCallback(() => {
    setModeState((prev) => {
      const next = prev === "simple" ? "advanced" : "simple"
      try { localStorage.setItem("cerid-ui-mode", next) } catch { /* noop */ }
      syncPreferences({ ui_mode: next }).catch(() => { /* fire-and-forget */ })
      return next
    })
  }, [])

  return (
    <UIModeContext value={{ mode, setMode, toggle, isSimple: mode === "simple" }}>
      {children}
    </UIModeContext>
  )
}

export function useUIMode(): UIModeContextType {
  const ctx = useContext(UIModeContext)
  if (!ctx) throw new Error("useUIMode must be used within UIModeProvider")
  return ctx
}
