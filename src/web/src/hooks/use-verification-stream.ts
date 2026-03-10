// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef, useState } from "react"
import { streamVerification } from "@/lib/api"
import type { StreamingClaim, HallucinationReport } from "@/lib/types"

export type VerificationPhase = "idle" | "extracting" | "verifying" | "done" | "error"

interface StreamingSummary {
  verified: number
  unverified: number
  uncertain: number
  total: number
  overallConfidence: number
  extractionMethod?: string
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
): UseVerificationStreamReturn {
  const [claims, setClaims] = useState<StreamingClaim[]>([])
  const [phase, setPhase] = useState<VerificationPhase>("idle")
  const [summary, setSummary] = useState<StreamingSummary | null>(null)
  const [extractionMethod, setExtractionMethod] = useState<string | null>(null)
  const abortRef = useRef<(() => void) | null>(null)
  const modelRef = useRef(model)
  useEffect(() => { modelRef.current = model }, [model])
  const historyRef = useRef(conversationHistory)
  useEffect(() => { historyRef.current = conversationHistory }, [conversationHistory])

  // Accumulated session metrics (persist across verification runs, reset on page reload)
  const sessionRef = useRef({ claimsChecked: 0, estCost: 0 })
  const [sessionClaimsChecked, setSessionClaimsChecked] = useState(0)
  const [sessionEstCost, setSessionEstCost] = useState(0)

  // Reset state when conversation changes
  useEffect(() => {
    setClaims([])
    setPhase("idle")
    setSummary(null)
    setExtractionMethod(null)
  }, [conversationId])

  // Clear stale verification state when a new response starts streaming (triggerKey resets to 0)
  const prevTriggerKey = useRef(triggerKey)
  useEffect(() => {
    if (prevTriggerKey.current !== 0 && triggerKey === 0) {
      setClaims([])
      setPhase("idle")
      setSummary(null)
      setExtractionMethod(null)
    }
    prevTriggerKey.current = triggerKey
  }, [triggerKey])

  useEffect(() => {
    if (!responseText || !conversationId || !enabled || triggerKey === 0) {
      return
    }

    // Abort any previous stream
    abortRef.current?.()

    setClaims([])
    setPhase("extracting")
    setSummary(null)
    setExtractionMethod(null)

    const { response, abort } = streamVerification(responseText, conversationId, undefined, modelRef.current, userQuery, historyRef.current)
    abortRef.current = abort

    let cancelled = false
    let receivedSummary = false

    // Timeout: abort verification if it takes too long (60s)
    const STREAM_TIMEOUT_MS = 60_000
    const timeoutId = setTimeout(() => {
      if (!cancelled) {
        cancelled = true
        abort()
        setPhase("error")
      }
    }, STREAM_TIMEOUT_MS)

    async function processStream() {
      try {
        const res = await response
        if (!res.ok || !res.body) {
          if (!cancelled) setPhase("error")
          return
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ""

        while (true) {
          const { done, value } = await reader.read()
          if (done || cancelled) break

          buffer += decoder.decode(value, { stream: true })

          // Parse SSE lines
          const lines = buffer.split("\n")
          buffer = lines.pop() ?? "" // Keep incomplete line in buffer

          for (const line of lines) {
            const trimmed = line.trim()
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
                  break

                case "claim_extracted":
                  setClaims((prev) => [
                    ...prev,
                    {
                      claim: event.claim,
                      claim_type: event.claim_type,
                      index: event.index,
                      status: "pending",
                    },
                  ])
                  setPhase("extracting")
                  break

                case "claim_verified":
                  setPhase("verifying")
                  setClaims((prev) =>
                    prev.map((c) =>
                      c.index === event.index
                        ? {
                            ...c,
                            claim: event.claim || c.claim,
                            claim_type: event.claim_type || c.claim_type,
                            status: event.status,
                            similarity: event.confidence,
                            source: event.source,
                            source_artifact_id: event.source_artifact_id,
                            source_domain: event.source_domain,
                            source_snippet: event.source_snippet,
                            source_urls: event.source_urls || [],
                            reason: event.reason,
                            verification_method: event.verification_method,
                            verification_model: event.verification_model,
                            verification_answer: event.verification_answer || undefined,
                          }
                        : c,
                    ),
                  )
                  break

                case "summary":
                  receivedSummary = true
                  setSummary({
                    verified: event.verified,
                    unverified: event.unverified,
                    uncertain: event.uncertain,
                    total: event.total,
                    overallConfidence: event.overall_confidence,
                    extractionMethod: event.extraction_method,
                  })
                  // Accumulate session-wide metrics
                  sessionRef.current.claimsChecked += event.total ?? 0
                  sessionRef.current.estCost += estimateVerificationCost(event.total ?? 0)
                  setSessionClaimsChecked(sessionRef.current.claimsChecked)
                  setSessionEstCost(sessionRef.current.estCost)
                  setPhase("done")
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

                case "error":
                  setPhase("error")
                  break
              }
            } catch {
              // Skip malformed JSON lines
            }
          }
        }

        // Stream ended — if no summary event was received, don't leave phase stuck
        if (!cancelled && !receivedSummary) {
          setPhase("error")
        }
      } catch (err) {
        if (!cancelled && !(err instanceof DOMException && (err as DOMException).name === "AbortError")) {
          setPhase("error")
        }
      } finally {
        clearTimeout(timeoutId)
      }
    }

    processStream()

    return () => {
      cancelled = true
      clearTimeout(timeoutId)
      abort()
    }
  }, [responseText, conversationId, enabled, triggerKey, userQuery])

  const verifiedCount = claims.filter((c) => c.status && c.status !== "pending").length
  const totalClaims = claims.length

  // Build a HallucinationReport-compatible object for status bar
  const report: HallucinationReport | null =
    phase === "done" && summary
      ? {
          conversation_id: conversationId ?? "",
          timestamp: new Date().toISOString(),
          skipped: summary.total === 0,
          extraction_method: summary.extractionMethod,
          claims: claims.map((c) => ({
            claim: c.claim,
            claim_type: c.claim_type,
            status: (c.status === "pending" ? "uncertain" : c.status) as "verified" | "unverified" | "uncertain" | "error",
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
          })),
          summary: {
            total: summary.total,
            verified: summary.verified,
            unverified: summary.unverified,
            uncertain: summary.uncertain,
          },
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
  }
}
