import { useState, useCallback, useEffect, useRef } from "react"
import { fetchSettings, updateSettings } from "@/lib/api"

export function useSettings() {
  const [feedbackLoop, setFeedbackLoop] = useState(() => {
    try { return localStorage.getItem("cerid-feedback-loop") === "true" } catch { return false }
  })

  const [showDashboard, setShowDashboard] = useState(() => {
    try { return localStorage.getItem("cerid-show-dashboard") === "true" } catch { return false }
  })

  const [autoModelSwitch, setAutoModelSwitch] = useState(() => {
    try { return localStorage.getItem("cerid-auto-model-switch") === "true" } catch { return false }
  })

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
          try { localStorage.setItem("cerid-feedback-loop", String(s.enable_feedback_loop)) } catch { /* noop */ }
        }
        if (s.cost_sensitivity) {
          const v = s.cost_sensitivity as "low" | "medium" | "high"
          if (v === "low" || v === "medium" || v === "high") {
            setCostSensitivity(v)
            try { localStorage.setItem("cerid-cost-sensitivity", v) } catch { /* noop */ }
          }
        }
      })
      .catch(() => { /* Server unavailable — use localStorage values */ })
  }, [])

  const toggleFeedbackLoop = useCallback(() => {
    setFeedbackLoop((prev) => {
      const next = !prev
      try { localStorage.setItem("cerid-feedback-loop", String(next)) } catch { /* noop */ }
      updateSettings({ enable_feedback_loop: next }).catch(() => { /* noop */ })
      return next
    })
  }, [])

  const toggleDashboard = useCallback(() => {
    setShowDashboard((prev) => {
      const next = !prev
      try { localStorage.setItem("cerid-show-dashboard", String(next)) } catch { /* noop */ }
      return next
    })
  }, [])

  const toggleAutoModelSwitch = useCallback(() => {
    setAutoModelSwitch((prev) => {
      const next = !prev
      try { localStorage.setItem("cerid-auto-model-switch", String(next)) } catch { /* noop */ }
      return next
    })
  }, [])

  const updateCostSensitivity = useCallback((value: "low" | "medium" | "high") => {
    setCostSensitivity(value)
    try { localStorage.setItem("cerid-cost-sensitivity", value) } catch { /* noop */ }
    updateSettings({ cost_sensitivity: value }).catch(() => { /* noop */ })
  }, [])

  return {
    feedbackLoop, toggleFeedbackLoop,
    showDashboard, toggleDashboard,
    autoModelSwitch, toggleAutoModelSwitch,
    costSensitivity, updateCostSensitivity,
  }
}
