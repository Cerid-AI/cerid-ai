// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Canonical UX copy strings for verification surfaces.
 *
 * All user-visible verification strings MUST come from here.
 * Do NOT inline verification copy in component files — add it here and import.
 *
 * See also: docs/UX_COPY.md for the canonical reference document.
 */

export const UX_COPY = {
  /** Per-claim user feedback strings (Phase R.1). */
  feedback: {
    /** Prompt label next to the thumbs buttons in the provenance popover. */
    rateThisClaim: "Rate this claim",
  },
  verification: {
    /** "Verified by {n} source(s)" — substitute n. */
    verifiedByN: (n: number) =>
      `Verified by ${n} source${n === 1 ? "" : "s"}`,

    /** Short form label for the "verified" band — used in badges. */
    verified: (n: number) => `Verified by ${n} source${n === 1 ? "" : "s"}`,

    /** Short form label for the "partial" band. */
    partial: "Partial source",

    /** Short form label for the "unverified" band. */
    unverified: "No source found for this claim",

    /** Compact label for "unverified" band (badge text). */
    noSource: "No source",

    /** Spinner label shown during live verification. */
    verifying: "Verifying…",

    /** Error when the source cannot be fetched. */
    sourceUnreachable: "Source unreachable — try again",

    /** Confidence score label with value substituted. */
    confidence: (n: number) => `Confidence: ${n.toFixed(2)}`,

    /** Link label to jump to source artifact. */
    viewSource: "View source",

    /** aria-label for a verified badge: "Claim verified by 2 sources" */
    ariaVerified: (n: number) =>
      `Claim verified by ${n} source${n === 1 ? "" : "s"}`,

    /** aria-label for a partial badge */
    ariaPartial: "Claim has partial source",

    /** aria-label for an unverified badge */
    ariaUnverified: "Claim has no source",
  },
} as const
