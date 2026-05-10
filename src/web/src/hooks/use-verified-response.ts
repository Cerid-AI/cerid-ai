// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * useVerifiedResponse — convenience hook that bridges existing verification
 * hooks to the props shape consumed by <VerifiedResponse>.
 *
 * Returns `{ claims, streaming, error }` — the exact prop triplet that
 * <VerifiedResponse> accepts. Callers can also spread the return directly:
 *
 *   const vr = useVerifiedResponse(...)
 *   <VerifiedResponse {...vr} />
 *
 * Backward compatibility: this hook is a thin shim. The underlying hooks
 * (useVerificationOrchestrator, useVerificationStream) remain callable
 * directly and are NOT modified.
 *
 * Conversion note: the existing HallucinationClaim type has `similarity`
 * where ClaimVerificationFE has `confidence`. We map `similarity` →
 * `confidence` as a best-effort approximation until all backend responses
 * include the `confidence` field. When both are present, `confidence` wins.
 */

import { useMemo } from "react"
import type { HallucinationReport, StreamingClaim } from "@/lib/types"
import type { ClaimVerificationFE } from "@/components/verification/types"

interface UseVerifiedResponseOptions {
  /** Completed report from the orchestrator. */
  report: HallucinationReport | null
  /** Whether the verification stream is in flight. */
  loading: boolean
  /** Live streaming claims (present during active stream). */
  streamingClaims?: StreamingClaim[]
  /** Error from the verification pipeline. */
  error?: Error | null
}

interface UseVerifiedResponseReturn {
  claims: ClaimVerificationFE[]
  streaming: boolean
  error: Error | null | undefined
}

/**
 * Map a HallucinationClaim (or StreamingClaim) to ClaimVerificationFE.
 * `confidence` is preferred; falls back to `similarity` when absent.
 */
function toFE(
  raw: HallucinationReport["claims"][number] | StreamingClaim,
): ClaimVerificationFE {
  const similarity =
    "similarity" in raw && raw.similarity != null ? raw.similarity : 0
  // `confidence` may be surfaced as an extra field on extended claim objects
  const confidence =
    "confidence" in raw && typeof (raw as Record<string, unknown>).confidence === "number"
      ? (raw as Record<string, unknown>).confidence as number
      : similarity

  return {
    claim: raw.claim,
    claim_type: raw.claim_type as ClaimVerificationFE["claim_type"],
    status: (raw.status ?? "uncertain") as ClaimVerificationFE["status"],
    confidence,
    similarity,
    reason: raw.reason,
    source_artifact_id: raw.source_artifact_id,
    source_filename:
      "source_filename" in raw ? raw.source_filename : undefined,
    source_domain: raw.source_domain,
    source_snippet: raw.source_snippet,
    source_urls: raw.source_urls,
    verification_method: raw.verification_method,
    verification_model: raw.verification_model,
    verification_answer:
      "verification_answer" in raw ? raw.verification_answer : undefined,
    nli_entailment: raw.nli_entailment,
    nli_contradiction: raw.nli_contradiction,
    memory_source: raw.memory_source,
    circular_source: raw.circular_source,
    user_feedback:
      "user_feedback" in raw ? (raw as { user_feedback?: "correct" | "incorrect" }).user_feedback : undefined,
  }
}

export function useVerifiedResponse({
  report,
  loading,
  streamingClaims,
  error,
}: UseVerifiedResponseOptions): UseVerifiedResponseReturn {
  const claims = useMemo<ClaimVerificationFE[]>(() => {
    // While streaming: surface live claims (may be partial/pending)
    if (streamingClaims && streamingClaims.length > 0) {
      return streamingClaims
        .filter((c) => c.status && c.status !== "pending")
        .map(toFE)
    }
    // Settled: use the completed report
    if (report && !report.skipped && report.claims.length > 0) {
      return report.claims.map(toFE)
    }
    return []
  }, [report, streamingClaims])

  const streaming = loading || (
    streamingClaims != null &&
    streamingClaims.length > 0 &&
    streamingClaims.some((c) => !c.status || c.status === "pending")
  )

  return { claims, streaming, error }
}
