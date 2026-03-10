// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Orchestrates verification state: streaming verification, saved report fetching,
 * report caching, re-verify triggering, and proactive model-switch banners.
 *
 * Extracted from ChatPanel to keep verification concerns cohesive.
 */

import { useState, useMemo, useEffect, useRef, useCallback } from "react"
import { useVerificationStream } from "@/hooks/use-verification-stream"
import { fetchHallucinationReport } from "@/lib/api"
import { MODELS } from "@/lib/types"
import type { ChatMessage, HallucinationReport, ModelOption } from "@/lib/types"
import type { MessageVerificationStatus } from "@/components/chat/message-bubble"

interface UseVerificationOrchestratorOptions {
  activeMessages: ChatMessage[] | undefined
  activeId: string | null
  isStreaming: boolean
  hallucinationEnabled: boolean
  currentModel: ModelOption
}

export interface UseVerificationOrchestratorReturn {
  /** Unified report: streaming report ?? saved report. */
  halReport: HallucinationReport | null
  halLoading: boolean
  /** Raw verification stream (phase, claims, counts, etc.). */
  verification: ReturnType<typeof useVerificationStream>
  /** ID of the latest non-empty assistant message. */
  lastAssistantMsgId: string | null
  /** Per-message verification badge state. */
  verificationStatusForMsg: MessageVerificationStatus
  /** Proactive web-model switch banner. */
  verificationRecBanner: { model: ModelOption; reason: string } | null
  setVerificationRecBanner: (banner: { model: ModelOption; reason: string } | null) => void
  /** Re-trigger verification for the latest assistant message. */
  handleVerifyMessage: () => void
}

export function useVerificationOrchestrator({
  activeMessages,
  activeId,
  isStreaming,
  hallucinationEnabled,
  currentModel,
}: UseVerificationOrchestratorOptions): UseVerificationOrchestratorReturn {
  const [savedReport, setSavedReport] = useState<HallucinationReport | null>(null)
  const [savedReportLoading, setSavedReportLoading] = useState(false)
  const [manualVerifyBump, setManualVerifyBump] = useState(0)
  const [verificationRecBanner, setVerificationRecBanner] = useState<{ model: ModelOption; reason: string } | null>(null)
  const reportCacheRef = useRef<Map<string, HallucinationReport>>(new Map())
  /** Tracks conversations that have completed verification to prevent re-runs on remount. */
  const [completedConversations, setCompletedConversations] = useState<Set<string>>(() => new Set())

  // Derived values from messages
  const latestAssistantText = useMemo(() => {
    if (!activeMessages) return null
    const assistantMsgs = activeMessages.filter((m) => m.role === "assistant")
    return assistantMsgs.length > 0 ? assistantMsgs[assistantMsgs.length - 1].content : null
  }, [activeMessages])

  const streamTriggerKey = useMemo(() => {
    if (!activeMessages || isStreaming) return 0
    // Prevent re-triggering verification for conversations that already completed
    if (activeId && completedConversations.has(activeId)) return 0
    return activeMessages.filter((m) => m.role === "assistant").length
  }, [activeMessages, isStreaming, activeId, completedConversations])

  const latestUserQuery = useMemo(() => {
    if (!activeMessages) return undefined
    const userMsgs = activeMessages.filter((m) => m.role === "user")
    return userMsgs.length > 0 ? userMsgs[userMsgs.length - 1].content : undefined
  }, [activeMessages])

  const latestAssistantModel = useMemo(() => {
    if (!activeMessages) return undefined
    const assistantMsgs = activeMessages.filter((m) => m.role === "assistant")
    return assistantMsgs.length > 0 ? assistantMsgs[assistantMsgs.length - 1].model : undefined
  }, [activeMessages])

  const priorAssistantContext = useMemo(() => {
    if (!activeMessages) return undefined
    const assistantMsgs = activeMessages.filter((m) => m.role === "assistant")
    if (assistantMsgs.length < 2) return undefined
    return assistantMsgs.slice(-4, -1).map((m) => ({
      role: "assistant" as const,
      content: m.content.slice(0, 2000),
    }))
  }, [activeMessages])

  const lastAssistantMsgId = useMemo(() => {
    if (!activeMessages) return null
    const assistantMsgs = activeMessages.filter((m) => m.role === "assistant" && m.content)
    return assistantMsgs.length > 0 ? assistantMsgs[assistantMsgs.length - 1].id : null
  }, [activeMessages])

  // Streaming verification hook
  const verification = useVerificationStream(
    latestAssistantText,
    activeId ?? null,
    hallucinationEnabled,
    streamTriggerKey + manualVerifyBump,
    latestAssistantModel,
    latestUserQuery,
    priorAssistantContext,
  )

  // Clear stale saved report when a new response starts streaming
  useEffect(() => {
    if (isStreaming) setSavedReport(null)
  }, [isStreaming])

  // Cache completed verification report and mark conversation as completed
  useEffect(() => {
    if (verification.phase === "done" && verification.report && activeId) {
      reportCacheRef.current.set(activeId, verification.report)
      setCompletedConversations((prev) => {
        if (prev.has(activeId)) return prev
        const next = new Set(prev)
        next.add(activeId)
        return next
      })
    }
  }, [verification.phase, verification.report, activeId])

  // Fetch saved report when switching conversations (check cache first)
  useEffect(() => {
    if (!activeId || !hallucinationEnabled) {
      setSavedReport(null)
      return
    }
    if (verification.phase !== "idle") return

    const cached = reportCacheRef.current.get(activeId)
    if (cached) {
      setSavedReport(cached)
      return
    }

    let cancelled = false
    setSavedReportLoading(true)
    fetchHallucinationReport(activeId)
      .then((r) => {
        if (!cancelled) {
          setSavedReport(r)
          setSavedReportLoading(false)
          if (r) reportCacheRef.current.set(activeId, r)
        }
      })
      .catch(() => { if (!cancelled) setSavedReportLoading(false) })
    return () => { cancelled = true }
  }, [activeId, hallucinationEnabled, verification.phase])

  // Proactive web-model switch banner
  useEffect(() => {
    if (verification.phase !== "done" || !verification.claims) return
    if (currentModel.capabilities?.webSearch) return
    const ignoranceWithAnswer = verification.claims.filter(
      (c) => c.claim_type === "ignorance" && c.verification_answer,
    )
    if (ignoranceWithAnswer.length === 0) return
    const webModel = MODELS.find((m) => m.capabilities?.webSearch)
    if (!webModel) return
    setVerificationRecBanner({
      model: webModel,
      reason: `${ignoranceWithAnswer.length} question${ignoranceWithAnswer.length > 1 ? "s" : ""} had real-time data available. ${webModel.label} has live web search.`,
    })
  }, [verification.phase, verification.claims, currentModel])

  const halReport = verification.report ?? savedReport
  const halLoading = verification.loading || savedReportLoading

  const verificationStatusForMsg = useMemo((): MessageVerificationStatus => {
    if (!hallucinationEnabled || !lastAssistantMsgId) return null
    if (verification.loading) return { state: "loading" }
    if (halReport && !halReport.skipped && halReport.summary.total > 0) {
      return {
        state: "done",
        verified: halReport.summary.verified,
        unverified: halReport.summary.unverified,
        uncertain: halReport.summary.uncertain,
        total: halReport.summary.total,
      }
    }
    return null
  }, [hallucinationEnabled, lastAssistantMsgId, verification.loading, halReport])

  const handleVerifyMessage = useCallback(() => {
    // Clear completed mark so re-verification can proceed
    if (activeId) {
      setCompletedConversations((prev) => {
        if (!prev.has(activeId)) return prev
        const next = new Set(prev)
        next.delete(activeId)
        return next
      })
    }
    setManualVerifyBump((prev) => prev + 1)
  }, [activeId])

  return {
    halReport,
    halLoading,
    verification,
    lastAssistantMsgId,
    verificationStatusForMsg,
    verificationRecBanner,
    setVerificationRecBanner,
    handleVerifyMessage,
  }
}
