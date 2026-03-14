// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useEffect, useRef } from "react"
import { fetchSettings, updateSettings, syncPreferences, fetchUserState } from "@/lib/api"
import type { RoutingMode, SettingsUpdate } from "@/lib/types"

function readBool(key: string): boolean {
  try { return localStorage.getItem(key) === "true" } catch { return false }
}

function readFloat(key: string, fallback: number): number {
  try {
    const v = localStorage.getItem(key)
    if (v !== null) { const n = parseFloat(v); if (!isNaN(n)) return n }
  } catch { /* noop */ }
  return fallback
}

function persist(key: string, value: string): void {
  try { localStorage.setItem(key, value) } catch { /* noop */ }
}

/** Boolean setting with localStorage persistence + server sync. */
function useSyncedToggle(
  localKey: string,
  serverKey: keyof SettingsUpdate,
): [boolean, () => void, (v: boolean) => void] {
  const [value, setValue] = useState(() => readBool(localKey))

  const toggle = useCallback(() => {
    setValue((prev) => {
      const next = !prev
      persist(localKey, String(next))
      updateSettings({ [serverKey]: next }).catch(() => { /* noop */ })
      return next
    })
  }, [localKey, serverKey])

  /**
   * Accept value from server hydration — but ONLY if localStorage has no
   * value for this key yet (first-time setup).  Once the user has toggled a
   * setting (or a prior hydration wrote it), localStorage IS the source of
   * truth because `toggle()` syncs to the server with fire-and-forget
   * semantics: if that `updateSettings()` call failed the server is stale,
   * and hydrating from the stale server value would overwrite the user's
   * explicit intent.
   */
  const hydrate = useCallback((v: boolean) => {
    try {
      if (localStorage.getItem(localKey) !== null) return
    } catch { /* noop */ }
    setValue(v)
    persist(localKey, String(v))
  }, [localKey])

  return [value, toggle, hydrate]
}

export function useSettings() {
  const [feedbackLoop, toggleFeedbackLoop, hydrateFeedback] = useSyncedToggle(
    "cerid-feedback-loop", "enable_feedback_loop",
  )
  const [autoInject, toggleAutoInject, hydrateAutoInject] = useSyncedToggle(
    "cerid-auto-inject", "enable_auto_inject",
  )
  const [hallucinationEnabled, toggleHallucinationEnabled, hydrateHallucination] = useSyncedToggle(
    "cerid-hallucination-check", "enable_hallucination_check",
  )
  const [memoryExtraction, toggleMemoryExtraction, hydrateMemory] = useSyncedToggle(
    "cerid-memory-extraction", "enable_memory_extraction",
  )

  const [showDashboard, setShowDashboard] = useState(() => readBool("cerid-show-dashboard"))
  const [routingMode, setRoutingModeState] = useState<RoutingMode>(() => {
    try {
      const v = localStorage.getItem("cerid-routing-mode")
      if (v === "manual" || v === "recommend" || v === "auto") return v
      const old = localStorage.getItem("cerid-auto-model-switch")
      if (old === "true") return "recommend"
    } catch { /* noop */ }
    return "manual"
  })
  const [autoInjectThreshold, setAutoInjectThresholdState] = useState(() => readFloat("cerid-auto-inject-threshold", 0.82))

  const [inlineMarkups, setInlineMarkupsState] = useState(() => {
    try { const v = localStorage.getItem("cerid-inline-markups"); return v === null ? true : v === "true" } catch { return true }
  })
  const toggleInlineMarkups = useCallback(() => {
    setInlineMarkupsState((prev) => {
      const next = !prev
      persist("cerid-inline-markups", String(next))
      syncPreferences({ inline_markups: next }).catch(() => { /* fire-and-forget */ })
      return next
    })
  }, [])

  const [expertVerification, setExpertVerificationState] = useState(() => readBool("cerid-expert-verification"))
  const toggleExpertVerification = useCallback(() => {
    setExpertVerificationState((prev) => {
      const next = !prev
      persist("cerid-expert-verification", String(next))
      syncPreferences({ expert_verification: next }).catch(() => { /* fire-and-forget */ })
      return next
    })
  }, [])

  const [costSensitivity, setCostSensitivity] = useState<"low" | "medium" | "high">(() => {
    try {
      const v = localStorage.getItem("cerid-cost-sensitivity")
      return v === "low" || v === "medium" || v === "high" ? v : "medium"
    } catch { return "medium" }
  })

  // Hydrate from server on mount (non-blocking, localStorage is immediate fallback)
  const hydratedRef = useRef(false)
  useEffect(() => {
    if (hydratedRef.current) return
    hydratedRef.current = true
    fetchSettings()
      .then((s) => {
        if (s.enable_feedback_loop !== undefined) hydrateFeedback(s.enable_feedback_loop)
        if (s.cost_sensitivity) {
          const v = s.cost_sensitivity as "low" | "medium" | "high"
          if (v === "low" || v === "medium" || v === "high") {
            setCostSensitivity(v)
            persist("cerid-cost-sensitivity", v)
          }
        }
        if (s.enable_auto_inject !== undefined) hydrateAutoInject(s.enable_auto_inject)
        if (s.auto_inject_threshold !== undefined) {
          setAutoInjectThresholdState(s.auto_inject_threshold)
          persist("cerid-auto-inject-threshold", String(s.auto_inject_threshold))
        }
        if (s.enable_hallucination_check !== undefined) hydrateHallucination(s.enable_hallucination_check)
        if (s.enable_memory_extraction !== undefined) hydrateMemory(s.enable_memory_extraction)
        if (s.enable_model_router !== undefined) {
          const current = localStorage.getItem("cerid-routing-mode")
          if (current !== "auto") {
            const mode: RoutingMode = s.enable_model_router ? "recommend" : "manual"
            setRoutingModeState(mode)
            persist("cerid-routing-mode", mode)
          }
        }

        // Reconcile: push local boolean toggle values back to server when
        // they disagree.  This fixes stale server state left by previous
        // fire-and-forget updateSettings() failures.
        const reconcile: SettingsUpdate = {}
        const check = (localKey: string, serverVal: boolean | undefined, assign: (r: SettingsUpdate, v: boolean) => void) => {
          try {
            const stored = localStorage.getItem(localKey)
            if (stored !== null && serverVal !== undefined && (stored === "true") !== serverVal) {
              assign(reconcile, stored === "true")
            }
          } catch { /* noop */ }
        }
        check("cerid-feedback-loop", s.enable_feedback_loop, (r, v) => { r.enable_feedback_loop = v })
        check("cerid-auto-inject", s.enable_auto_inject, (r, v) => { r.enable_auto_inject = v })
        check("cerid-hallucination-check", s.enable_hallucination_check, (r, v) => { r.enable_hallucination_check = v })
        check("cerid-memory-extraction", s.enable_memory_extraction, (r, v) => { r.enable_memory_extraction = v })
        if (Object.keys(reconcile).length > 0) {
          updateSettings(reconcile).catch(() => { /* best-effort */ })
        }
      })
      .catch(() => { /* Server unavailable — use localStorage values */ })

    // Hydrate UI preferences from cloud sync
    fetchUserState()
      .then((state) => {
        if (!state.preferences) return
        const p = state.preferences as Record<string, unknown>
        if (p.routing_mode && !localStorage.getItem("cerid-routing-mode")) {
          const m = p.routing_mode as string
          if (m === "manual" || m === "recommend" || m === "auto") {
            setRoutingModeState(m as RoutingMode)
            persist("cerid-routing-mode", m)
          }
        }
        if (p.expert_verification !== undefined && localStorage.getItem("cerid-expert-verification") === null) {
          const v = Boolean(p.expert_verification)
          setExpertVerificationState(v)
          persist("cerid-expert-verification", String(v))
        }
        if (p.inline_markups !== undefined && localStorage.getItem("cerid-inline-markups") === null) {
          const v = Boolean(p.inline_markups)
          setInlineMarkupsState(v)
          persist("cerid-inline-markups", String(v))
        }
      })
      .catch(() => { /* noop */ })
  }, [hydrateFeedback, hydrateAutoInject, hydrateHallucination, hydrateMemory])

  const toggleDashboard = useCallback(() => {
    setShowDashboard((prev) => {
      const next = !prev
      persist("cerid-show-dashboard", String(next))
      return next
    })
  }, [])

  const setRoutingMode = useCallback((mode: RoutingMode) => {
    setRoutingModeState(mode)
    persist("cerid-routing-mode", mode)
    updateSettings({ enable_model_router: mode !== "manual" }).catch(() => { /* noop */ })
    syncPreferences({ routing_mode: mode }).catch(() => { /* fire-and-forget */ })
  }, [])

  const cycleRoutingMode = useCallback(() => {
    setRoutingModeState((prev) => {
      const next: RoutingMode = prev === "manual" ? "recommend" : prev === "recommend" ? "auto" : "manual"
      persist("cerid-routing-mode", next)
      updateSettings({ enable_model_router: next !== "manual" }).catch(() => { /* noop */ })
      syncPreferences({ routing_mode: next }).catch(() => { /* fire-and-forget */ })
      return next
    })
  }, [])

  const setAutoInjectThreshold = useCallback((value: number) => {
    setAutoInjectThresholdState(value)
    persist("cerid-auto-inject-threshold", String(value))
    updateSettings({ auto_inject_threshold: value }).catch(() => { /* noop */ })
  }, [])

  const updateCostSensitivity = useCallback((value: "low" | "medium" | "high") => {
    setCostSensitivity(value)
    persist("cerid-cost-sensitivity", value)
    updateSettings({ cost_sensitivity: value }).catch(() => { /* noop */ })
  }, [])

  return {
    feedbackLoop, toggleFeedbackLoop,
    showDashboard, toggleDashboard,
    routingMode, setRoutingMode, cycleRoutingMode,
    autoInject, toggleAutoInject,
    autoInjectThreshold, setAutoInjectThreshold,
    costSensitivity, updateCostSensitivity,
    hallucinationEnabled, toggleHallucinationEnabled,
    memoryExtraction, toggleMemoryExtraction,
    inlineMarkups, toggleInlineMarkups,
    expertVerification, toggleExpertVerification,
  }
}
