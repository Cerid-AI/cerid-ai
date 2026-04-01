// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback } from "react"
import type { Theme } from "@/lib/types"

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => {
    try {
      const stored = localStorage.getItem("cerid-theme")
      if (stored === "dark" || stored === "light") return stored
    } catch { /* localStorage unavailable */ }
    try {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
    } catch { return "dark" }
  })

  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle("dark", theme === "dark")
    try { localStorage.setItem("cerid-theme", theme) } catch { /* noop */ }
  }, [theme])

  const toggleTheme = useCallback(() => {
    setThemeState((t) => (t === "dark" ? "light" : "dark"))
  }, [])

  return { theme, toggleTheme }
}