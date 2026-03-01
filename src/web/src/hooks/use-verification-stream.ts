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
}

export interface UseVerificationStreamReturn {
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
  /** Converted to HallucinationReport format for status bar compatibility. */
  report: HallucinationReport | null
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
): UseVerificationStreamReturn {
  const [claims, setClaims] = useState<StreamingClaim[]>([])
  const [phase, setPhase] = useState<VerificationPhase>("idle")
  const [summary, setSummary] = useState<StreamingSummary | null>(null)
  const abortRef = useRef<(() => void) | null>(null)

  // Reset state when conversation changes
  useEffect(() => {
    setClaims([])
    setPhase("idle")
    setSummary(null)
  }, [conversationId])

  useEffect(() => {
    if (!responseText || !conversationId || !enabled || triggerKey === 0) {
      return
    }

    // Abort any previous stream
    abortRef.current?.()

    setClaims([])
    setPhase("extracting")
    setSummary(null)

    const { response, abort } = streamVerification(responseText, conversationId)
    abortRef.current = abort

    let cancelled = false

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
                case "claim_extracted":
                  setClaims((prev) => [
                    ...prev,
                    {
                      claim: event.claim,
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
                            status: event.status,
                            confidence: event.confidence,
                            source: event.source,
                            reason: event.reason,
                          }
                        : c,
                    ),
                  )
                  break

                case "summary":
                  setSummary({
                    verified: event.verified,
                    unverified: event.unverified,
                    uncertain: event.uncertain,
                    total: event.total,
                    overallConfidence: event.overall_confidence,
                  })
                  setPhase("done")
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
      } catch (err) {
        if (!cancelled && !(err instanceof DOMException && (err as DOMException).name === "AbortError")) {
          setPhase("error")
        }
      }
    }

    processStream()

    return () => {
      cancelled = true
      abort()
    }
  }, [responseText, conversationId, enabled, triggerKey])

  const verifiedCount = claims.filter((c) => c.status && c.status !== "pending").length
  const totalClaims = claims.length

  // Build a HallucinationReport-compatible object for status bar
  const report: HallucinationReport | null =
    phase === "done" && summary
      ? {
          conversation_id: conversationId ?? "",
          timestamp: new Date().toISOString(),
          skipped: summary.total === 0,
          claims: claims.map((c) => ({
            claim: c.claim,
            status: (c.status === "pending" ? "uncertain" : c.status) as "verified" | "unverified" | "uncertain" | "error",
            similarity: c.confidence ?? 0,
            source_filename: c.source || undefined,
            reason: c.reason || undefined,
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
    report,
  }
}
