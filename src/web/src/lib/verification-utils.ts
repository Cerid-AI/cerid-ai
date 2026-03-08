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

export type ClaimDisplayStatus = "verified" | "refuted" | "unverified" | "uncertain" | "pending" | "evasion" | "citation"

export interface ClaimSpan {
  start: number
  end: number
  claim: string
  displayStatus: ClaimDisplayStatus
}

interface ClaimLike {
  claim: string
  status: string
  claim_type?: string
  verification_method?: string
}

/** Normalize whitespace: collapse runs of whitespace into single spaces, trim. */
function normalizeWS(s: string): string {
  return s.replace(/\s+/g, " ").trim()
}

/**
 * Match verification claims to positions in the response text.
 * Returns sorted, non-overlapping ClaimSpan[].
 */
export function matchClaimsToText(text: string, claims: ClaimLike[]): ClaimSpan[] {
  if (!text || !claims || claims.length === 0) return []

  const normText = normalizeWS(text)
  const normLower = normText.toLowerCase()
  const rawSpans: ClaimSpan[] = []

  for (const c of claims) {
    const normClaim = normalizeWS(c.claim)
    if (!normClaim) continue

    const displayStatus = getClaimDisplayStatus(c.status, c.verification_method, c.claim_type)

    // Try exact substring match (case-insensitive)
    const claimLower = normClaim.toLowerCase()
    let idx = normLower.indexOf(claimLower)
    if (idx >= 0) {
      rawSpans.push({ start: idx, end: idx + claimLower.length, claim: c.claim, displayStatus })
      continue
    }

    // Fallback: match first 5 significant words (>2 chars)
    const words = normClaim.split(/\s+/).filter((w) => w.length > 2)
    const prefix = words.slice(0, 5).join(" ").toLowerCase()
    if (prefix.length >= 8) {
      idx = normLower.indexOf(prefix)
      if (idx >= 0) {
        rawSpans.push({ start: idx, end: idx + prefix.length, claim: c.claim, displayStatus })
      }
    }
  }

  // Sort by start position, then by length (longer first)
  rawSpans.sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start))

  // De-overlap: keep first span when overlapping
  const result: ClaimSpan[] = []
  let lastEnd = -1
  for (const span of rawSpans) {
    if (span.start >= lastEnd) {
      result.push(span)
      lastEnd = span.end
    }
  }

  return result
}

/**
 * Derive the user-facing display status from backend status + verification method.
 *
 * - evasion claim_type → evasion (orange, model deflected)
 * - citation claim_type → citation (purple, source verification)
 * - verified → verified (green)
 * - unverified + cross_model/web_search → refuted (red, actively wrong)
 * - unverified + kb/none → unverified (yellow, no evidence)
 * - uncertain → uncertain (gray, checked but inconclusive)
 * - pending → pending (gray, spinning)
 */
export function getClaimDisplayStatus(
  status: string,
  verificationMethod?: string,
  claimType?: string,
): ClaimDisplayStatus {
  // Evasion claims get special orange treatment regardless of verification outcome
  if (claimType === "evasion") return "evasion"
  // Citation verification gets purple treatment
  if (claimType === "citation") return "citation"
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

/** Shared display-status → color classes map used across claim UI components. */
export const DISPLAY_STATUS_COLORS: Record<ClaimDisplayStatus | "error", string> = {
  verified: "bg-green-500/20 text-green-400 border-green-500/30",
  refuted: "bg-red-500/20 text-red-400 border-red-500/30",
  unverified: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  evasion: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  citation: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  uncertain: "bg-muted/50 text-muted-foreground border-border",
  pending: "bg-muted text-muted-foreground border-border",
  error: "bg-muted text-muted-foreground",
}

/** Verification method → human-readable label. */
export function verificationMethodLabel(method?: string): string | null {
  if (!method || method === "kb") return null
  if (method === "cross_model") return "cross-model"
  if (method === "web_search") return "web search"
  if (method === "cross_model_failed") return "cross-model (failed)"
  if (method === "web_search_failed") return "web search (failed)"
  return method
}

/** Verification method → badge color classes. */
export function verificationMethodColor(method?: string): string {
  if (method === "cross_model") return "bg-purple-500/15 text-purple-400 border-purple-500/30"
  if (method === "web_search") return "bg-blue-500/15 text-blue-400 border-blue-500/30"
  return "bg-muted text-muted-foreground border-border"
}
