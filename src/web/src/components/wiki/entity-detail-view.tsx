// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { AlertCircle, RefreshCw } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/ui/empty-state"
import { DomainBadge } from "@/components/ui/domain-badge"
import { ConfidenceBandBadge } from "./confidence-band-badge"
import { ContradictionItem } from "./contradiction-item"
import { ExternalReferencesSection } from "./external-references-section"
import { useWikiEntity } from "@/hooks/use-wiki-entities"
import { BookOpen } from "lucide-react"

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
  const { data, isLoading, isError, isNotFound } = useWikiEntity(slug)

  if (isLoading) return <LoadingSkeleton />

  if (isError) {
    return (
      <div className="p-6">
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertDescription>
            Failed to load entity page. Please try again.
          </AlertDescription>
        </Alert>
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

  return (
    <div className="h-full overflow-y-auto">
      <div className="space-y-6 p-6">
        {/* ----------------------------------------------------------------- */}
        {/* Header                                                            */}
        {/* ----------------------------------------------------------------- */}
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold text-foreground">{data.name}</h1>
            <ConfidenceBandBadge band={data.confidence_band} />
            {refreshOverdue && (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-brand/10 px-2 py-0.5 text-xs font-medium text-brand"
                aria-label="Updating from new evidence"
              >
                <RefreshCw className="h-3 w-3 animate-spin" aria-hidden="true" />
                Updating from new evidence
              </span>
            )}
          </div>
          {relativeUpdated && (
            <p className="text-xs text-muted-foreground">Updated {relativeUpdated}</p>
          )}
        </div>

        <Separator />

        {/* ----------------------------------------------------------------- */}
        {/* Summary                                                           */}
        {/* ----------------------------------------------------------------- */}
        {data.summary && (
          <section aria-labelledby="wiki-summary-heading">
            <h2 id="wiki-summary-heading" className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Summary
            </h2>
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {data.summary}
              </ReactMarkdown>
            </div>
          </section>
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
                        <Badge variant="outline" className="font-mono text-[9px]">
                          {src.chunk_hash.slice(0, 8)}
                        </Badge>
                      )}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="shrink-0 text-xs text-muted-foreground hover:text-foreground"
                    aria-label={`View source: ${src.title ?? src.artifact_id}`}
                    disabled
                  >
                    View source
                  </Button>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ----------------------------------------------------------------- */}
        {/* External references (Phase API.3) — hidden when empty           */}
        {/* Always visually distinct from internal Source artifacts.         */}
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
