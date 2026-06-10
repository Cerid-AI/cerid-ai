// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { RefreshCw } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { PaneError } from "@/components/ui/pane-error"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/ui/empty-state"
import { DomainBadge } from "@/components/ui/domain-badge"
import { TrustBandBadge, type TrustState } from "@/components/ui/trust-band-badge"
import { ContradictionItem } from "./contradiction-item"
import { ExternalReferencesSection } from "./external-references-section"
import { ProvenanceMarker, type ProvenanceKind } from "./provenance-marker"
import { MiniGraph } from "./mini-graph"
import { MentionSparkline } from "./mention-sparkline"
import { ProvenanceSankey } from "./provenance-sankey"
import { ContradictionLink } from "./contradiction-link"
import { useWikiEntity } from "@/hooks/use-wiki-entities"
import { useNavigation } from "@/contexts/navigation-context"
import { BookOpen } from "lucide-react"
import { communitySlot } from "@/components/subjects/timeline/stratigraph/strata-layout"
import { resolveMapTokens } from "@/components/subjects/constellation/map/community-layer"
import type { ConfidenceBand } from "@/lib/types/wiki"

interface EntityDetailViewProps {
  slug: string
  onSelectRelated: (slug: string) => void
}

function formatLastUpdated(iso: string | null): string | null {
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

function isRefreshOverdue(nextRefreshDue: string | null): boolean {
  if (!nextRefreshDue) return false
  try {
    return Date.parse(nextRefreshDue) < Date.now()
  } catch {
    return false
  }
}

function confidenceBandToTrust(band: ConfidenceBand): TrustState {
  switch (band) {
    case "high": return "verified"
    case "medium": return "partial"
    case "low": return "unverified"
    default: return "unknown"
  }
}

// Resolve community hue color for the identity rail + swatch.
// Falls back to clusterOther when communityId is null/undefined.
function resolveCommunityHue(communityId: string | null | undefined): string {
  // drift-allowed: runtime token resolution — community color resolved from CSS tokens
  if (typeof document === "undefined") return "var(--color-map-cluster-other)"
  const tokens = resolveMapTokens(document.documentElement)
  if (!communityId) return tokens.clusterOther
  const slot = communitySlot(communityId)
  return tokens.clusters[slot] ?? tokens.clusterOther
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <div role="status" aria-busy="true" aria-label="Loading entity page" className="space-y-6 p-6">
      <div className="space-y-2">
        <Skeleton className="h-7 w-48" />
        <Skeleton className="h-4 w-32" />
      </div>
      <Separator />
      <div className="space-y-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </div>
      <div className="space-y-2">
        <Skeleton className="h-4 w-24" />
        <div className="flex flex-wrap gap-2">
          <Skeleton className="h-7 w-20 rounded-full" />
          <Skeleton className="h-7 w-24 rounded-full" />
          <Skeleton className="h-7 w-16 rounded-full" />
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main detail view
// ---------------------------------------------------------------------------

export function EntityDetailView({ slug, onSelectRelated }: EntityDetailViewProps) {
  const { data, isLoading, isError, isNotFound, refetch } = useWikiEntity(slug)
  const navigation = useNavigation()

  // Strip ?entity= deep-link param so back-navigation doesn't re-select.
  useEffect(() => {
    if (typeof window === "undefined") return
    const params = new URLSearchParams(window.location.search)
    if (!params.has("entity")) return
    params.delete("entity")
    const next = params.toString()
    const url = `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`
    window.history.replaceState({}, "", url)
  }, [slug])

  if (isLoading) return <LoadingSkeleton />

  if (isError) {
    return (
      <div className="p-6">
        <PaneError
          title="Failed to load entity page"
          description="Try selecting this entity again."
          onRetry={() => void refetch()}
        />
      </div>
    )
  }

  if (isNotFound || data === null) {
    return (
      <div className="p-6">
        <EmptyState
          icon={BookOpen}
          title="Entity not found"
          description="This entity may have been removed or is not yet available."
        />
      </div>
    )
  }

  if (!data) return null

  const relativeUpdated = formatLastUpdated(data.last_updated_at)
  const refreshOverdue = isRefreshOverdue(data.next_refresh_due)
  const communityHue = resolveCommunityHue(data.community_id)

  const summaryProvenance: ProvenanceKind = "auto"
  const showLowConfidenceMarker = data.confidence_band === "low"

  const trustState: TrustState = data.trust_state ?? confidenceBandToTrust(data.confidence_band)

  return (
    <div className="cerid-stagger-fast h-full overflow-y-auto" style={{ ["--i" as string]: 0 }}>
      {/* Liquid-glass sticky header with identity capsule */}
      <div className="liquid-glass sticky top-0 z-10 space-y-1 px-6 pb-3 pt-6">
        {/* 3px community-hue left rail — width pinned to 3px per design spec (between 1px hairline and 4px w-1) */}
        <div
          className="absolute left-0 top-0 h-full rounded-l"
          style={{ width: "3px", backgroundColor: communityHue }} // drift-allowed: design-spec geometry + runtime token resolution
          aria-hidden="true"
        />

        {/* Identity row: name + trust badge */}
        <div className="flex flex-wrap items-center gap-2">
          <h1
            className="text-xl font-semibold text-foreground"
            style={{ viewTransitionName: "focal-entity" }}
          >
            {data.name}
          </h1>
          {refreshOverdue ? (
            <span
              className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary"
              aria-label="Updating from new evidence"
            >
              <RefreshCw className="h-3 w-3 animate-spin" aria-hidden="true" />
              Updating from new evidence
            </span>
          ) : (
            <TrustBandBadge
              trust={trustState}
              corroboratingCount={data.source_artifacts.length}
              contradictionCount={data.contradictions.length}
            />
          )}
          {showLowConfidenceMarker && <ProvenanceMarker kind="uncertain" />}
          {data.contradictions.length > 0 && <ProvenanceMarker kind="contradicted" />}
        </div>

        {/* Metadata row: community swatch + label + type chip + mention count */}
        <div className="flex flex-wrap items-center gap-2">
          {/* 10–14px community swatch */}
          <span
            className="inline-block h-3 w-3 shrink-0 rounded-sm"
            style={{ backgroundColor: communityHue }} // drift-allowed: runtime token resolution
            aria-hidden="true"
          />
          {/* Community label linked via "Open in Atlas" */}
          {data.community_id ? (
            <button
              type="button"
              onClick={() =>
                navigation.goTo("subjects", {
                  mode: "atlas",
                  entity: data.slug,
                })
              }
              className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
              aria-label={`Open ${data.community_label ?? data.community_id} in Atlas`}
            >
              {data.community_label ?? data.community_id}
            </button>
          ) : (
            <span className="text-xs text-muted-foreground">Unassigned</span>
          )}

          {/* Entity TYPE chip */}
          <Badge variant="outline" className="font-mono text-label-xs uppercase">
            {data.entity_type}
          </Badge>

          {/* Mention count */}
          {typeof data.mention_count === "number" && (
            <span className="text-xs text-muted-foreground">
              {data.mention_count.toLocaleString()} mentions
            </span>
          )}
        </div>

        {relativeUpdated && (
          <p className="text-xs text-muted-foreground">Updated {relativeUpdated}</p>
        )}
      </div>

      <div className="space-y-6 px-6 pb-6 pt-3">
        <Separator />

        {/* ----------------------------------------------------------------- */}
        {/* Summary                                                           */}
        {/* ----------------------------------------------------------------- */}
        {data.summary && (
          <section aria-labelledby="wiki-summary-heading">
            <div className="mb-2 flex items-center gap-2">
              <h2 id="wiki-summary-heading" className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Summary
              </h2>
              <ProvenanceMarker kind={summaryProvenance} />
            </div>
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {data.summary}
              </ReactMarkdown>
            </div>
          </section>
        )}

        {/* --------------------------------------------------------------- */}
        {/* Graph context — inline mini-graph (1-hop neighborhood)         */}
        {/* --------------------------------------------------------------- */}
        <MiniGraph entitySlug={data.slug} entityName={data.name} />

        {/* --------------------------------------------------------------- */}
        {/* Mention sparkline                                               */}
        {/* --------------------------------------------------------------- */}
        <MentionSparkline
          entitySlug={data.slug}
          entityName={data.name}
          onOpenTimeline={(slugArg) => {
            navigation.goTo("subjects", { mode: "timeline", entity: slugArg })
          }}
        />

        {/* --------------------------------------------------------------- */}
        {/* Provenance Sankey                                               */}
        {/* --------------------------------------------------------------- */}
        <ProvenanceSankey
          entitySlug={data.slug}
          entityName={data.name}
          communityId={data.community_id}
          onOpenAtlas={(slugArg) => {
            navigation.goTo("subjects", { mode: "atlas", entity: slugArg, lens: "provenance" })
          }}
        />

        {/* Contradiction lens jump-off */}
        {data.contradictions.length > 0 && (
          <ContradictionLink
            entitySlug={data.slug}
            contradictionCount={data.contradictions.length}
            onOpenAtlas={(slugArg) => {
              navigation.goTo("subjects", { mode: "atlas", entity: slugArg, lens: "contradiction" })
            }}
          />
        )}

        {/* ----------------------------------------------------------------- */}
        {/* Related entities                                                  */}
        {/* ----------------------------------------------------------------- */}
        {data.related_entities.length > 0 && (
          <section aria-labelledby="wiki-related-heading">
            <h2 id="wiki-related-heading" className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Related Entities
            </h2>
            <div className="flex flex-wrap gap-2">
              {data.related_entities.map((rel) => (
                <Button
                  key={rel.slug}
                  variant="outline"
                  size="sm"
                  className="h-auto rounded-full px-3 py-1 text-xs"
                  onClick={() => onSelectRelated(rel.slug)}
                  aria-label={`View entity: ${rel.name}`}
                >
                  {rel.name}
                </Button>
              ))}
            </div>
          </section>
        )}

        {/* ----------------------------------------------------------------- */}
        {/* Source artifacts                                                  */}
        {/* ----------------------------------------------------------------- */}
        {data.source_artifacts.length > 0 && (
          <section aria-labelledby="wiki-sources-heading">
            <h2 id="wiki-sources-heading" className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Source Artifacts
            </h2>
            <div className="space-y-2">
              {data.source_artifacts.map((src) => (
                <div
                  key={src.artifact_id}
                  className="flex items-start justify-between gap-3 rounded-md border border-border bg-muted/20 px-3 py-2"
                >
                  <div className="min-w-0 space-y-0.5">
                    <p className="truncate text-sm font-medium text-foreground">
                      {src.title ?? src.artifact_id}
                    </p>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {src.domain && <DomainBadge domain={src.domain} />}
                      {src.chunk_hash && (
                        <Badge variant="outline" className="font-mono text-label-xxs">
                          {src.chunk_hash.slice(0, 8)}
                        </Badge>
                      )}
                    </div>
                  </div>
                  {/* "View source" route not yet implemented (P0.4 from
                      2026-05-11-ui-audit.md). Render nothing until the route
                      lands rather than show a permanently-disabled affordance lie. */}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ----------------------------------------------------------------- */}
        {/* External references (Phase API.3) — hidden when empty           */}
        {/* ----------------------------------------------------------------- */}
        <ExternalReferencesSection refs={data.external_references ?? []} />

        {/* ----------------------------------------------------------------- */}
        {/* Contradictions — hidden when empty                               */}
        {/* ----------------------------------------------------------------- */}
        {data.contradictions.length > 0 && (
          <section aria-labelledby="wiki-contradictions-heading">
            <h2 id="wiki-contradictions-heading" className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Contradictions ({data.contradictions.length})
            </h2>
            <div className="space-y-3">
              {data.contradictions.map((finding) => (
                <ContradictionItem key={finding.finding_id} finding={finding} />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
