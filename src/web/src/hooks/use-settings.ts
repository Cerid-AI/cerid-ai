import { useState, useCallback } from "react"

export function useSettings() {
  const [feedbackLoop, setFeedbackLoop] = useState(() => {
    try { return localStorage.getItem("cerid-feedback-loop") === "true" } catch { return false }
  })

  const [showDashboard, setShowDashboard] = useState(() => {
    try { return localStorage.getItem("cerid-show-dashboard") === "true" } catch { return false }
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

  return { feedbackLoop, toggleFeedbackLoop, showDashboard, toggleDashboard }
}
