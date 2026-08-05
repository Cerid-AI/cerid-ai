// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Per-claim user feedback API client (Phase R.1).
 *
 * Calls POST /sdk/v1/feedback — the stable SDK v1 endpoint that persists
 * user thumbs ratings as Neo4j RATED edges.
 *
 * Design constraints from the v0.92 plan:
 * - Feedback is **per-claim**, never bundled.
 * - The rolling agreement metric is operator-facing only; no vote tally
 *   is returned to or displayed for end-users.
 * - Sentiment is an integer: 1=positive, 0=neutral, -1=negative.
 */

import { MCP_BASE, mcpHeaders, extractError } from "./common"

export type ClaimSentiment = 1 | 0 | -1

export interface ClaimFeedbackPayload {
  claim_id: string
  /** 1 = positive/correct, 0 = neutral, -1 = negative/incorrect */
  sentiment: ClaimSentiment
  user_id?: string
  session_id?: string
  comment?: string
}

export interface ClaimFeedbackResponse {
  ok: boolean
  rating_id: string
}

/**
 * POST /sdk/v1/feedback
 *
 * Submit a user rating for a single verified claim.  Idempotent per
 * (claim_id, user_id) or (claim_id, session_id): re-rating the same
 * claim updates the existing edge instead of creating a duplicate.
 *
 * Throws on non-2xx responses.
 */
export async function submitClaimFeedbackV2(
  payload: ClaimFeedbackPayload,
): Promise<ClaimFeedbackResponse> {
  const res = await fetch(`${MCP_BASE}/sdk/v1/feedback`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    throw new Error(
      await extractError(res, `Claim feedback failed: ${res.status}`),
    )
  }
  return res.json() as Promise<ClaimFeedbackResponse>
}
