// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Adapts a backend BriefClaim (text/band/source_ids) into the
 * ClaimVerificationFE shape `<VerifiedResponse>` expects.
 *
 * ClaimVerificationFE has no `band` field — the badge colour is derived by
 * `deriveBand()` from `status` + presence of a source (see
 * components/verification/types.ts). A `verified` band with no
 * `source_ids` therefore honestly degrades to an amber "partial" badge
 * (no viewable provenance) rather than fabricating a source — do not
 * work around that here.
 */

import type { ClaimVerificationFE, ClaimStatus } from "@/components/verification/types"
import type { BriefClaim } from "@/lib/types/brief"

const STATUS_BY_BAND: Record<BriefClaim["band"], ClaimStatus> = {
  verified: "verified",
  partial: "uncertain",
  unverified: "unverified",
}

const CONFIDENCE_BY_BAND: Record<BriefClaim["band"], number> = {
  verified: 1,
  partial: 0.5,
  unverified: 0,
}

export function briefClaimToFE(claim: BriefClaim): ClaimVerificationFE {
  return {
    claim: claim.text,
    status: STATUS_BY_BAND[claim.band],
    confidence: CONFIDENCE_BY_BAND[claim.band],
    source_artifact_id: claim.source_ids[0],
  }
}
