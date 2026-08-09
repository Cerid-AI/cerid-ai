// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { MCP_BASE, mcpHeaders, extractError } from "./common"

import type { HallucinationClaim } from "../types"

// --- Hallucination Detection ---

// E1 CR-065: saveVerificationReport was removed here — Sprint C moved report
// persistence server-side (verify_response_streaming persists to Neo4j and emits
// a "persisted" SSE event the stream hook now surfaces on failure). The FE helper
// was dead code with no callers; the POST /verification/save endpoint remains for
// external SDK consumers.

export function streamVerification(
  responseText: string,
  conversationId: string,
  threshold?: number,
  model?: string,
  userQuery?: string,
  conversationHistory?: Array<{ role: string; content: string }>,
  expertMode?: boolean,
  sourceArtifactIds?: string[],
): { response: Promise<Response>; abort: () => void } {
  const controller = new AbortController()
  const response = fetch(`${MCP_BASE}/agent/verify-stream`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      response_text: responseText,
      conversation_id: conversationId,
      ...(threshold !== undefined && { threshold }),
      ...(model && { model }),
      ...(userQuery && { user_query: userQuery }),
      ...(conversationHistory?.length && { conversation_history: conversationHistory }),
      ...(expertMode && { expert_mode: true }),
      ...(sourceArtifactIds?.length && { source_artifact_ids: sourceArtifactIds }),
    }),
    signal: controller.signal,
  })
  return { response, abort: () => controller.abort() }
}

export async function submitClaimFeedback(
  conversationId: string,
  claimIndex: number,
  correct: boolean,
): Promise<void> {
  const res = await fetch(`${MCP_BASE}/agent/hallucination/feedback`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      conversation_id: conversationId,
      claim_index: claimIndex,
      correct,
    }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Claim feedback failed: ${res.status}`))
}

/**
 * Re-verify a single claim with expert mode (Grok 4).
 * Sends the claim text as the response, reads the SSE stream for the
 * first `claim_verified` event, and returns the updated claim data.
 */
type ClaimVerificationResult = Pick<HallucinationClaim, "status" | "similarity" | "source_filename" | "source_artifact_id" | "source_domain" | "source_snippet" | "source_urls" | "reason" | "verification_method" | "verification_model" | "verification_answer">

export type { ClaimVerificationResult }

export async function verifySingleClaim(
  claimText: string,
  conversationId: string,
  claimIndex: number,
): Promise<ClaimVerificationResult | null> {
  const res = await fetch(`${MCP_BASE}/agent/verify-stream`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      response_text: claimText,
      conversation_id: conversationId,
      expert_mode: true,
      // E1 CR-019: this is a per-claim retry — the backend must MERGE the fresh
      // verdict into the durable N-claim report at this index, not replace the
      // whole report with a 1-claim one (which wiped the other claims' verdicts
      // and broke their feedback indices).
      single_claim_index: claimIndex,
    }),
  })
  if (!res.ok || !res.body) return null

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split("\n")
      buffer = lines.pop() ?? ""

      for (const line of lines) {
        const trimmed = line.trim()
        if (trimmed.startsWith(":") || !trimmed.startsWith("data:")) continue
        const jsonStr = trimmed.slice(5).trim()
        if (!jsonStr) continue
        try {
          const event = JSON.parse(jsonStr)
          if (event.type === "claim_verified") {
            reader.cancel().catch(() => {})
            return {
              status: (event.status ?? "uncertain") as HallucinationClaim["status"],
              similarity: event.confidence ?? 0,
              source_filename: event.source || undefined,
              source_artifact_id: event.source_artifact_id || undefined,
              source_domain: event.source_domain || undefined,
              source_snippet: event.source_snippet || undefined,
              source_urls: event.source_urls || [],
              // Timeout verdicts nest the reason under verdict — same
              // fallback as the streaming hook's claim_verified handler.
              reason: event.reason || event.verdict?.reason || undefined,
              verification_method: (event.verification_method || undefined) as HallucinationClaim["verification_method"],
              verification_model: event.verification_model || undefined,
              verification_answer: event.verification_answer || undefined,
            }
          }
        } catch { /* skip malformed JSON */ }
      }
    }
  } catch { /* stream error */ }

  return null
}
