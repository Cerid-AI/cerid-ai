// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Shared verification display utilities.
 *
 * Maps backend verification status + method into user-facing display labels.
 * Key distinction: "unverified" from the backend can mean two different things:
 * - **Refuted**: Cross-model or web-search actively found the claim to be wrong.
 * - **Unverified**: KB simply has no matching evidence (softer, not a failure).
 *
 * This mapping is frontend-only — zero backend changes needed.
 */

export type ClaimDisplayStatus = "verified" | "refuted" | "unverified" | "uncertain" | "pending"

/**
 * Derive the user-facing display status from backend status + verification method.
 *
 * - verified → verified (green)
 * - unverified + cross_model/web_search → refuted (red, actively wrong)
 * - unverified + kb/none → unverified (yellow, no evidence)
 * - uncertain → uncertain (gray, checked but inconclusive)
 * - pending → pending (gray, spinning)
 */
export function getClaimDisplayStatus(
  status: string,
  verificationMethod?: string,
): ClaimDisplayStatus {
  if (status === "verified") return "verified"
  if (
    status === "unverified" &&
    (verificationMethod === "cross_model" || verificationMethod === "web_search")
  )
    return "refuted" // actively found wrong by another model
  if (status === "unverified") return "unverified" // no KB evidence (softer)
  if (status === "uncertain") return "uncertain"
  if (status === "pending") return "pending"
  return "uncertain"
}
