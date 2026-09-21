// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Frontend mirror of the backend ClaimVerification Pydantic model.
 *
 * Field-for-field match with:
 *   src/mcp/core/agents/hallucination/models.py::ClaimVerification
 *
 * Differences from the legacy HallucinationClaim in lib/types.ts:
 * - `confidence` replaces `similarity` as the primary numeric score
 * - `status` maps to the same backend enum (verified/unverified/uncertain/skipped/error)
 * - `sources` (plural) is the canonical field via `source_urls`; the
 *   singular `source_artifact_id` / `source_filename` remain for KB provenance
 *
 * The four linguistic bands derived here:
 * - "verified":   status === "verified" AND at least 1 source
 * - "partial":    status === "verified" with no strong source OR status === "uncertain"
 * - "refuted":    status === "unverified" from an independent check that
 *                 actively contradicted the claim (cross-model / web search)
 * - "unverified": status === "unverified" with no evidence either way
 *
 * "refuted" is a *derived* band, not a backend status: the ClaimVerification
 * enum below stays a field-for-field mirror of models.py, which has no
 * "refuted" member. The distinction lives in `verification_method`.
 */

import { getClaimDisplayStatus } from "@/lib/verification-utils"

export type ClaimStatus =
  | "verified"
  | "unverified"
  | "uncertain"
  | "skipped"
  | "error"

export type ClaimType = "factual" | "evasion" | "ignorance" | "citation"

export type VerificationBand = "verified" | "partial" | "refuted" | "unverified"

/**
 * Canonical per-claim verification type for frontend components.
 * Serialised from the backend ClaimVerification model.
 */
export interface ClaimVerificationFE {
  claim: string
  claim_type?: ClaimType
  status: ClaimStatus
  /** 0–1 confidence score (primary numeric signal). */
  confidence: number
  /**
   * Similarity score (0–1). Kept for backward compat with older backend
   * responses that omit `confidence`; components should prefer `confidence`.
   */
  similarity?: number
  reason?: string

  // Source provenance (KB path)
  source_artifact_id?: string
  source_filename?: string
  source_domain?: string
  source_snippet?: string

  // Source provenance (web/external path)
  source_urls?: string[]

  // Verifier metadata
  verification_method?: string
  verification_model?: string
  verification_answer?: string

  // NLI scores (set by kb_nli path)
  nli_entailment?: number
  nli_contradiction?: number

  // Flags
  memory_source?: boolean
  circular_source?: boolean

  // User signal (R.1)
  user_feedback?: "correct" | "incorrect"
}

/**
 * Derive the linguistic band for a claim.
 *
 * - "verified"   → status=verified with ≥1 source
 * - "partial"    → status=uncertain OR status=verified but no source
 * - "refuted"    → status=unverified from a check that contradicted the claim
 * - "unverified" → status=unverified with no evidence, OR error, OR skipped
 *
 * The refuted branch delegates to getClaimDisplayStatus so this badge and the
 * hallucination-audit report classify the same payload the same way. Only the
 * status + method are passed: claim_type routing (evasion / citation) belongs
 * to the audit surface, which has dedicated treatments for it, and has no band
 * of its own here.
 */
export function deriveBand(claim: ClaimVerificationFE): VerificationBand {
  const hasSource =
    !!(claim.source_artifact_id || (claim.source_urls?.length ?? 0) > 0)

  if (claim.status === "verified") {
    return hasSource ? "verified" : "partial"
  }
  if (claim.status === "uncertain") {
    return "partial"
  }
  if (getClaimDisplayStatus(claim.status, claim.verification_method) === "refuted") {
    return "refuted"
  }
  return "unverified"
}

/** Count sources for a claim. */
export function sourceCount(claim: ClaimVerificationFE): number {
  const urlCount = claim.source_urls?.length ?? 0
  const artifactCount = claim.source_artifact_id ? 1 : 0
  // source_urls may include the artifact URL — de-duplicate by taking max
  return Math.max(urlCount, artifactCount)
}
