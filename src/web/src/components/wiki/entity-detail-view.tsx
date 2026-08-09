// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useEffect, useMemo, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { BookOpen, Pencil, RefreshCw } from "lucide-react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
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
import { ProvenanceMarker } from "./provenance-marker"
import { MiniGraph } from "./mini-graph"
import { MentionSparkline } from "./mention-sparkline"
import { ProvenanceSankey } from "./provenance-sankey"
import { ContradictionLink } from "./contradiction-link"
import { ArticleInfobox } from "./article-infobox"
import { ArticleToc, type TocEntry } from "./article-toc"
import { PageHistory } from "./page-history"
import { WhatLinksHere } from "./what-links-here"
import { buildLinkifyComponents } from "./linkify"
import { refreshEntity, updateEntitySummary } from "@/lib/api/wiki"
import { useWikiEntity } from "@/hooks/use-wiki-entities"
import { useNavigation } from "@/contexts/navigation-context"
import { communitySlot } from "@/components/subjects/timeline/stratigraph/strata-layout"
import { resolveMapTokens } from "@/components/subjects/constellation/map/community-layer"
import type { ConfidenceBand } from "@/lib/types/wiki"

interface EntityDetailViewProps {
  slug: string
  onSelectRelated: (slug: string) => void
  /** Called when a domain category badge is clicked. Null = "all". */
  onSelectDomain?: (domain: string | null) => void
  /** Called when the community row / infobox button is clicked. */
  onSelectConcept?: (communityId: string) => void
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

function confidenceBandToTrust(band: ConfidenceBand): TrustState {
  switch (band) {
    case "high": return "verified"
    case "medium": return "partial"
    case "low": return "unverified"
    default: return "unknown"
  }
}

function resolveCommunityHue(communityId: string | null | undefined): string {
  // drift-allowed: runtime token resolution — community color resolved from CSS tokens
  if (typeof document === "undefined") return "var(--color-map-cluster-other)"
  const tokens = resolveMapTokens(document.documentElement)
  if (!communityId) return tokens.clusterOther
  const slot = communitySlot(communityId)
  return tokens.clusters[slot] ?? tokens.clusterOther
}

function titleCase(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

// ---------------------------------------------------------------------------
// Categories footer — unchanged from pre-rebuild (Cycle 1 work)
// ---------------------------------------------------------------------------

interface CategoriesFooterProps {
  primaryDomain: string
  domainMix: Record<string, number> | null
  primarySubcategory: string | null
  onNavigateToDomain: (domain: string | null) => void
}

function CategoriesFooter({
  primaryDomain,
  domainMix,
  primarySubcategory,
  onNavigateToDomain,
}: CategoriesFooterProps) {
  const orderedDomains: string[] = [primaryDomain]
  if (domainMix) {
    const rest = Object.keys(domainMix)
      .filter((k) => k !== primaryDomain)
      .sort((a, b) => (domainMix[b] ?? 0) - (domainMix[a] ?? 0))
    orderedDomains.push(...rest)
  }

  return (
    <section aria-labelledby="wiki-categories-footer-heading" className="border-t pt-3">
      <p
        id="wiki-categories-footer-heading"
        className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
      >
        Categories
      </p>
      <div className="flex flex-wrap items-center gap-1">
        {orderedDomains.map((domain, idx) => (
          <div key={domain} className="flex items-center gap-1">
            {idx > 0 && (
              <span className="text-muted-foreground/50" aria-hidden="true">·</span>
            )}
            <button
              type="button"
              onClick={() => onNavigateToDomain(domain)}
              className="transition-opacity hover:opacity-80"
              aria-label={`Filter wiki to ${titleCase(domain)}`}
            >
              <DomainBadge domain={domain} />
            </button>
            {idx === 0 && primarySubcategory && (
              <span className="text-label-xs text-muted-foreground">
                › {titleCase(primarySubcategory)}
              </span>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Loading skeleton — article-shaped
// ---------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <div role="status" aria-busy="true" aria-label="Loading article" className="@container h-full overflow-y-auto">
      {/* Slim header skeleton */}
      <div className="sticky top-0 z-10 flex items-center gap-3 border-b px-6 py-3">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      {/* Article body skeleton */}
      <div className="grid grid-cols-1 gap-6 p-6 @3xl:grid-cols-[200px_minmax(0,1fr)_280px]">
        <div className="hidden @3xl:block">
          <Skeleton className="h-4 w-20 mb-2" />
          <Skeleton className="h-3 w-full mb-1" />
          <Skeleton className="h-3 w-full mb-1" />
          <Skeleton className="h-3 w-3/4" />
        </div>
        <div className="space-y-4">
          <Skeleton className="h-3 w-24" />
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        </div>
        <div className="hidden @3xl:block">
          <Skeleton className="h-36 w-full rounded-xl mb-2" />
          <Skeleton className="h-3 w-full mb-1" />
          <Skeleton className="h-3 w-3/4" />
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main detail view
// ---------------------------------------------------------------------------

export function EntityDetailView({
  slug,
  onSelectRelated,
  onSelectDomain,
  onSelectConcept,
}: EntityDetailViewProps) {
  const { data, isLoading, isError, isNotFound, refetch } = useWikiEntity(slug)
  const navigation = useNavigation()
  const queryClient = useQueryClient()

  // WK4: inline summary editing state — must be above early returns (Rules of Hooks).
  const [editingMode, setEditingMode] = useState(false)
  const [draftSummary, setDraftSummary] = useState("")

  const refreshMutation = useMutation({
    mutationFn: () => refreshEntity(slug),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["wiki-entity", slug] })
      toast.success("Refresh queued")
    },
    onError: () => {
      toast.error("Refresh failed")
    },
  })

  const summaryMutation = useMutation({
    mutationFn: (summary: string) => updateEntitySummary(slug, summary),
    onSuccess: (updated) => {
      queryClient.setQueryData(["wiki-entity", slug], updated)
      void queryClient.invalidateQueries({ queryKey: ["wiki-entity", slug] })
      setEditingMode(false)
      toast.success("Summary updated")
    },
    onError: () => {
      toast.error("Failed to save summary")
    },
  })

  function handleEditSummary() {
    setDraftSummary(data?.summary ?? "")
    setEditingMode(true)
  }

  function handleCancelEdit() {
    setEditingMode(false)
    setDraftSummary("")
  }

  function handleSaveSummary() {
    summaryMutation.mutate(draftSummary)
  }

  // These useMemo calls must be above all early returns (Rules of Hooks).
  const linkifyEntities = useMemo(
    () =>
      data?.related_entities.map((rel) => ({
        slug: rel.slug,
        name: rel.name,
        entity_type: rel.entity_type,
        has_summary: rel.has_summary,
        one_liner: rel.one_liner,
      })) ?? [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data?.slug],
  )

  const linkifyComponents = useMemo(
    () => buildLinkifyComponents({ entities: linkifyEntities, onSelect: onSelectRelated }),
    [linkifyEntities, onSelectRelated],
  )

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
          title="Failed to load article"
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
  const refreshStatus = data.refresh_status ?? "idle"
  const communityHue = resolveCommunityHue(data.community_id)
  const trustState: TrustState = confidenceBandToTrust(data.confidence_band)

  // Split summary into lead paragraph and body paragraphs.
  const summaryParts = data.summary?.split(/\n\n+/) ?? []
  const leadParagraph = summaryParts[0] ?? null
  const bodyParagraphs = summaryParts.slice(1).join("\n\n") || null

  // Build TOC entries — only for sections that have data.
  const tocEntries: TocEntry[] = []
  if (bodyParagraphs) tocEntries.push({ id: "wiki-section-body", label: "Summary" })
  tocEntries.push({ id: "wiki-section-activity", label: "Activity & graph" })
  if (data.related_entities.length > 0) {
    tocEntries.push({ id: "wiki-section-related", label: "Mentioned together" })
  }
  if (data.source_artifacts.length > 0) {
    tocEntries.push({ id: "references", label: "References" })
  }
  if (data.external_references.length > 0) {
    tocEntries.push({ id: "wiki-section-external", label: "External links" })
  }
  if (data.contradictions.length > 0) {
    tocEntries.push({ id: "wiki-section-contradictions", label: "Contradictions" })
  }
  tocEntries.push({ id: "wiki-section-history", label: "Page history" })
  tocEntries.push({ id: "wiki-section-backlinks", label: "What links here" })

  function handleNavigateToDomain(domain: string) {
    if (onSelectDomain) {
      onSelectDomain(domain)
    } else {
      if (typeof window !== "undefined") {
        const params = new URLSearchParams(window.location.search)
        params.set("domain", domain)
        window.history.replaceState(
          {},
          "",
          `${window.location.pathname}?${params.toString()}${window.location.hash}`,
        )
      }
      navigation.goTo("subjects", { mode: "wiki" })
    }
  }

  function handleNavigateToConcept(communityId: string) {
    if (onSelectConcept) {
      onSelectConcept(communityId)
    } else {
      navigation.goTo("subjects", { mode: "wiki", concept: communityId })
    }
  }

  return (
    <div className="cerid-stagger-fast @container h-full overflow-y-auto" style={{ ["--i" as string]: 0 }}> {/* drift-allowed: animation stagger index */}

      {/* ----------------------------------------------------------------- */}
      {/* Slim sticky header — title + TrustBandBadge + refresh pill        */}
      {/* The ONLY glass in the pane (amendment #6).                        */}
      {/* ----------------------------------------------------------------- */}
      <div className="liquid-glass sticky top-0 z-10 px-6 pb-2 pt-4">
        {/* Community-hue left rail */}
        <div
          className="absolute left-0 top-0 h-full rounded-l"
          style={{ width: "3px", backgroundColor: communityHue }} // drift-allowed: design-spec geometry + runtime token resolution
          aria-hidden="true"
        />

        <div className="flex flex-wrap items-center gap-2">
          <h1
            className="text-lg font-semibold text-foreground"
            style={{ viewTransitionName: "focal-entity" }} // drift-allowed: view-transition runtime binding
          >
            {data.name}
          </h1>
          <TrustBandBadge
            trust={trustState}
            corroboratingCount={data.source_artifacts.length}
            contradictionCount={data.contradictions.length}
          />
          {refreshStatus === "running" && (
            <span
              className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary"
              aria-label="Updating from new evidence"
            >
              <RefreshCw className="h-3 w-3 animate-spin" aria-hidden="true" />
              Updating…
            </span>
          )}
          {refreshStatus === "due" && (
            <span className="text-xs text-muted-foreground" aria-label="Refresh scheduled">
              Refresh due
            </span>
          )}
          {/* WK4: manual refresh trigger */}
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-7 gap-1 px-2 text-xs"
            onClick={() => refreshMutation.mutate()}
            disabled={refreshMutation.isPending || refreshStatus === "running"}
            aria-label={refreshMutation.isPending ? "Refresh queued" : "Refresh this entity"}
          >
            <RefreshCw
              className={`h-3 w-3 ${refreshMutation.isPending ? "animate-spin" : ""}`}
              aria-hidden="true"
            />
            {refreshMutation.isPending ? "Queued" : "Refresh"}
          </Button>
        </div>
      </div>

      {/* ----------------------------------------------------------------- */}
      {/* Article body — Vector-2022 three-column grid at lg+               */}
      {/* lg+: [200px TOC | prose | 280px infobox]                         */}
      {/* <lg: single column (infobox after lead)                          */}
      {/* Cramped-measure guard: TOC column collapses first at marginal lg  */}
      {/* ----------------------------------------------------------------- */}
      <div className="grid grid-cols-1 gap-x-6 gap-y-0 px-6 pb-8 pt-4 @3xl:grid-cols-[200px_minmax(0,1fr)_280px]">

        {/* ------ Left: sticky TOC (wide container only) ------ */}
        <aside aria-label="Article contents" className="hidden pt-2 @3xl:block">
          <div className="sticky top-16">
            <ArticleToc entries={tocEntries} />
          </div>
        </aside>

        {/* ------ Center: article prose ------ */}
        <article className="min-w-0">

          {/* Inline TOC disclosure (narrow container) */}
          <div className="mb-4 @3xl:hidden">
            <ArticleToc entries={tocEntries} ariaLabel="Contents (mobile)" />
          </div>

          {/* Lead paragraph — heading-less, max-w-prose */}
          {leadParagraph ? (
            <section aria-labelledby="wiki-article-title">
              {/* In-flow byline — "Generated from N sources · updated X · band" */}
              {data.source_artifacts.length > 0 && (
                <p className="mb-2 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
                  <ProvenanceMarker kind="auto" />
                  <span>
                    Generated from {data.source_artifacts.length}{" "}
                    {data.source_artifacts.length === 1 ? "source" : "sources"}
                  </span>
                  {relativeUpdated && (
                    <>
                      <span aria-hidden="true">·</span>
                      <span>updated {relativeUpdated}</span>
                    </>
                  )}
                  <span aria-hidden="true">·</span>
                  <span className="capitalize">{data.confidence_band}</span>
                </p>
              )}
              {/* WK4: inline editable summary */}
              {editingMode ? (
                <div className="space-y-2">
                  <label htmlFor="wiki-summary-textarea" className="sr-only">
                    Edit summary
                  </label>
                  <textarea
                    id="wiki-summary-textarea"
                    aria-label="Edit summary"
                    className="w-full rounded-md border border-border bg-background p-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    rows={6}
                    value={draftSummary}
                    onChange={(e) => setDraftSummary(e.target.value)}
                  />
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      onClick={handleSaveSummary}
                      disabled={summaryMutation.isPending}
                    >
                      Save
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={handleCancelEdit}
                      disabled={summaryMutation.isPending}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="group/summary relative">
                  <div className="prose prose-sm dark:prose-invert max-w-prose">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={linkifyComponents}
                    >
                      {leadParagraph}
                    </ReactMarkdown>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-1 h-6 gap-1 px-2 text-xs text-muted-foreground opacity-0 transition-opacity group-hover/summary:opacity-100 focus-visible:opacity-100"
                    onClick={handleEditSummary}
                    aria-label="Edit summary"
                  >
                    <Pencil className="h-3 w-3" aria-hidden="true" />
                    Edit
                  </Button>
                </div>
              )}
            </section>
          ) : (
            /* Stub article state — entity exists, summary not yet generated */
            <section
              aria-labelledby="wiki-stub-heading"
              className="rounded-lg border border-border/50 bg-muted/20 px-4 py-4"
            >
              <p id="wiki-stub-heading" className="font-medium text-foreground">
                No summary yet
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                This entity has{" "}
                {typeof data.mention_count === "number"
                  ? `${data.mention_count.toLocaleString()} mentions`
                  : "mentions"}{" "}
                across {data.source_artifacts.length}{" "}
                {data.source_artifacts.length === 1 ? "source" : "sources"}. The nightly
                refresh writes summaries in activity order.
                {refreshStatus === "due" && data.next_refresh_due && (
                  <span> Next sweep {formatLastUpdated(data.next_refresh_due)}.</span>
                )}
              </p>
            </section>
          )}

          {/* Narrow-container: infobox renders after lead when no side rail */}
          {data.primary_domain && (
            <div className="my-4 @3xl:hidden">
              <ArticleInfobox
                page={data}
                onNavigateToDomain={handleNavigateToDomain}
                onNavigateToConcept={handleNavigateToConcept}
                onOpenAtlas={() => navigation.goTo("subjects", { mode: "atlas", entity: data.slug })}
              />
            </div>
          )}

          {/* Body paragraphs (remainder of summary after lead) */}
          {bodyParagraphs && (
            <div
              id="wiki-section-body"
              className="prose prose-sm dark:prose-invert mt-4 max-w-prose"
            >
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={linkifyComponents}
              >
                {bodyParagraphs}
              </ReactMarkdown>
            </div>
          )}

          <Separator className="my-6" />

          {/* --------------------------------------------------------------- */}
          {/* Activity & graph (anchored section)                              */}
          {/* --------------------------------------------------------------- */}
          <section id="wiki-section-activity" aria-labelledby="wiki-activity-heading">
            <h2
              id="wiki-activity-heading"
              className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
            >
              Activity &amp; graph
            </h2>
            <MiniGraph entitySlug={data.slug} entityName={data.name} />
            <MentionSparkline
              entitySlug={data.slug}
              entityName={data.name}
              onOpenTimeline={(slugArg) => {
                navigation.goTo("subjects", { mode: "timeline", entity: slugArg })
              }}
            />
            <ProvenanceSankey
              entitySlug={data.slug}
              entityName={data.name}
              communityId={data.community_id}
              sourceArtifacts={data.source_artifacts}
              onOpenAtlas={(slugArg) => {
                navigation.goTo("subjects", { mode: "atlas", entity: slugArg, lens: "provenance" })
              }}
            />
            {data.contradictions.length > 0 && (
              <ContradictionLink
                entitySlug={data.slug}
                contradictionCount={data.contradictions.length}
                onOpenAtlas={(slugArg) => {
                  navigation.goTo("subjects", {
                    mode: "atlas",
                    entity: slugArg,
                    lens: "contradiction",
                  })
                }}
              />
            )}
          </section>

          <Separator className="my-6" />

          {/* --------------------------------------------------------------- */}
          {/* Mentioned together (Related entities / See also)                */}
          {/* --------------------------------------------------------------- */}
          {data.related_entities.length > 0 && (
            <section id="wiki-section-related" aria-labelledby="wiki-related-heading">
              <h2
                id="wiki-related-heading"
                className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
              >
                Mentioned together
              </h2>
              <p className="mb-3 text-label-xs text-muted-foreground/70">
                Co-mentioned in the same sources — ordered by co-mention strength.
              </p>
              <div className="flex flex-wrap gap-2">
                {data.related_entities
                  .slice()
                  .sort((a, b) => b.co_mention_strength - a.co_mention_strength)
                  .map((rel) => {
                    const isStub = !rel.has_summary
                    return (
                      <Button
                        key={rel.slug}
                        variant="outline"
                        size="sm"
                        className={
                          isStub
                            ? "h-auto rounded-full px-3 py-1 text-xs text-muted-foreground border-dashed"
                            : "h-auto rounded-full px-3 py-1 text-xs"
                        }
                        onClick={() => onSelectRelated(rel.slug)}
                        aria-label={
                          isStub
                            ? `View ${rel.name} — summary pending`
                            : `View entity: ${rel.name}`
                        }
                        title={rel.entity_type ? titleCase(rel.entity_type) : undefined}
                      >
                        {rel.name}
                        {rel.entity_type && rel.entity_type !== "OTHER" && (
                          <span className="ml-1 font-mono text-label-xxs opacity-60 uppercase">
                            {rel.entity_type.slice(0, 3)}
                          </span>
                        )}
                      </Button>
                    )
                  })}
              </div>
              <Separator className="mt-6 mb-0" />
            </section>
          )}

          {/* --------------------------------------------------------------- */}
          {/* References (Source artifacts — anchored)                        */}
          {/* --------------------------------------------------------------- */}
          {data.source_artifacts.length > 0 && (
            <section id="references" aria-labelledby="wiki-references-heading" className="mt-6">
              <h2
                id="wiki-references-heading"
                className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
              >
                References
              </h2>
              <ol className="space-y-2" aria-label="Source references">
                {data.source_artifacts.map((src, idx) => {
                  const displayTitle =
                    src.title ??
                    src.filename ??
                    (src.source_type ? `Untitled (${src.source_type})` : null)
                  return (
                    <li key={src.artifact_id} className="flex items-start gap-3">
                      <span
                        className="mt-0.5 shrink-0 font-mono text-label-xxs text-muted-foreground/60"
                        aria-hidden="true"
                      >
                        [{idx + 1}]
                      </span>
                      <button
                        type="button"
                        title={src.artifact_id}
                        aria-label={`View source: ${displayTitle ?? src.artifact_id}`}
                        onClick={() => navigation.goTo("sources")}
                        className="flex min-w-0 flex-1 items-start gap-2 rounded-md border border-border bg-muted/20 px-3 py-2 text-left transition-colors hover:border-border/60 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <div className="min-w-0 flex-1 space-y-0.5">
                          <p className="truncate text-sm font-medium text-foreground">
                            {displayTitle ?? (
                              <span className="font-mono text-xs text-muted-foreground">
                                {src.artifact_id.slice(0, 8)}…
                              </span>
                            )}
                          </p>
                          <div className="flex flex-wrap items-center gap-2 text-label-xs text-muted-foreground">
                            {src.domain && <DomainBadge domain={src.domain} />}
                            {src.confidence != null && (
                              <span>
                                {Math.round(src.confidence * 100)}% confidence
                              </span>
                            )}
                            {src.updated_at && (
                              <span>{formatLastUpdated(src.updated_at)}</span>
                            )}
                          </div>
                        </div>
                      </button>
                    </li>
                  )
                })}
              </ol>
              <Separator className="mt-6 mb-0" />
            </section>
          )}

          {/* --------------------------------------------------------------- */}
          {/* External links                                                   */}
          {/* --------------------------------------------------------------- */}
          {data.external_references.length > 0 && (
            <section id="wiki-section-external" aria-labelledby="wiki-external-heading" className="mt-6">
              <h2
                id="wiki-external-heading"
                className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
              >
                External links
              </h2>
              <ExternalReferencesSection refs={data.external_references} />
              <Separator className="mt-6 mb-0" />
            </section>
          )}

          {/* --------------------------------------------------------------- */}
          {/* Contradictions                                                   */}
          {/* --------------------------------------------------------------- */}
          {data.contradictions.length > 0 && (
            <section id="wiki-section-contradictions" aria-labelledby="wiki-contradictions-heading" className="mt-6">
              <h2
                id="wiki-contradictions-heading"
                className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
              >
                Contradictions ({data.contradictions.length})
              </h2>
              <div className="space-y-3">
                {data.contradictions.map((finding) => (
                  <ContradictionItem key={finding.finding_id} finding={finding} />
                ))}
              </div>
              <Separator className="mt-6 mb-0" />
            </section>
          )}

          {/* --------------------------------------------------------------- */}
          {/* Page history                                                     */}
          {/* --------------------------------------------------------------- */}
          <section id="wiki-section-history" aria-labelledby="wiki-history-heading" className="mt-6">
            <h2
              id="wiki-history-heading"
              className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
            >
              Page history
            </h2>
            <PageHistory entitySlug={data.slug} />
          </section>

          {/* --------------------------------------------------------------- */}
          {/* What links here (WK1 backlinks)                                 */}
          {/* --------------------------------------------------------------- */}
          <div id="wiki-section-backlinks" className="mt-6">
            <WhatLinksHere
              entitySlug={data.slug}
              onSelectEntity={onSelectRelated}
            />
          </div>

          {/* --------------------------------------------------------------- */}
          {/* Categories footer                                                */}
          {/* --------------------------------------------------------------- */}
          {data.primary_domain && (
            <div className="mt-6">
              <CategoriesFooter
                primaryDomain={data.primary_domain}
                domainMix={data.domain_mix ?? null}
                primarySubcategory={data.primary_subcategory ?? null}
                onNavigateToDomain={(domain) => handleNavigateToDomain(domain ?? data.primary_domain!)}
              />
            </div>
          )}
        </article>

        {/* ------ Right: infobox (lg+ only — mobile version rendered above) ------ */}
        <aside aria-label="Article infobox" className="hidden pt-2 @3xl:block">
          <div className="sticky top-16">
            {data.primary_domain ? (
              <ArticleInfobox
                page={data}
                onNavigateToDomain={handleNavigateToDomain}
                onNavigateToConcept={handleNavigateToConcept}
                onOpenAtlas={() => navigation.goTo("subjects", { mode: "atlas", entity: data.slug })}
              />
            ) : (
              /* Minimal infobox when domain not yet derived */
              <div className="rounded-xl border bg-card p-3 text-xs text-muted-foreground">
                <p className="font-semibold text-foreground">{data.name}</p>
                <p className="mt-1">
                  <Badge variant="outline" className="font-mono text-label-xxs uppercase">
                    {data.entity_type}
                  </Badge>
                </p>
                <div className="mt-2">
                  <TrustBandBadge
                    trust={confidenceBandToTrust(data.confidence_band)}
                    corroboratingCount={data.source_artifacts.length}
                    contradictionCount={data.contradictions.length}
                  />
                </div>
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
