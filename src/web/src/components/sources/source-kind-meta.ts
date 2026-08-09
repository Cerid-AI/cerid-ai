// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Extended /sources/kinds metadata (beta triage P0-C).
 *
 * The backend now ships two additional fields on SourceKindMeta plus a
 * fourth availability value:
 *
 *   - availability "requires_desktop" — the kind is implemented but its
 *     desktop helper/daemon (ceridmail, ceridreminders, clipboard daemon)
 *     is not present, so connect() would 422.
 *   - requires_desktop — true for helper-backed kinds regardless of
 *     current helper presence.
 *   - allowed_roots — folder kind only: the container-side roots a
 *     watched folder path must live under.
 *
 * Kept as a local extension of the base API type until the SDK types are
 * regenerated; SourceKindMeta payloads are assignable to this shape.
 */

import type { SourceKindMeta } from "@/lib/api/sources"

export type SourceKindAvailability =
  | "available"
  | "oauth"
  | "coming_soon"
  | "requires_desktop"

export interface SourceKindMetaExt extends Omit<SourceKindMeta, "availability"> {
  availability?: SourceKindAvailability
  requires_desktop?: boolean
  allowed_roots?: string[]
}
