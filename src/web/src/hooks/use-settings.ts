import { useState, useCallback } from "react"

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

  const toggleFeedbackLoop = useCallback(() => {
    setFeedbackLoop((prev) => {
      const next = !prev
      try { localStorage.setItem("cerid-feedback-loop", String(next)) } catch { /* noop */ }
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
  }, [])

  return {
    feedbackLoop, toggleFeedbackLoop,
    showDashboard, toggleDashboard,
    autoModelSwitch, toggleAutoModelSwitch,
    costSensitivity, updateCostSensitivity,
  }
}
