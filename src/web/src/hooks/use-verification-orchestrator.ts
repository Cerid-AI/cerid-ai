// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Orchestrates verification state: streaming verification, saved report fetching,
 * report caching, re-verify triggering, proactive model-switch banners,
 * and per-message verification selection.
 *
 * Extracted from ChatPanel to keep verification concerns cohesive.
 */

import { useState, useMemo, useEffect, useCallback, useRef } from "react"
import { useVerificationStream } from "@/hooks/use-verification-stream"
import { useConversationsContext } from "@/contexts/conversations-context"
import { useKBInjection } from "@/contexts/kb-injection-context"
import { saveVerificationReport } from "@/lib/api"
import { MODELS } from "@/lib/types"
import type { ChatMessage, HallucinationClaim, HallucinationReport, ModelOption } from "@/lib/types"
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
  /** Per-claim expert re-verification updates (index → partial claim). */
  claimUpdates: Map<number, Partial<HallucinationClaim>>
  /** Set of claim indices that have been expert-re-verified. */
  expertVerifiedClaims: Set<number>
  /** Callback to record an expert re-verification result for a claim. */
  handleClaimUpdate: (index: number, result: Partial<HallucinationClaim>) => void
}

export function useVerificationOrchestrator({
  activeMessages,
  activeId,
  isStreaming,
  hallucinationEnabled,
  currentModel,
  expertVerification,
}: UseVerificationOrchestratorOptions): UseVerificationOrchestratorReturn {
  const { markVerified, clearVerified, saveVerification, getVerification, getAllVerificationReports: getAllReports } = useConversationsContext()
  const [savedReport, setSavedReport] = useState<HallucinationReport | null>(null)
  const [manualVerifyBump, setManualVerifyBump] = useState(0)
  const [verificationRecBanner, setVerificationRecBanner] = useState<{ model: ModelOption; reason: string } | null>(null)
  const [selectedMsgId, setSelectedMsgId] = useState<string | null>(null)

  // Per-claim expert re-verification state (lifted from HallucinationPanel)
  const [claimUpdates, setClaimUpdates] = useState<Map<number, Partial<HallucinationClaim>>>(new Map())
  const [expertVerifiedClaims, setExpertVerifiedClaims] = useState<Set<number>>(new Set())

  const handleClaimUpdate = useCallback((index: number, result: Partial<HallucinationClaim>) => {
    setClaimUpdates((prev) => new Map(prev).set(index, result))
    setExpertVerifiedClaims((prev) => new Set(prev).add(index))
  }, [])

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

  // Stable trigger key: only increments when a genuinely NEW assistant message
  // is ADDED (streaming completes), not when switching to an existing conversation.
  // Initialize lastKnownCount to current count so opening an existing conversation
  // doesn't trigger verification (count goes 0→1 but that's conversation switch, not new message).
  const assistantCount = activeMessages?.filter((m) => m.role === "assistant").length ?? 0
  const lastKnownCount = useRef(-1) // -1 = uninitialized
  const triggerCounter = useRef(0)

  if (lastKnownCount.current === -1) {
    // First render: set baseline without triggering
    lastKnownCount.current = assistantCount
  } else if (assistantCount > lastKnownCount.current && !isStreaming) {
    // Count genuinely increased (new message completed streaming)
    const key = activeId && lastAssistantMsgId ? cacheKey(activeId, lastAssistantMsgId) : ""
    if (!key || !reportCache.has(key)) {
      triggerCounter.current += 1
    }
    lastKnownCount.current = assistantCount
  } else if (assistantCount !== lastKnownCount.current && !isStreaming) {
    // Count changed while not streaming (conversation switch) — update baseline
    // without triggering. IMPORTANT: do NOT update baseline while streaming,
    // otherwise the post-stream render sees count==baseline and never triggers.
    lastKnownCount.current = assistantCount
  }

  const streamTriggerKey = isStreaming ? 0 : triggerCounter.current

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

  // Anti-circularity: collect KB artifact IDs that were injected into the LLM prompt
  const { injectedContext } = useKBInjection()
  const sourceArtifactIds = useMemo(
    () => injectedContext.map((r) => r.artifact_id).filter(Boolean),
    [injectedContext],
  )

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
    sourceArtifactIds,
  )

  // Clear stale saved report, verified mark, AND module-level cache when a new
  // response starts streaming. Also auto-reset selected message to latest.
  useEffect(() => {
    if (isStreaming) {
      setSavedReport(null)
      setSelectedMsgId(null)
      setClaimUpdates(new Map())
      setExpertVerifiedClaims(new Set())
      savedForKey.current = ""
      if (activeId && lastAssistantMsgId) {
        reportCache.delete(cacheKey(activeId, lastAssistantMsgId))
        // Defer context update to avoid re-render during effect
        const aid = activeId
        setTimeout(() => clearVerified(aid), 0)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- exclude clearVerified: context callback, triggers state update
  }, [isStreaming, activeId, lastAssistantMsgId])

  // Guard: only save once per verification completion (prevents infinite re-render loop)
  const savedForKey = useRef<string>("")

  // Cache completed verification report — uses refs to avoid triggering re-renders
  const markVerifiedRef = useRef(markVerified)
  const saveVerificationRef = useRef(saveVerification)
  markVerifiedRef.current = markVerified
  saveVerificationRef.current = saveVerification

  useEffect(() => {
    if (verification.phase !== "done" || !verification.report || !activeId || !lastAssistantMsgId) return
    const key = cacheKey(activeId, lastAssistantMsgId)
    if (savedForKey.current === key) return
    savedForKey.current = key

    // Cache in module-level map (instant, no re-render)
    reportCache.set(key, verification.report)

    // Defer context saves to next event loop tick.
    // This breaks the synchronous render loop: without deferral, calling
    // markVerified/saveVerification updates conversations state, which
    // re-renders the entire component tree, which can re-trigger effects.
    const reportCopy = verification.report
    const aid = activeId
    const mid = lastAssistantMsgId
    setTimeout(() => {
      markVerifiedRef.current(aid)
      saveVerificationRef.current(aid, mid, reportCopy)

      // Persist to Neo4j (fire-and-forget)
      if (reportCopy.summary && reportCopy.claims) {
        saveVerificationReport({
          conversation_id: aid,
          claims: reportCopy.claims as unknown as Array<Record<string, unknown>>,
          overall_score: (reportCopy.summary as Record<string, unknown>).overall_confidence as number ?? 0,
          verified: reportCopy.summary?.verified ?? 0,
          unverified: reportCopy.summary?.unverified ?? 0,
          uncertain: reportCopy.summary?.uncertain ?? 0,
          total: reportCopy.summary?.total ?? 0,
        }).catch(() => {})
      }
    }, 0)
  // eslint-disable-next-line react-hooks/exhaustive-deps -- context callbacks accessed via refs to prevent re-render loops
  }, [verification.phase, verification.report, activeId, lastAssistantMsgId])

  // Load saved verification report from local caches (module-level + localStorage).
  useEffect(() => {
    if (!activeId || !hallucinationEnabled || !effectiveMsgId) {
      setSavedReport(null)
      return
    }

    const key = cacheKey(activeId, effectiveMsgId)

    // 1. Check module-level cache (fastest, survives tab switches)
    const cached = reportCache.get(key)
    if (cached) {
      setSavedReport(cached)
      return
    }

    // 2. Check localStorage (persisted across page reloads)
    const localReport = getVerification(activeId, effectiveMsgId)
    if (localReport) {
      reportCache.set(key, localReport)
      setSavedReport(localReport)
      return
    }

    // No cached report — user can re-verify via button
    setSavedReport(null)
  // eslint-disable-next-line react-hooks/exhaustive-deps -- exclude getVerification (context callback, causes re-render loops)
  }, [activeId, effectiveMsgId, hallucinationEnabled])

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

  const halLoading = effectiveMsgId === lastAssistantMsgId
    ? verification.loading
    : false

  const verificationStatusForMsg = useMemo((): MessageVerificationStatus => {
    if (!hallucinationEnabled || !effectiveMsgId) return null
    if (halLoading) return { state: "loading" }
    if (halReport && !halReport.skipped && halReport.summary?.total && halReport.summary.total > 0) {
      // Merge original claims with expert re-verification updates
      if (claimUpdates.size > 0 && halReport.claims) {
        const merged = halReport.claims.map((c, i) =>
          claimUpdates.has(i) ? { ...c, ...claimUpdates.get(i) } : c,
        )
        const verified = merged.filter((c) => c.status === "verified").length
        const unverified = merged.filter((c) => c.status === "unverified").length
        const uncertain = merged.filter((c) => c.status === "uncertain").length
        const skipped = merged.filter((c) => c.status === "skipped").length
        return {
          state: "done",
          verified,
          unverified,
          uncertain,
          skipped,
          total: merged.length,
          creditExhausted: verification.creditError != null,
          hasExpertClaims: expertVerifiedClaims.size > 0,
        }
      }
      return {
        state: "done",
        verified: halReport.summary?.verified,
        unverified: halReport.summary?.unverified,
        uncertain: halReport.summary?.uncertain,
        skipped: halReport.summary?.skipped,
        total: halReport.summary?.total,
        creditExhausted: verification.creditError != null,
        hasExpertClaims: expertVerifiedClaims.size > 0,
      }
    }
    return null
  }, [hallucinationEnabled, effectiveMsgId, halLoading, halReport, claimUpdates, expertVerifiedClaims])

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
    claimUpdates,
    expertVerifiedClaims,
    handleClaimUpdate,
  }
}
