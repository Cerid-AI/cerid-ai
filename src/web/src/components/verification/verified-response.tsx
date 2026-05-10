// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * VerifiedResponse — canonical component for rendering per-claim verification.
 *
 * Four states:
 *   idle      — no claims, no streaming, no error → renders nothing
 *   streaming — skeleton placeholder + spinner while claims are arriving
 *   settled   — full per-claim badges (one ClaimBadge per claim)
 *   error     — shadcn Alert with a descriptive message
 *
 * Props:
 *   claims    — array of ClaimVerificationFE objects (per-claim, never bundled)
 *   streaming — true while the verification stream is active
 *   error     — Error object if verification failed
 *
 * Design constraints (from plan + CLAUDE.md):
 * - No composite score prop; no trustThreshold prop
 * - All primitives from @/components/ui/*
 * - Lucide icons only
 * - No hex literals or style={{}} except dynamically-computed layout
 * - Radix transitions only (via hover-card)
 * - WCAG 2.1 AA: keyboard navigable (Tab into each badge → HoverCard opens)
 */

import { Loader2, AlertCircle } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { ClaimBadge } from "@/components/verification/claim-badge"
import type { ClaimVerificationFE } from "@/components/verification/types"
import { UX_COPY } from "@/lib/ux-copy"

export type VerifiedResponseState = "idle" | "streaming" | "settled" | "error"

interface VerifiedResponseProps {
  /** Per-claim verification data. */
  claims: ClaimVerificationFE[]
  /** True while the verification stream is active (skeleton mode). */
  streaming?: boolean
  /** Error from the verification pipeline. */
  error?: Error | null
  /** Callback to navigate to/open a source artifact. */
  onArtifactClick?: (artifactId: string) => void
}

/** Derive the four-state enum from props. */
function deriveState(
  claims: ClaimVerificationFE[],
  streaming: boolean,
  error: Error | null | undefined,
): VerifiedResponseState {
  if (error) return "error"
  if (streaming) return "streaming"
  if (claims.length > 0) return "settled"
  return "idle"
}

export function VerifiedResponse({
  claims,
  streaming = false,
  error = null,
  onArtifactClick,
}: VerifiedResponseProps) {
  const state = deriveState(claims, streaming, error)

  // idle — render nothing; callers choose whether to show a placeholder
  if (state === "idle") return null

  // error — shadcn Alert
  if (state === "error") {
    return (
      <div
        className="mt-2"
        data-verification-state="error"
        role="status"
        aria-live="polite"
      >
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertDescription>
            {UX_COPY.verification.sourceUnreachable}
            {error?.message ? ` — ${error.message}` : ""}
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  // streaming — skeleton placeholders + spinner
  if (state === "streaming") {
    return (
      <div
        className="mt-2 flex flex-wrap items-center gap-2"
        data-verification-state="streaming"
        role="status"
        aria-live="polite"
        aria-label={UX_COPY.verification.verifying}
      >
        <Loader2
          className="h-3.5 w-3.5 animate-spin text-muted-foreground"
          aria-hidden="true"
        />
        <span className="sr-only">{UX_COPY.verification.verifying}</span>
        <Skeleton className="h-6 w-28 rounded-full" />
        <Skeleton className="h-6 w-20 rounded-full" />
        <Skeleton className="h-6 w-24 rounded-full" />
      </div>
    )
  }

  // settled — one ClaimBadge per claim
  return (
    <div
      className="mt-2 flex flex-wrap items-center gap-1.5"
      data-verification-state="settled"
      role="list"
      aria-label="Claim verification results"
    >
      {claims.map((claim, i) => (
        <div key={i} role="listitem">
          <ClaimBadge claim={claim} onArtifactClick={onArtifactClick} />
        </div>
      ))}
    </div>
  )
}
