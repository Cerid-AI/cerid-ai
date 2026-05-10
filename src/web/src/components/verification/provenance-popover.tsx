// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ProvenancePopover — hover-card content showing per-claim provenance detail.
 *
 * Power-user disclosure: confidence number (not shown at-rest), NLI verdict,
 * source artifact list, chunk hashes, and a "view source" link.
 *
 * Also hosts the per-claim thumbs feedback UI (Phase R.1).  The thumbs appear
 * only in the popover — the at-rest badge is unchanged.  No vote count is
 * shown; the rating is a personal signal, not a public tally.
 */

import { ExternalLink, ThumbsUp, ThumbsDown } from "lucide-react"
import type { ClaimVerificationFE } from "@/components/verification/types"
import { useClaimFeedback } from "@/hooks/use-claim-feedback"
import { UX_COPY } from "@/lib/ux-copy"
import { cn } from "@/lib/utils"

interface ProvenancePopoverProps {
  claim: ClaimVerificationFE
  /** Callback when the user clicks "view source" for an artifact. */
  onArtifactClick?: (artifactId: string) => void
  /**
   * Optional session identifier for per-claim feedback idempotency (R.1).
   * When provided, thumbs ratings are de-duplicated per session+claim.
   */
  sessionId?: string
}

/** Extract hostname for display. */
function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "")
  } catch {
    return url
  }
}

/** NLI verdict label from raw scores. */
function nliVerdict(entailment?: number, contradiction?: number): string | null {
  if (entailment == null) return null
  if (entailment >= 0.7) return "entailed"
  if (contradiction != null && contradiction >= 0.5) return "contradicted"
  return "neutral"
}

export function ProvenancePopover({
  claim,
  onArtifactClick,
  sessionId,
}: ProvenancePopoverProps) {
  const verdict = nliVerdict(claim.nli_entailment, claim.nli_contradiction)
  const sourceCount = claim.source_urls?.length ?? (claim.source_artifact_id ? 1 : 0)
  const { submit: submitFeedback, activeSentiment, isPending } = useClaimFeedback({ sessionId })

  return (
    <div className="space-y-2 text-xs">
      {/* Confidence row */}
      <div className="flex items-center justify-between gap-3">
        <span className="text-muted-foreground">
          {UX_COPY.verification.confidence(claim.confidence)}
        </span>
        {verdict && (
          <span
            className={cn(
              "rounded-full px-1.5 py-0.5 text-[10px] font-medium",
              verdict === "entailed" &&
                "bg-green-500/10 text-green-700 dark:text-green-400",
              verdict === "contradicted" &&
                "bg-red-500/10 text-red-700 dark:text-red-400",
              verdict === "neutral" &&
                "bg-muted text-muted-foreground",
            )}
          >
            NLI: {verdict}
          </span>
        )}
      </div>

      {/* Verification method */}
      {claim.verification_method && claim.verification_method !== "none" && (
        <p className="text-muted-foreground">
          Method:{" "}
          <span className="font-medium text-foreground">
            {claim.verification_method}
          </span>
        </p>
      )}

      {/* Source artifact */}
      {claim.source_filename && (
        <div className="space-y-0.5">
          <p className="font-medium text-muted-foreground">Source</p>
          {claim.source_artifact_id && onArtifactClick ? (
            <button
              className="text-primary hover:underline text-left"
              onClick={() => onArtifactClick(claim.source_artifact_id!)}
              aria-label={`View source: ${claim.source_filename}`}
            >
              {claim.source_filename}
            </button>
          ) : (
            <span>{claim.source_filename}</span>
          )}
          {claim.source_domain && (
            <p className="text-muted-foreground">{claim.source_domain}</p>
          )}
        </div>
      )}

      {/* Source snippet */}
      {claim.source_snippet && (
        <p className="line-clamp-3 italic text-muted-foreground/80 leading-relaxed">
          &ldquo;{claim.source_snippet.slice(0, 200)}&rdquo;
        </p>
      )}

      {/* External URLs */}
      {claim.source_urls && claim.source_urls.length > 0 && (
        <div className="space-y-0.5">
          <p className="font-medium text-muted-foreground">
            References ({sourceCount})
          </p>
          {claim.source_urls.slice(0, 5).map((url, i) => (
            <a
              key={i}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-primary hover:underline truncate"
              aria-label={`View source: ${hostname(url)}`}
            >
              <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
              {hostname(url)}
            </a>
          ))}
          {claim.source_urls.length > 5 && (
            <p className="text-muted-foreground">
              +{claim.source_urls.length - 5} more
            </p>
          )}
        </div>
      )}

      {/* View source CTA */}
      {claim.source_artifact_id && onArtifactClick && (
        <button
          className="mt-1 flex items-center gap-1 text-primary hover:underline"
          onClick={() => onArtifactClick(claim.source_artifact_id!)}
          aria-label={UX_COPY.verification.viewSource}
        >
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
          {UX_COPY.verification.viewSource}
        </button>
      )}

      {/* Fallback when no provenance at all */}
      {!claim.source_filename &&
        (!claim.source_urls || claim.source_urls.length === 0) &&
        !claim.source_artifact_id && (
          <p className="text-muted-foreground">
            {UX_COPY.verification.unverified}
          </p>
        )}

      {/* Per-claim thumbs feedback (Phase R.1).
          Visible only inside the popover — the at-rest badge is unchanged.
          No vote count shown — this is a personal signal, not a public tally. */}
      {claim.claim && (
        <div className="flex items-center gap-1 border-t border-border/50 pt-2 mt-2">
          <span className="mr-auto text-[10px] text-muted-foreground">
            {UX_COPY.feedback.rateThisClaim}
          </span>
          <button
            type="button"
            aria-label="Mark claim as correct"
            aria-pressed={activeSentiment === 1}
            disabled={isPending}
            onClick={() => void submitFeedback(claim.claim, 1)}
            className={cn(
              "rounded p-0.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              activeSentiment === 1
                ? "text-green-600 dark:text-green-400"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <ThumbsUp className="h-3 w-3" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Mark claim as incorrect"
            aria-pressed={activeSentiment === -1}
            disabled={isPending}
            onClick={() => void submitFeedback(claim.claim, -1)}
            className={cn(
              "rounded p-0.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              activeSentiment === -1
                ? "text-destructive"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <ThumbsDown className="h-3 w-3" aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  )
}
