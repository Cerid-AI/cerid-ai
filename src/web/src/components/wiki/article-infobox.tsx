// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useRef } from "react"
import { ExternalLink, Network } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { DomainBadge } from "@/components/ui/domain-badge"
import { TrustBandBadge, type TrustState } from "@/components/ui/trust-band-badge"
import { MiniGraph } from "./mini-graph"
import type { WikiEntityPage, ConfidenceBand } from "@/lib/types/wiki"

// ---------------------------------------------------------------------------
// Article infobox — Wikipedia-style data summary card.
//
// Design rules (binding):
//   - ≤8 rows; strict omit-if-absent (never render a row from a coalesced default).
//   - Verification band is a PERMANENT row — never omitted.
//   - Plain Card, no glassmorphism (amendment #6).
//   - MiniGraph thumbnail as the infobox image slot (amendment #3).
//   - Clicking the MiniGraph thumbnail scrolls to the anchored Activity section.
//   - Domain row links to domain landing (?domain= param via onNavigateToDomain).
//   - Community row links to concept page (onNavigateToConcept).
//   - Sources row anchor-links to #references.
// ---------------------------------------------------------------------------

function confidenceBandToTrust(band: ConfidenceBand): TrustState {
  switch (band) {
    case "high": return "verified"
    case "medium": return "partial"
    case "low": return "unverified"
    default: return "unknown"
  }
}

function formatRelativeTime(iso: string | null): string | null {
  if (!iso) return null
  try {
    const ms = Date.parse(iso)
    if (Number.isNaN(ms)) return null
    const seconds = Math.floor((Date.now() - ms) / 1000)
    if (seconds < 60) return "just now"
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
    return `${Math.floor(seconds / 86400)}d ago`
  } catch {
    return null
  }
}

function titleCase(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

interface InfoboxRowProps {
  label: string
  children: React.ReactNode
}

function InfoboxRow({ label, children }: InfoboxRowProps) {
  return (
    <tr className="border-b border-border/40 last:border-0">
      <th
        scope="row"
        className="w-24 py-1.5 pr-3 text-left text-label-xs font-medium text-muted-foreground align-top"
      >
        {label}
      </th>
      <td className="py-1.5 text-xs text-foreground">{children}</td>
    </tr>
  )
}

export interface ArticleInfoboxProps {
  page: WikiEntityPage
  /** Called when the domain row is clicked. */
  onNavigateToDomain: (domain: string) => void
  /** Called when the community row is clicked. */
  onNavigateToConcept: (communityId: string) => void
  /** Cross-pane navigation to the Atlas ego-network for this entity. */
  onOpenAtlas?: () => void
}

export function ArticleInfobox({
  page,
  onNavigateToDomain,
  onNavigateToConcept,
  onOpenAtlas,
}: ArticleInfoboxProps) {
  const activitySectionRef = useRef<HTMLElement | null>(null)

  function scrollToActivity() {
    if (typeof document === "undefined") return
    const target = document.getElementById("wiki-section-activity")
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "start" })
    }
  }

  const trustState = confidenceBandToTrust(page.confidence_band)
  const relativeUpdated = formatRelativeTime(page.last_updated_at)
  const relativeRefreshDue = page.refresh_status === "due" && page.next_refresh_due
    ? formatRelativeTime(page.next_refresh_due)
    : null

  return (
    <Card
      className="gap-0 py-0 text-sm"
      aria-label={`${page.name} — article infobox`}
    >
      {/* MiniGraph thumbnail — infobox image slot (amendment #3) */}
      <div
        className="relative cursor-pointer overflow-hidden rounded-t-xl border-b border-border/40"
        onClick={scrollToActivity}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault()
            scrollToActivity()
          }
        }}
        role="button"
        tabIndex={0}
        aria-label={`Open ${page.name} activity graph`}
        // drift-allowed: fixed height for the thumbnail slot
        style={{ height: "140px" }}
        ref={(el) => {
          activitySectionRef.current = el as unknown as HTMLElement
        }}
      >
        <div className="pointer-events-none h-full w-full">
          <MiniGraph
            entitySlug={page.slug}
            entityName={page.name}
          />
        </div>
        <div
          className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-1 bg-card/80 py-1 text-label-xs text-muted-foreground"
          aria-hidden="true"
        >
          <span>Activity graph — click to expand</span>
        </div>
      </div>

      <CardContent className="px-3 py-2">
        <table className="w-full border-collapse" aria-label={`${page.name} summary data`}>
          <tbody>
            {/* Row 1: Type — only when present */}
            {page.entity_type && (
              <InfoboxRow label="Type">
                <Badge variant="outline" className="font-mono text-label-xxs uppercase">
                  {page.entity_type}
                </Badge>
              </InfoboxRow>
            )}

            {/* Row 2: Domain — only when present; links to domain landing */}
            {page.primary_domain && (
              <InfoboxRow label="Domain">
                <button
                  type="button"
                  onClick={() => onNavigateToDomain(page.primary_domain!)}
                  className="inline-flex items-center gap-1 hover:opacity-80"
                  aria-label={`View ${titleCase(page.primary_domain)} domain`}
                >
                  <DomainBadge domain={page.primary_domain} />
                  {page.primary_subcategory && (
                    <span className="text-muted-foreground">
                      › {titleCase(page.primary_subcategory)}
                    </span>
                  )}
                </button>
              </InfoboxRow>
            )}

            {/* Row 3: Community — only when community_id is present */}
            {page.community_id && page.community_label && (
              <InfoboxRow label="Community">
                <button
                  type="button"
                  onClick={() => onNavigateToConcept(page.community_id!)}
                  className="text-brand underline-offset-2 hover:underline"
                  title={page.community_id}
                  aria-label={`Open ${page.community_label} concept page`}
                >
                  {page.community_label}
                </button>
              </InfoboxRow>
            )}

            {/* Row 4: Mentions — only when non-zero */}
            {typeof page.mention_count === "number" && page.mention_count > 0 && (
              <InfoboxRow label="Mentions">
                {page.mention_count.toLocaleString()}
              </InfoboxRow>
            )}

            {/* Row 5: Verification — PERMANENT ROW */}
            <InfoboxRow label="Verification">
              <TrustBandBadge
                trust={trustState}
                corroboratingCount={page.source_artifacts.length}
                contradictionCount={page.contradictions.length}
              />
            </InfoboxRow>

            {/* Row 6: Updated — only when last_updated_at is present */}
            {relativeUpdated && (
              <InfoboxRow label="Updated">
                <span>{relativeUpdated}</span>
                {page.refresh_status === "running" && (
                  <span className="ml-1 text-label-xs text-primary">· updating…</span>
                )}
                {page.refresh_status === "due" && relativeRefreshDue && (
                  <span className="ml-1 text-label-xs text-muted-foreground">
                    · next sweep {relativeRefreshDue}
                  </span>
                )}
              </InfoboxRow>
            )}

            {/* Row 7: External — only when at least one external ref with a URL */}
            {page.external_references.length > 0 && page.external_references[0].url && (
              <InfoboxRow label="External">
                <a
                  href={page.external_references[0].url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-brand hover:underline"
                  aria-label={`${page.external_references[0].source_display} (opens in new tab)`}
                >
                  {page.external_references[0].source_display}
                  <ExternalLink className="h-3 w-3" aria-hidden="true" />
                </a>
              </InfoboxRow>
            )}

            {/* Row 8: Sources — only when source_artifacts are present */}
            {page.source_artifacts.length > 0 && (
              <InfoboxRow label="Sources">
                <a
                  href="#references"
                  className="text-brand hover:underline"
                  aria-label={`${page.source_artifacts.length} source ${page.source_artifacts.length === 1 ? "artifact" : "artifacts"} — jump to references`}
                >
                  {page.source_artifacts.length}{" "}
                  {page.source_artifacts.length === 1 ? "artifact" : "artifacts"}
                </a>
              </InfoboxRow>
            )}
          </tbody>
        </table>
        {onOpenAtlas && (
          <button
            type="button"
            onClick={onOpenAtlas}
            className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-md border border-border/60 py-1.5 text-label-xs text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
            aria-label={`Open ${page.name} in Atlas`}
          >
            <Network className="h-3 w-3" aria-hidden="true" />
            Open in Atlas
          </button>
        )}
      </CardContent>
    </Card>
  )
}
