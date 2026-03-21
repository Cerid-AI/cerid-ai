// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Orchestrates verification state: streaming verification, saved report fetching,
 * report caching, re-verify triggering, proactive model-switch banners,
 * and per-message verification selection.
 *
 * Extracted from ChatPanel to keep verification concerns cohesive.
 */

import { useState, useMemo, useEffect, useCallback } from "react"
import { useVerificationStream } from "@/hooks/use-verification-stream"
import { useConversationsContext } from "@/contexts/conversations-context"
import { fetchHallucinationReport, saveVerificationReport } from "@/lib/api"
import { MODELS } from "@/lib/types"
import type { ChatMessage, HallucinationReport, ModelOption } from "@/lib/types"
import type { MessageVerificationStatus } from "@/components/chat/message-bubble"

/** Module-level report cache — survives hook unmount/remount across pane switches.
 *  Keyed by composite "convId:msgId" for per-message granularity. */
const reportCache = new Map<string, HallucinationReport>()

function cacheKey(convId: string, msgId: string): string {
  return `${convId}:${msgId}`
}

interface UseVerificationOrchestratorOptions {
  activeMessages: ChatMessage[] | undefined
  activeId: string | null
  isStreaming: boolean
  hallucinationEnabled: boolean
  currentModel: ModelOption
  expertVerification?: boolean
}

export interface UseVerificationOrchestratorReturn {
  /** Unified report for the selected message: streaming report ?? saved report. */
  halReport: HallucinationReport | null
  halLoading: boolean
  /** Raw verification stream (phase, claims, counts, etc.). */
  verification: ReturnType<typeof useVerificationStream>
  /** ID of the latest non-empty assistant message. */
  lastAssistantMsgId: string | null
  /** Per-message verification badge state for the selected message. */
  verificationStatusForMsg: MessageVerificationStatus
  /** Proactive web-model switch banner. */
  verificationRecBanner: { model: ModelOption; reason: string } | null
  setVerificationRecBanner: (banner: { model: ModelOption; reason: string } | null) => void
  /** Re-trigger verification for the latest assistant message. */
  handleVerifyMessage: () => void
  /** Currently selected message ID for verification display (null = latest). */
  selectedVerificationMsgId: string | null
  setSelectedVerificationMsgId: (id: string | null) => void
  /** All verification reports for the active conversation (keyed by message ID). */
  allVerificationReports: Record<string, HallucinationReport>
}

export function useVerificationOrchestrator({
  activeMessages,
  activeId,
  isStreaming,
  hallucinationEnabled,
  currentModel,
  expertVerification,
}: UseVerificationOrchestratorOptions): UseVerificationOrchestratorReturn {
  const { verifiedConversations, markVerified, clearVerified, saveVerification, getVerification, getAllVerificationReports: getAllReports } = useConversationsContext()
  const [savedReport, setSavedReport] = useState<HallucinationReport | null>(null)
  const [savedReportLoading, setSavedReportLoading] = useState(false)
  const [manualVerifyBump, setManualVerifyBump] = useState(0)
  const [verificationRecBanner, setVerificationRecBanner] = useState<{ model: ModelOption; reason: string } | null>(null)
  const [selectedMsgId, setSelectedMsgId] = useState<string | null>(null)

  // Derived values from messages
  const latestAssistantText = useMemo(() => {
    if (!activeMessages) return null
    const assistantMsgs = activeMessages.filter((m) => m.role === "assistant")
    return assistantMsgs.length > 0 ? assistantMsgs[assistantMsgs.length - 1].content : null
  }, [activeMessages])

  const lastAssistantMsgId = useMemo(() => {
    if (!activeMessages) return null
    const assistantMsgs = activeMessages.filter((m) => m.role === "assistant" && m.content)
    return assistantMsgs.length > 0 ? assistantMsgs[assistantMsgs.length - 1].id : null
  }, [activeMessages])

  // Effective selected message ID — falls back to latest assistant message
  const effectiveMsgId = selectedMsgId ?? lastAssistantMsgId

  const streamTriggerKey = useMemo(() => {
    if (!activeMessages || isStreaming) return 0
    // Only skip if the LATEST message already has a cached report (per-message, not per-conversation)
    if (activeId && lastAssistantMsgId && reportCache.has(cacheKey(activeId, lastAssistantMsgId))) return 0
    return activeMessages.filter((m) => m.role === "assistant").length
  }, [activeMessages, isStreaming, activeId, lastAssistantMsgId])

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

  // Streaming verification hook
  const verification = useVerificationStream(
    latestAssistantText,
    activeId ?? null,
    hallucinationEnabled,
    streamTriggerKey + manualVerifyBump,
    latestAssistantModel,
    latestUserQuery,
    priorAssistantContext,
    expertVerification,
  )

  // Clear stale saved report, verified mark, AND module-level cache when a new
  // response starts streaming. Also auto-reset selected message to latest.
  useEffect(() => {
    if (isStreaming) {
      setSavedReport(null)
      setSelectedMsgId(null)
      if (activeId && lastAssistantMsgId) {
        clearVerified(activeId)
        reportCache.delete(cacheKey(activeId, lastAssistantMsgId))
      }
    }
  }, [isStreaming, activeId, lastAssistantMsgId, clearVerified])

  // Cache completed verification report, mark conversation as completed, and persist to backend + localStorage
  useEffect(() => {
    if (verification.phase === "done" && verification.report && activeId && lastAssistantMsgId) {
      reportCache.set(cacheKey(activeId, lastAssistantMsgId), verification.report)
      markVerified(activeId)

      // Persist to localStorage alongside chat history
      saveVerification(activeId, lastAssistantMsgId, verification.report)

      // Persist to Neo4j for long-term storage (fire-and-forget)
      const r = verification.report
      if (r.summary && r.claims) {
        saveVerificationReport({
          conversation_id: activeId,
          claims: r.claims as unknown as Array<Record<string, unknown>>,
          overall_score: (r.summary as Record<string, unknown>).overall_confidence as number ?? 0,
          verified: r.summary.verified ?? 0,
          unverified: r.summary.unverified ?? 0,
          uncertain: r.summary.uncertain ?? 0,
          total: r.summary.total ?? 0,
        }).catch(() => {})
      }
    }
  }, [verification.phase, verification.report, activeId, lastAssistantMsgId, markVerified, saveVerification])

  // Fetch saved report when switching conversations or selected message changes
  useEffect(() => {
    if (!activeId || !hallucinationEnabled || !effectiveMsgId) {
      setSavedReport(null)
      return
    }

    const key = cacheKey(activeId, effectiveMsgId)

    // 1. Check module-level cache FIRST — avoids race with stream phase reset on remount
    const cached = reportCache.get(key)
    if (cached) {
      setSavedReport(cached)
      return
    }

    // 2. Check localStorage (persisted with chat history)
    const localReport = getVerification(activeId, effectiveMsgId)
    if (localReport) {
      reportCache.set(key, localReport)
      setSavedReport(localReport)
      return
    }

    // For non-latest messages, there's nothing more to check — skip API fetch
    if (effectiveMsgId !== lastAssistantMsgId) {
      setSavedReport(null)
      return
    }

    // Only fetch from API when stream is idle and no claims have been streamed yet
    if (verification.phase !== "idle") return
    if (verification.claims && verification.claims.length > 0) return

    // 3. Fetch from API (Redis → Neo4j fallback) — only for latest message
    let cancelled = false
    setSavedReportLoading(true)
    fetchHallucinationReport(activeId)
      .then((r) => {
        if (!cancelled) {
          setSavedReport(r)
          setSavedReportLoading(false)
          if (r && lastAssistantMsgId) {
            reportCache.set(cacheKey(activeId, lastAssistantMsgId), r)
            saveVerification(activeId, lastAssistantMsgId, r)  // cache in localStorage for future
          }
        }
      })
      .catch(() => { if (!cancelled) setSavedReportLoading(false) })
    return () => { cancelled = true; setSavedReportLoading(false) }
  }, [activeId, effectiveMsgId, lastAssistantMsgId, hallucinationEnabled, verification.phase, getVerification, saveVerification])

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

  // For the selected message, use live streaming report only when viewing the latest message
  const halReport = useMemo(() => {
    if (effectiveMsgId === lastAssistantMsgId) {
      return verification.report ?? savedReport
    }
    return savedReport
  }, [effectiveMsgId, lastAssistantMsgId, verification.report, savedReport])

  // Only include savedReportLoading when stream is idle — otherwise stream has its own phase
  const halLoading = effectiveMsgId === lastAssistantMsgId
    ? (verification.loading || (verification.phase === "idle" && savedReportLoading))
    : false

  const verificationStatusForMsg = useMemo((): MessageVerificationStatus => {
    if (!hallucinationEnabled || !effectiveMsgId) return null
    if (halLoading) return { state: "loading" }
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
  }, [hallucinationEnabled, effectiveMsgId, halLoading, halReport])

  const handleVerifyMessage = useCallback(() => {
    // Clear completed mark + module cache so re-verification can proceed
    if (activeId && lastAssistantMsgId) {
      clearVerified(activeId)
      reportCache.delete(cacheKey(activeId, lastAssistantMsgId))
    }
    setSelectedMsgId(null) // Reset to latest for re-verify
    setManualVerifyBump((prev) => prev + 1)
  }, [activeId, lastAssistantMsgId, clearVerified])

  // All verification reports for badges on all messages
  const allVerificationReports = useMemo(() => {
    if (!activeId) return {}
    const stored = getAllReports(activeId)
    // Also include any live/cached reports not yet persisted
    if (lastAssistantMsgId && verification.report) {
      return { ...stored, [lastAssistantMsgId]: verification.report }
    }
    return stored
  }, [activeId, getAllReports, lastAssistantMsgId, verification.report])

  return {
    halReport,
    halLoading,
    verification,
    lastAssistantMsgId,
    verificationStatusForMsg,
    verificationRecBanner,
    setVerificationRecBanner,
    handleVerifyMessage,
    selectedVerificationMsgId: effectiveMsgId,
    setSelectedVerificationMsgId: setSelectedMsgId,
    allVerificationReports,
  }
}
