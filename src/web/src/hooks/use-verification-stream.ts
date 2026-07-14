// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef, useState } from "react"
import { streamVerification } from "@/lib/api"
import type { StreamingClaim, HallucinationReport } from "@/lib/types"

export type VerificationPhase = "idle" | "extracting" | "verifying" | "done" | "degraded" | "error"

/** Idle budget between received SSE chunks. The backend emits keepalive
 *  comments every ~15s while long verifications run, so 45s of true silence
 *  means the stream is gone — three missed keepalives. */
export const VERIFICATION_IDLE_TIMEOUT_MS = 45_000

/** Absolute ceiling for a single verification run, regardless of traffic. */
export const VERIFICATION_MAX_DURATION_MS = 300_000

export interface ActivityLogEntry {
  time: string
  message: string
  type: "info" | "success" | "error"
}

/** Format elapsed milliseconds as MM:SS. */
function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const min = String(Math.floor(totalSec / 60)).padStart(2, "0")
  const sec = String(totalSec % 60).padStart(2, "0")
  return `${min}:${sec}`
}

interface StreamingSummary {
  verified: number
  unverified: number
  uncertain: number
  skipped: number
  total: number
  overallConfidence: number
  extractionMethod?: string
  creditExhausted?: boolean
}

/**
 * Estimate verification cost for a set of claims.
 * Based on GPT-4o-mini pricing: $0.15/$0.60 per 1M tokens.
 * ~250 input + ~75 output tokens per claim verification.
 * ~1200 input + ~300 output for extraction (amortized per run).
 */
function estimateVerificationCost(claimCount: number): number {
  const extractionCost = (1200 * 0.15 + 300 * 0.6) / 1_000_000 // ~$0.00036
  const perClaimCost = (250 * 0.15 + 75 * 0.6) / 1_000_000     // ~$0.000083
  return extractionCost + claimCount * perClaimCost
}

interface UseVerificationStreamReturn {
  /** Claims discovered and verified so far. */
  claims: StreamingClaim[]
  /** Current phase of the verification process. */
  phase: VerificationPhase
  /** Summary once verification completes. */
  summary: StreamingSummary | null
  /** True while verification is in progress. */
  loading: boolean
  /** Number of claims verified so far (for progress display). */
  verifiedCount: number
  /** Total claims extracted (known after extraction phase). */
  totalClaims: number
  /** Extraction method used: "llm", "heuristic", or "none". */
  extractionMethod: string | null
  /** Converted to HallucinationReport format for status bar compatibility. */
  report: HallucinationReport | null
  /** Accumulated session-wide claims checked (resets on page reload). */
  sessionClaimsChecked: number
  /** Accumulated session-wide estimated verification cost in USD. */
  sessionEstCost: number
  /** Credit exhaustion error message (set when provider returns 402). */
  creditError: string | null
  /** Live activity log entries from the verification pipeline. */
  activityLog: ActivityLogEntry[]
}

/**
 * Streams verification results from the SSE endpoint.
 * Uses POST + ReadableStream since EventSource only supports GET.
 */
export function useVerificationStream(
  responseText: string | null,
  conversationId: string | null,
  enabled: boolean,
  /** Increment to re-trigger verification for the same conversation. */
  triggerKey: number,
  /** Model ID that generated the response (from message.model, not dropdown). */
  model?: string,
  /** Original user query for evasion detection. */
  userQuery?: string,
  /** Prior assistant messages for history consistency checking. */
  conversationHistory?: Array<{ role: string; content: string }>,
  /** Use expert-tier model (Grok 4) for all verification. */
  expertMode?: boolean,
  /** KB artifact IDs injected into the LLM prompt (anti-circularity). */
  sourceArtifactIds?: string[],
): UseVerificationStreamReturn {
  const [claims, setClaims] = useState<StreamingClaim[]>([])
  const [phase, setPhase] = useState<VerificationPhase>("idle")
  const [summary, setSummary] = useState<StreamingSummary | null>(null)
  const [extractionMethod, setExtractionMethod] = useState<string | null>(null)
  const [creditError, setCreditError] = useState<string | null>(null)
  const [activityLog, setActivityLog] = useState<ActivityLogEntry[]>([])
  const streamStartRef = useRef<number>(0)
  const abortRef = useRef<(() => void) | null>(null)
  const hasReceivedEventsRef = useRef(false)
  const responseTextRef = useRef(responseText)
  useEffect(() => { responseTextRef.current = responseText }, [responseText])
  const modelRef = useRef(model)
  useEffect(() => { modelRef.current = model }, [model])
  const userQueryRef = useRef(userQuery)
  useEffect(() => { userQueryRef.current = userQuery }, [userQuery])
  const historyRef = useRef(conversationHistory)
  useEffect(() => { historyRef.current = conversationHistory }, [conversationHistory])
  const expertModeRef = useRef(expertMode)
  useEffect(() => { expertModeRef.current = expertMode }, [expertMode])
  const sourceArtifactIdsRef = useRef(sourceArtifactIds)
  useEffect(() => { sourceArtifactIdsRef.current = sourceArtifactIds }, [sourceArtifactIds])
  // enabled is read via ref so that toggling verification mid-stream does NOT
  // abort the running stream.  Only checked at stream-start time.  This
  // prevents settings hydration (fetchSettings → hydrateHallucination) from
  // racing with an active stream and killing it via effect cleanup.
  const enabledRef = useRef(enabled)
  useEffect(() => { enabledRef.current = enabled }, [enabled])

  // Accumulated session metrics (persist across verification runs, reset on page reload)
  const sessionRef = useRef({ claimsChecked: 0, estCost: 0 })
  const [sessionClaimsChecked, setSessionClaimsChecked] = useState(0)
  const [sessionEstCost, setSessionEstCost] = useState(0)

  // Reset state when conversation changes
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    setClaims([])
    setPhase("idle")
    setSummary(null)
    setExtractionMethod(null)
    setCreditError(null)
    setActivityLog([])
  }, [conversationId])

  // Clear stale verification state when a new response starts streaming (triggerKey resets to 0)
  const prevTriggerKey = useRef(triggerKey)
  useEffect(() => {
    if (prevTriggerKey.current !== 0 && triggerKey === 0) {
      setClaims([])
      setPhase("idle")
      setSummary(null)
      setExtractionMethod(null)
      setCreditError(null)
      setActivityLog([])
    }
    prevTriggerKey.current = triggerKey
  }, [triggerKey])

  // Debounce guard: React 18+ StrictMode double-mounts components in dev
  // mode, which fires this effect twice with identical deps. The cleanup
  // from the first mount aborts stream #1, but stream #2 still fires a
  // second HTTP POST to the server. This ref tracks the last stream key
  // and timestamp so we can skip the duplicate.
  const lastStreamRef = useRef<{ key: string; time: number }>({ key: "", time: 0 })

  useEffect(() => {
    // Read refs at effect-fire time to avoid re-triggering when these values
    // change after the stream has already started (e.g., debounced content updates).
    const text = responseTextRef.current
    const query = userQueryRef.current
    if (!text || !conversationId || !enabledRef.current || triggerKey === 0) {
      return
    }

    // StrictMode debounce: if the same stream key fired within 50ms,
    // this is a StrictMode remount — skip to avoid duplicate HTTP requests.
    const streamKey = `${conversationId}-${triggerKey}`
    const now = Date.now()
    if (
      lastStreamRef.current.key === streamKey &&
      now - lastStreamRef.current.time < 50
    ) {
      return
    }
    lastStreamRef.current = { key: streamKey, time: now }

    // Abort any previous stream
    abortRef.current?.()

    setClaims([])
    setPhase("extracting")
    setSummary(null)
    setExtractionMethod(null)
    setCreditError(null)
    setActivityLog([])
    streamStartRef.current = Date.now()
    // Seed the first log entry synchronously (elapsed = 0)
    setActivityLog([{ time: "00:00", message: "Extracting claims...", type: "info" }])

    const { response, abort } = streamVerification(text, conversationId, undefined, modelRef.current, query, historyRef.current, expertModeRef.current, sourceArtifactIdsRef.current)
    abortRef.current = abort

    let cancelled = false
    let receivedSummary = false
    // Track claim count locally for log messages (avoids stale state reads)
    let extractedClaimCount = 0

    /** Append an activity log entry with elapsed time from stream start. */
    const logEntry = (message: string, type: ActivityLogEntry["type"]) => {
      const elapsed = Date.now() - streamStartRef.current
      setActivityLog((prev) => {
        const next = [...prev, { time: formatElapsed(elapsed), message, type }]
        return next.length > 200 ? next.slice(-200) : next
      })
    }

    // Finalize any still-`pending` claim cards so the UI doesn't render a
    // stale spinner indefinitely when the stream ends without a per-claim
    // verdict (abort, timeout, or natural close without summary).
    const finalizePendingClaims = (reason: string) => {
      setClaims((prev) =>
        prev.map((c) =>
          c.status === "pending"
            ? { ...c, status: "uncertain", reason: c.reason ?? reason }
            : c,
        ),
      )
    }

    // Timeouts: the old fixed 60s wall-clock deadline from stream start
    // killed healthy slow-but-alive streams — the backend sends keepalive
    // comments every ~15s under load, and the premature client abort showed
    // up live as nginx 499 + backend CancelledError. Instead run an IDLE
    // timeout that re-arms on every received chunk (keepalives included),
    // plus a generous absolute ceiling as a backstop. On timeout, degrade
    // (partial results + retry affordance via the orchestrator's manual
    // re-verify) instead of hard-erroring.
    let idleTimerId: ReturnType<typeof setTimeout> | undefined
    const onStreamTimeout = (reason: string) => {
      if (cancelled) return
      cancelled = true
      abort()
      // Once the summary landed the round-1 verdict is complete — the stream
      // only stays open for the backend's post-summary retry sweep. Close it
      // but keep phase "done": degrading here would hide a full report
      // behind the partial-results banner.
      if (receivedSummary) return
      finalizePendingClaims("verification stalled — partial result, retry to re-verify")
      setPhase("degraded")
      logEntry(`${reason} — showing partial results, use Retry to re-verify`, "error")
    }
    const armIdleTimer = () => {
      clearTimeout(idleTimerId)
      idleTimerId = setTimeout(
        () => onStreamTimeout(`No data from verifier for ${VERIFICATION_IDLE_TIMEOUT_MS / 1000}s`),
        VERIFICATION_IDLE_TIMEOUT_MS,
      )
    }
    armIdleTimer()
    const maxDurationTimerId = setTimeout(
      () => onStreamTimeout(`Verification exceeded ${VERIFICATION_MAX_DURATION_MS / 60_000} minutes`),
      VERIFICATION_MAX_DURATION_MS,
    )

    async function processStream() {
      try {
        const res = await response
        if (!res.ok || !res.body) {
          if (!cancelled) {
            finalizePendingClaims("stream failed to open")
            setPhase("error")
          }
          return
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ""

        while (true) {
          const { done, value } = await reader.read()
          if (done || cancelled) break

          // Any received bytes — including keepalive comment lines, which the
          // parser below skips — prove the stream is alive; re-arm the idle
          // timer here so keepalives feed it.
          armIdleTimer()

          buffer += decoder.decode(value, { stream: true })

          // Parse SSE lines
          const lines = buffer.split("\n")
          buffer = lines.pop() ?? "" // Keep incomplete line in buffer

          for (const line of lines) {
            const trimmed = line.trim()
            // Skip SSE comments (keepalive heartbeats from backend)
            if (trimmed.startsWith(":")) continue
            if (!trimmed.startsWith("data:")) continue

            const jsonStr = trimmed.slice(5).trim()
            if (!jsonStr) continue

            try {
              const event = JSON.parse(jsonStr)
              if (cancelled) break

              switch (event.type) {
                case "extraction_complete":
                  setExtractionMethod(event.method ?? null)
                  setPhase("verifying")
                  hasReceivedEventsRef.current = true
                  logEntry(`Extracted ${extractedClaimCount} claim${extractedClaimCount !== 1 ? "s" : ""} (${event.method ?? "unknown"})`, "info")
                  logEntry("Querying knowledge base...", "info")
                  break

                case "claim_extracted":
                  extractedClaimCount++
                  setClaims((prev) => [
                    ...prev,
                    {
                      claim: event.claim,
                      claim_type: event.claim_type,
                      index: event.index,
                      status: "pending",
                    },
                  ])
                  // Phase stays at "verifying" (set by extraction_complete).
                  // Previously this set "extracting" which reverted the phase
                  // after extraction_complete had already transitioned it.
                  break

                case "claim_verified": {
                  // Round-2 retry-sweep verdicts arrive AFTER the summary;
                  // apply them in place without regressing "done" → "verifying".
                  if (!receivedSummary) setPhase("verifying")
                  const claimNum = (event.index ?? 0) + 1
                  const conf = event.confidence != null ? ` (${event.verification_method ?? "KB"} match ${(event.confidence as number).toFixed(2)})` : ""
                  if (event.status === "verified") {
                    logEntry(`Claim ${claimNum}: supported${conf}`, "success")
                  } else if (event.status === "unverified") {
                    logEntry(`Claim ${claimNum}: refuted (${event.verification_method ?? "external"})`, "error")
                  } else {
                    logEntry(`Claim ${claimNum}: ${event.status ?? "uncertain"}${conf}`, "info")
                  }
                  setClaims((prev) =>
                    prev.map((c) =>
                      c.index === event.index
                        ? {
                            ...c,
                            claim: event.claim || c.claim,
                            claim_type: event.claim_type || c.claim_type,
                            status: event.status ?? event.verdict?.status ?? "uncertain",
                            similarity: event.confidence ?? event.verdict?.confidence ?? 0,
                            source: event.source,
                            source_artifact_id: event.source_artifact_id,
                            source_domain: event.source_domain,
                            source_snippet: event.source_snippet,
                            source_urls: event.source_urls || [],
                            // Total-deadline timeout events nest the reason under
                            // verdict (e.g. "KB-only verdict (verification timeout)").
                            reason: event.reason ?? event.verdict?.reason,
                            verification_method: event.verification_method,
                            verification_model: event.verification_model,
                            verification_answer: event.verification_answer || undefined,
                            circular_source: event.circular_source || undefined,
                            nli_entailment: event.nli_entailment,
                            nli_contradiction: event.nli_contradiction,
                            memory_source: event.memory_source,
                          }
                        : c,
                    ),
                  )
                  break
                }

                case "credit_error":
                  setCreditError(event.message ?? "LLM provider credits exhausted")
                  logEntry("Credit exhausted — verification incomplete", "error")
                  break

                case "summary":
                  receivedSummary = true
                  setSummary({
                    verified: event.verified,
                    unverified: event.unverified,
                    uncertain: event.uncertain,
                    skipped: event.skipped ?? 0,
                    total: event.total,
                    overallConfidence: event.overall_confidence,
                    extractionMethod: event.extraction_method,
                    creditExhausted: event.credit_exhausted ?? false,
                  })
                  // Accumulate session-wide metrics
                  sessionRef.current.claimsChecked += event.total ?? 0
                  sessionRef.current.estCost += estimateVerificationCost(event.total ?? 0)
                  setSessionClaimsChecked(sessionRef.current.claimsChecked)
                  setSessionEstCost(sessionRef.current.estCost)
                  setPhase("done")
                  logEntry(`Complete — ${event.verified ?? 0}/${event.total ?? 0} claims verified`, "success")
                  break

                case "summary_update":
                  // Final totals after the backend's round-2 retry sweep
                  // (emitted after "summary", before "persisted"). Phase stays
                  // "done"; the derived report picks up the refreshed counts.
                  setSummary((prev) =>
                    prev
                      ? {
                          ...prev,
                          verified: event.verified ?? prev.verified,
                          unverified: event.unverified ?? prev.unverified,
                          uncertain: event.uncertain ?? prev.uncertain,
                          total: event.total ?? prev.total,
                          overallConfidence: event.overall_confidence ?? prev.overallConfidence,
                        }
                      : prev,
                  )
                  logEntry(`Retry sweep — ${event.verified ?? 0}/${event.total ?? 0} claims verified`, "success")
                  break

                case "consistency_check":
                  // Update claims with consistency issues found
                  if (Array.isArray(event.issues)) {
                    setClaims((prev) =>
                      prev.map((c) => {
                        const issue = (event.issues as Array<{ claim_index: number; contradiction: string; type: string }>)
                          .find((iss) => iss.claim_index === c.index)
                        return issue
                          ? { ...c, consistency_issue: issue.contradiction }
                          : c
                      }),
                    )
                  }
                  break

                case "error": {
                  finalizePendingClaims(
                    typeof event.message === "string" && event.message
                      ? `verification error: ${String(event.message).slice(0, 120)}`
                      : "verification error",
                  )
                  setPhase("error")
                  // Use the detailed payload surfaced by streaming.py when
                  // available (error_type / phase / message / claims_seen).
                  // Falls back to a generic message for forward-compat with
                  // older backends that still emit bare error events.
                  const detail = event.message ? `: ${String(event.message).slice(0, 200)}` : ""
                  const errPhase = event.phase ? ` (${event.phase})` : ""
                  const seen = typeof event.claims_seen === "number" && typeof event.claims_total === "number"
                    ? ` — ${event.claims_seen}/${event.claims_total} claims processed before failure`
                    : ""
                  logEntry(`Verification error${errPhase}${detail}${seen}`, "error")
                  break
                }
              }
            } catch {
              // Skip malformed JSON lines
            }
          }
        }

        // Stream ended — if no summary event was received, don't leave phase
        // stuck AND don't leave per-claim cards rendering a spinner forever.
        if (!cancelled && !receivedSummary) {
          finalizePendingClaims("stream closed before verdict")
          setPhase("error")
        }
      } catch (err) {
        if (!cancelled && !(err instanceof DOMException && (err as DOMException).name === "AbortError")) {
          finalizePendingClaims("stream interrupted")
          setPhase("error")
        }
      } finally {
        clearTimeout(idleTimerId)
        clearTimeout(maxDurationTimerId)
      }
    }

    hasReceivedEventsRef.current = false
    processStream()

    return () => {
      clearTimeout(idleTimerId)
      clearTimeout(maxDurationTimerId)
      cancelled = true
      abort()
    }
  }, [conversationId, triggerKey])

  const verifiedCount = claims.filter((c) => c.status && c.status !== "pending").length
  const totalClaims = claims.length

  // Build a HallucinationReport-compatible object for the status bar.
  // "done": authoritative counts from the summary event.
  // "degraded": the stream idled out mid-run — derive counts from whatever
  // claims settled (pending ones were finalized to "uncertain") so the
  // promised partial results actually render instead of staying hidden.
  const doneCounts = phase === "done" && summary
    ? {
        total: summary.total,
        verified: summary.verified,
        unverified: summary.unverified,
        uncertain: summary.uncertain,
        skipped: summary.skipped,
      }
    : null
  const degradedCounts = phase === "degraded" && claims.length > 0
    ? (() => {
        const verified = claims.filter((c) => c.status === "verified").length
        const unverified = claims.filter((c) => c.status === "unverified").length
        const skipped = claims.filter((c) => c.status === "skipped").length
        return {
          total: claims.length,
          verified,
          unverified,
          skipped,
          // Everything else (uncertain / finalized-pending / error) rolls up
          // as uncertain so the counts always sum to the total.
          uncertain: claims.length - verified - unverified - skipped,
        }
      })()
    : null
  const reportSummary = doneCounts ?? degradedCounts
  const report: HallucinationReport | null =
    reportSummary
      ? {
          conversation_id: conversationId ?? "",
          timestamp: new Date().toISOString(),
          skipped: reportSummary.total === 0,
          extraction_method: summary?.extractionMethod ?? extractionMethod ?? undefined,
          claims: claims.map((c) => ({
            claim: c.claim,
            claim_type: c.claim_type,
            status: (c.status === "pending" ? "uncertain" : c.status) as "verified" | "unverified" | "uncertain" | "error" | "skipped",
            similarity: c.similarity ?? 0,
            source_filename: c.source || undefined,
            source_artifact_id: c.source_artifact_id || undefined,
            source_domain: c.source_domain || undefined,
            source_snippet: c.source_snippet || undefined,
            source_urls: c.source_urls,
            reason: c.reason || undefined,
            verification_method: c.verification_method,
            verification_model: c.verification_model,
            verification_answer: c.verification_answer,
            consistency_issue: c.consistency_issue,
            circular_source: c.circular_source,
          })),
          summary: reportSummary,
        }
      : null

  return {
    claims,
    phase,
    summary,
    loading: phase === "extracting" || phase === "verifying",
    verifiedCount,
    totalClaims,
    extractionMethod: extractionMethod ?? summary?.extractionMethod ?? null,
    report,
    sessionClaimsChecked,
    sessionEstCost,
    creditError,
    activityLog,
  }
}
