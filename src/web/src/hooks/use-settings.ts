// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useEffect, useRef } from "react"
import { fetchSettings, updateSettings } from "@/lib/api"

function readBool(key: string): boolean {
  try { return localStorage.getItem(key) === "true" } catch { return false }
}

function persist(key: string, value: string): void {
  try { localStorage.setItem(key, value) } catch { /* noop */ }
}

export function useSettings() {
  const [feedbackLoop, setFeedbackLoop] = useState(() => readBool("cerid-feedback-loop"))
  const [showDashboard, setShowDashboard] = useState(() => readBool("cerid-show-dashboard"))
  const [autoModelSwitch, setAutoModelSwitch] = useState(() => readBool("cerid-auto-model-switch"))

  const [costSensitivity, setCostSensitivity] = useState<"low" | "medium" | "high">(() => {
    try {
      const v = localStorage.getItem("cerid-cost-sensitivity")
      return v === "low" || v === "high" ? v : "medium"
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

  const toggleAutoModelSwitch = useCallback(() => {
    setAutoModelSwitch((prev) => {
      const next = !prev
      persist("cerid-auto-model-switch", String(next))
      return next
    })
  }, [])

  const updateCostSensitivity = useCallback((value: "low" | "medium" | "high") => {
    setCostSensitivity(value)
    persist("cerid-cost-sensitivity", value)
    updateSettings({ cost_sensitivity: value }).catch(() => { /* noop */ })
  }, [])

  return {
    feedbackLoop, toggleFeedbackLoop,
    showDashboard, toggleDashboard,
    autoModelSwitch, toggleAutoModelSwitch,
    costSensitivity, updateCostSensitivity,
  }
}
