// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * TypeScript value objects for the Briefs read API (Task 2.1a/2.2).
 *
 * Mirror the Pydantic models in app.routers.briefs:
 *   ClaimView    → BriefClaim
 *   BriefSection → BriefSection
 *   BriefView    → Brief
 */

export type BriefKind = "daily" | "weekly"

/** A single claim cited by a brief. `source_ids` are KB artifact ids, not URLs. */
export interface BriefClaim {
  text: string
  band: "verified" | "partial" | "unverified"
  source_ids: string[]
}

/** One named section of brief markdown (e.g. "CONNECTIONS"). */
export interface BriefSection {
  title: string
  body: string
}

/** A rendered brief: sections plus the claims it cites. */
export interface Brief {
  id: string
  kind: BriefKind
  generated_at: string
  sections: BriefSection[]
  claims: BriefClaim[]
  /** UX-18 — substantial data landed after generation; the brief describes a
      different day than the one that happened. Optional for older backends. */
  stale?: boolean
  new_items_since_generation?: number
}
