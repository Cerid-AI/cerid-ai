// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ExternalReferencesSection — Phase API.3
 *
 * Renders a clearly-labeled "External references" section with one card per
 * reference.  Returns null when refs is empty — the section is never shown
 * for entities that have not been enriched.
 *
 * Structural invariant: external references are visually distinct from
 * internal "Source artifacts".  This section uses a different background
 * tone (muted/10 vs muted/20 for source artifacts) and carries an explicit
 * "External references" heading so users always know the provenance.
 */

import { ExternalLink } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import type { ExternalReference } from "@/lib/types/wiki"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatFetchedAt(iso: string): string {
  try {
    const ms = Date.parse(iso)
    if (Number.isNaN(ms)) return iso
    const seconds = Math.floor((Date.now() - ms) / 1000)
    if (seconds < 60) return "just now"
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
    return `${Math.floor(seconds / 86400)}d ago`
  } catch {
    return iso
  }
}

// ---------------------------------------------------------------------------
// Single reference card
// ---------------------------------------------------------------------------

interface ReferenceCardProps {
  reference: ExternalReference
}

function ReferenceCard({ reference }: ReferenceCardProps) {
  const cardClasses =
    "flex items-start gap-3 rounded-md border border-border bg-muted/10 px-3 py-2 transition-colors"

  const body = (
    <>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="secondary" className="shrink-0 text-label-xs font-medium">
            {reference.source_display}
          </Badge>
          {reference.title && (
            <span className="truncate text-sm font-medium text-foreground">
              {reference.title}
            </span>
          )}
        </div>
        {reference.snippet && (
          <p className="line-clamp-2 text-xs text-muted-foreground">
            {reference.snippet}
          </p>
        )}
        <p className="text-label-xs text-muted-foreground/70">
          Fetched {formatFetchedAt(reference.fetched_at)}
        </p>
      </div>
      {reference.url && (
        <ExternalLink
          className="h-4 w-4 shrink-0 text-muted-foreground group-hover:text-foreground transition-colors"
          aria-hidden="true"
        />
      )}
    </>
  )

  // V-P2.6: when the reference has a URL, the entire card is the tap target
  // (44×44+ minimum). Without a URL we fall back to a plain non-interactive div.
  if (reference.url) {
    return (
      <a
        href={reference.url}
        target="_blank"
        rel="noopener noreferrer"
        className={`group ${cardClasses} hover:bg-muted/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1`}
        data-testid="external-reference-card"
        aria-label={`Open ${reference.title ?? reference.source_display} on ${reference.source_display} (opens in new tab)`}
      >
        {body}
      </a>
    )
  }

  return (
    <div className={cardClasses} data-testid="external-reference-card">
      {body}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Section
// ---------------------------------------------------------------------------

interface ExternalReferencesSectionProps {
  refs: ExternalReference[]
}

/**
 * Renders the "External references" section for a wiki entity page.
 *
 * Returns null when refs is empty — callers do not need to guard.
 *
 * The "External references" heading is the contract: it always appears so
 * users know they are looking at data from external public APIs, not from
 * Cerid's internal knowledge corpus.
 */
export function ExternalReferencesSection({ refs }: ExternalReferencesSectionProps) {
  if (refs.length === 0) return null

  return (
    <section aria-labelledby="wiki-ext-refs-heading">
      <h2
        id="wiki-ext-refs-heading"
        className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
      >
        External references
      </h2>
      <div className="space-y-2">
        {refs.map((reference) => (
          <ReferenceCard
            key={`${reference.source}:${reference.title}`}
            reference={reference}
          />
        ))}
      </div>
    </section>
  )
}
