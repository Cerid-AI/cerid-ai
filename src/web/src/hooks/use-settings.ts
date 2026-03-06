// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useEffect, useRef } from "react"
import { fetchSettings, updateSettings } from "@/lib/api"
import type { RoutingMode } from "@/lib/types"

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

export function useSettings() {
  const [feedbackLoop, setFeedbackLoop] = useState(() => readBool("cerid-feedback-loop"))
  const [showDashboard, setShowDashboard] = useState(() => readBool("cerid-show-dashboard"))
  const [routingMode, setRoutingModeState] = useState<RoutingMode>(() => {
    try {
      // New key takes precedence
      const v = localStorage.getItem("cerid-routing-mode")
      if (v === "manual" || v === "recommend" || v === "auto") return v
      // Migrate from old boolean key
      const old = localStorage.getItem("cerid-auto-model-switch")
      if (old === "true") return "recommend"
    } catch { /* noop */ }
    return "manual"
  })
  const [autoInject, setAutoInject] = useState(() => readBool("cerid-auto-inject"))
  const [autoInjectThreshold, setAutoInjectThresholdState] = useState(() => readFloat("cerid-auto-inject-threshold", 0.82))
  const [hallucinationEnabled, setHallucinationEnabled] = useState(() => readBool("cerid-hallucination-check"))

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
        if (s.enable_feedback_loop !== undefined) {
          setFeedbackLoop(s.enable_feedback_loop)
          persist("cerid-feedback-loop", String(s.enable_feedback_loop))
        }
        if (s.cost_sensitivity) {
          const v = s.cost_sensitivity as "low" | "medium" | "high"
          if (v === "low" || v === "medium" || v === "high") {
            setCostSensitivity(v)
            persist("cerid-cost-sensitivity", v)
          }
        }
        if (s.enable_auto_inject !== undefined) {
          setAutoInject(s.enable_auto_inject)
          persist("cerid-auto-inject", String(s.enable_auto_inject))
        }
        if (s.auto_inject_threshold !== undefined) {
          setAutoInjectThresholdState(s.auto_inject_threshold)
          persist("cerid-auto-inject-threshold", String(s.auto_inject_threshold))
        }
        if (s.enable_hallucination_check !== undefined) {
          setHallucinationEnabled(s.enable_hallucination_check)
          persist("cerid-hallucination-check", String(s.enable_hallucination_check))
        }
        if (s.enable_model_router !== undefined) {
          // Server bool maps to recommend/manual; preserve "auto" if user set it
          const current = localStorage.getItem("cerid-routing-mode")
          if (current !== "auto") {
            const mode: RoutingMode = s.enable_model_router ? "recommend" : "manual"
            setRoutingModeState(mode)
            persist("cerid-routing-mode", mode)
          }
        }
      })
      .catch(() => { /* Server unavailable — use localStorage values */ })
  }, [])

  const toggleFeedbackLoop = useCallback(() => {
    setFeedbackLoop((prev) => {
      const next = !prev
      persist("cerid-feedback-loop", String(next))
      updateSettings({ enable_feedback_loop: next }).catch(() => { /* noop */ })
      return next
    })
  }, [])

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
    // Sync to server as boolean (manual=off, recommend/auto=on)
    updateSettings({ enable_model_router: mode !== "manual" }).catch(() => { /* noop */ })
  }, [])

  const cycleRoutingMode = useCallback(() => {
    setRoutingModeState((prev) => {
      const next: RoutingMode = prev === "manual" ? "recommend" : prev === "recommend" ? "auto" : "manual"
      persist("cerid-routing-mode", next)
      updateSettings({ enable_model_router: next !== "manual" }).catch(() => { /* noop */ })
      return next
    })
  }, [])

  const toggleAutoInject = useCallback(() => {
    setAutoInject((prev) => {
      const next = !prev
      persist("cerid-auto-inject", String(next))
      updateSettings({ enable_auto_inject: next }).catch(() => { /* noop */ })
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

  const toggleHallucinationEnabled = useCallback(() => {
    setHallucinationEnabled((prev) => {
      const next = !prev
      persist("cerid-hallucination-check", String(next))
      updateSettings({ enable_hallucination_check: next }).catch(() => { /* noop */ })
      return next
    })
  }, [])

  return {
    feedbackLoop, toggleFeedbackLoop,
    showDashboard, toggleDashboard,
    routingMode, setRoutingMode, cycleRoutingMode,
    autoInject, toggleAutoInject,
    autoInjectThreshold, setAutoInjectThreshold,
    costSensitivity, updateCostSensitivity,
    hallucinationEnabled, toggleHallucinationEnabled,
  }
}
