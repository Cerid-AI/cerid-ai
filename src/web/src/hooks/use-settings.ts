import { useState, useCallback } from "react"

export function useSettings() {
  const [feedbackLoop, setFeedbackLoop] = useState(
    () => localStorage.getItem("cerid-feedback-loop") === "true",
  )

  const [showDashboard, setShowDashboard] = useState(
    () => localStorage.getItem("cerid-show-dashboard") === "true",
  )

  const toggleFeedbackLoop = useCallback(() => {
    setFeedbackLoop((prev) => {
      const next = !prev
      localStorage.setItem("cerid-feedback-loop", String(next))
      return next
    })
  }, [])

  const toggleDashboard = useCallback(() => {
    setShowDashboard((prev) => {
      const next = !prev
      localStorage.setItem("cerid-show-dashboard", String(next))
      return next
    })
  }, [])

  return { feedbackLoop, toggleFeedbackLoop, showDashboard, toggleDashboard }
}
