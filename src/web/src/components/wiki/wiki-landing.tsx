// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useMemo } from "react"
import {
  BookOpen,
  RefreshCw,
  Shuffle,
  Activity,
  FileWarning,
  AlertCircle,
  List,
} from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { EmptyState } from "@/components/ui/empty-state"
import { fetchDomainCounts } from "@/lib/api/domains"
import { fetchWikiIndex, fetchWikiLog } from "@/lib/api/wiki-browse"
import { domainIcon } from "@/lib/graph/domain-icons"
import { domainSlot } from "@/lib/graph/identity"
import { useWikiEntities } from "@/hooks/use-wiki-entities"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function titleCase(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

function relativeTime(iso: string): string {
  try {
    const seconds = Math.floor((Date.now() - Date.parse(iso)) / 1000)
    if (seconds < 60) return "just now"
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
    return `${Math.floor(seconds / 86400)}d ago`
  } catch {
    return ""
  }
}

function actionLabel(action: string): string {
  if (action === "refresh") return "refreshed"
  if (action === "enrich") return "enriched"
  if (action === "contradict") return "contradiction recorded"
  return action
}

// ---------------------------------------------------------------------------
// BlockSkeleton — loading state for a single landing block
// ---------------------------------------------------------------------------

function BlockSkeleton() {
  return (
    <div className="space-y-2 p-4" role="status" aria-label="Loading">
      <Skeleton className="h-4 w-1/3" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-4/5" />
      <Skeleton className="h-3 w-3/5" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// BlockError — per-block error with retry
// ---------------------------------------------------------------------------

function BlockError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Alert variant="destructive" className="m-3">
      <AlertCircle className="h-4 w-4" aria-hidden="true" />
      <AlertTitle>Failed to load</AlertTitle>
      <AlertDescription className="flex items-center justify-between gap-2">
        <span>{message}</span>
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 text-xs underline underline-offset-2 hover:no-underline"
        >
          Retry
        </button>
      </AlertDescription>
    </Alert>
  )
}

// ---------------------------------------------------------------------------
// DomainCardsBlock
// ---------------------------------------------------------------------------

interface DomainCardsBlockProps {
  onSelectDomain: (domain: string) => void
}

function DomainCardsBlock({ onSelectDomain }: DomainCardsBlockProps) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["graph-domains"],
    queryFn: fetchDomainCounts,
    staleTime: 10 * 60_000,
    retry: 1,
  })

  if (isLoading) return <BlockSkeleton />
  if (isError) {
    return (
      <BlockError
        message="Could not load domain categories."
        onRetry={() => void refetch()}
      />
    )
  }
  const domains = data?.domains ?? []
  if (domains.length === 0) {
    return (
      <EmptyState
        icon={BookOpen}
        title="No domains yet"
        description="Domain data is derived nightly after ingest."
      />
    )
  }

  return (
    <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3">
      {domains.map((dc) => {
        const Icon = domainIcon(dc.icon)
        const slot = domainSlot(dc.name)
        return (
          <button
            key={dc.name}
            type="button"
            onClick={() => onSelectDomain(dc.name)}
            aria-label={`Browse ${titleCase(dc.name)} — ${dc.entity_count} entities`}
            className="flex items-center gap-2 rounded-md border p-3 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Icon
              className="h-4 w-4 shrink-0"
              aria-hidden="true"
              // drift-allowed: runtime domain slot color
              style={{ color: `var(--color-domain-${slot})` }}
            />
            <span className="min-w-0">
              <span className="block text-xs font-medium truncate">{titleCase(dc.name)}</span>
              <span className="block text-label-xs text-muted-foreground tabular-nums">
                {dc.entity_count} entities
              </span>
            </span>
          </button>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// RecentChangesBlock
// ---------------------------------------------------------------------------

interface RecentChangesBlockProps {
  onSelectEntity: (slug: string) => void
}

function RecentChangesBlock({ onSelectEntity }: RecentChangesBlockProps) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["wiki-log-global"],
    queryFn: () => fetchWikiLog({ limit: 10 }),
    staleTime: 2 * 60_000,
    retry: 1,
  })

  if (isLoading) return <BlockSkeleton />
  if (isError) {
    return (
      <BlockError
        message="Could not load recent changes."
        onRetry={() => void refetch()}
      />
    )
  }
  const entries = data ?? []
  if (entries.length === 0) {
    return (
      <EmptyState
        icon={RefreshCw}
        title="No recorded changes yet"
        description="History begins with the next refresh."
      />
    )
  }

  return (
    <ul className="divide-y text-sm">
      {entries.map((entry) => (
        <li key={entry.log_id} className="flex items-start justify-between gap-3 px-3 py-2">
          <span className="min-w-0 flex-1">
            <button
              type="button"
              className="font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              onClick={() => onSelectEntity(entry.entity_slug)}
            >
              {entry.entity_slug}
            </button>
            <span className="text-muted-foreground"> — {actionLabel(entry.action)}</span>
          </span>
          <span className="shrink-0 text-label-xs text-muted-foreground whitespace-nowrap">
            {relativeTime(entry.ts)}
          </span>
        </li>
      ))}
    </ul>
  )
}

// ---------------------------------------------------------------------------
// MostActiveBlock
// ---------------------------------------------------------------------------

interface MostActiveBlockProps {
  onSelectEntity: (slug: string) => void
}

function MostActiveBlock({ onSelectEntity }: MostActiveBlockProps) {
  const { data, isLoading, isError, refetch } = useWikiEntities({ limit: 10 })

  if (isLoading) return <BlockSkeleton />
  if (isError) {
    return (
      <BlockError
        message="Could not load activity data."
        onRetry={() => void refetch()}
      />
    )
  }
  const entities = data ?? []
  if (entities.length === 0) {
    return (
      <EmptyState
        icon={Activity}
        title="No entities yet"
        description="Add documents to start building entity pages."
      />
    )
  }

  return (
    <ol className="divide-y text-sm">
      {entities.map((entity, idx) => (
        <li key={entity.slug} className="flex items-center gap-3 px-3 py-2">
          <span className="w-5 shrink-0 text-right text-label-xs text-muted-foreground tabular-nums">
            {idx + 1}.
          </span>
          <span className="min-w-0 flex-1">
            <button
              type="button"
              className="font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              onClick={() => onSelectEntity(entity.slug)}
            >
              {entity.name}
            </button>
            {entity.primary_domain && (
              <span className="ml-1.5 text-label-xs text-muted-foreground">
                {titleCase(entity.primary_domain)}
              </span>
            )}
          </span>
          <span className="shrink-0 text-label-xs text-muted-foreground tabular-nums">
            {entity.recent_activity_score}
          </span>
        </li>
      ))}
    </ol>
  )
}

// ---------------------------------------------------------------------------
// AwaitingSummariesBlock + RandomArticleButton
// ---------------------------------------------------------------------------

interface IndexBlocksProps {
  onSelectEntity: (slug: string) => void
  onOpenIndex?: () => void
}

function IndexBlocks({ onSelectEntity, onOpenIndex }: IndexBlocksProps) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["wiki-index-browse"],
    queryFn: () => fetchWikiIndex({ order: "activity", limit: 200 }),
    staleTime: 5 * 60_000,
    retry: 1,
  })

  const awaitingCount = useMemo(() => {
    if (!data) return null
    return data.entries.filter((e) => !e.has_summary).length
  }, [data])

  const randomSlug = useMemo(() => {
    if (!data || data.entries.length === 0) return null
    const withSummary = data.entries.filter((e) => e.has_summary)
    const pool = withSummary.length > 0 ? withSummary : data.entries
    return pool[Math.floor(Math.random() * pool.length)].slug
  }, [data])

  if (isLoading) {
    return (
      <>
        <Card>
          <CardHeader className="pb-2 pt-3 px-3">
            <p className="flex items-center gap-1.5 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <FileWarning className="h-3.5 w-3.5" aria-hidden="true" />
              Pages awaiting summaries
            </p>
          </CardHeader>
          <CardContent className="pb-3 px-3">
            <BlockSkeleton />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-3 px-3">
            <BlockSkeleton />
          </CardContent>
        </Card>
      </>
    )
  }

  if (isError) {
    return (
      <BlockError
        message="Could not load page index."
        onRetry={() => void refetch()}
      />
    )
  }

  return (
    <>
      {/* Pages awaiting summaries */}
      {awaitingCount !== null && (
        <Card>
          <CardHeader className="pb-1 pt-3 px-3">
            <p className="flex items-center gap-1.5 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <FileWarning className="h-3.5 w-3.5" aria-hidden="true" />
              Pages awaiting summaries
            </p>
          </CardHeader>
          <CardContent className="pb-3 px-3">
            {awaitingCount === 0 ? (
              <p className="text-sm text-muted-foreground">All entities have summaries.</p>
            ) : (
              <p className="text-sm">
                <span className="font-semibold tabular-nums">{awaitingCount}</span>
                <span className="text-muted-foreground">
                  {" "}
                  {awaitingCount === 1 ? "entity" : "entities"} pending the nightly refresh.
                </span>
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Random article + A-Z index row */}
      {(randomSlug || onOpenIndex) && (
        <Card>
          <CardContent className="flex items-center gap-4 py-3 px-3">
            {randomSlug && (
              <button
                type="button"
                onClick={() => onSelectEntity(randomSlug)}
                className="flex flex-1 items-center gap-2 text-sm font-medium hover:text-foreground/80 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              >
                <Shuffle className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                Random article
              </button>
            )}
            {onOpenIndex && (
              <button
                type="button"
                onClick={onOpenIndex}
                className="flex flex-1 items-center gap-2 text-sm font-medium hover:text-foreground/80 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              >
                <List className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                A–Z Index
              </button>
            )}
          </CardContent>
        </Card>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// WikiLanding — main export
// ---------------------------------------------------------------------------

export interface WikiLandingProps {
  onSelectEntity: (slug: string) => void
  onSelectDomain: (domain: string) => void
  onOpenIndex?: () => void
}

export function WikiLanding({ onSelectEntity, onSelectDomain, onOpenIndex }: WikiLandingProps) {
  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl space-y-6 px-4 py-6">

        {/* Domain cards */}
        <section aria-labelledby="landing-domains-heading">
          <Card>
            <CardHeader className="pb-2 pt-3 px-3">
              <h2
                id="landing-domains-heading"
                className="flex items-center gap-1.5 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground"
              >
                <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
                Browse by domain
              </h2>
            </CardHeader>
            <CardContent className="pb-2 px-0">
              <DomainCardsBlock onSelectDomain={onSelectDomain} />
            </CardContent>
          </Card>
        </section>

        {/* Two-column: recent changes + most active */}
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
          <section aria-labelledby="landing-recent-heading">
            <Card className="h-full">
              <CardHeader className="pb-2 pt-3 px-3">
                <h2
                  id="landing-recent-heading"
                  className="flex items-center gap-1.5 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground"
                >
                  <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                  Recent changes
                </h2>
              </CardHeader>
              <CardContent className="px-0 pb-2">
                <RecentChangesBlock onSelectEntity={onSelectEntity} />
              </CardContent>
            </Card>
          </section>

          <section aria-labelledby="landing-active-heading">
            <Card className="h-full">
              <CardHeader className="pb-2 pt-3 px-3">
                <h2
                  id="landing-active-heading"
                  className="flex items-center gap-1.5 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground"
                >
                  <Activity className="h-3.5 w-3.5" aria-hidden="true" />
                  Most active this month
                </h2>
              </CardHeader>
              <CardContent className="px-0 pb-2">
                <MostActiveBlock onSelectEntity={onSelectEntity} />
              </CardContent>
            </Card>
          </section>
        </div>

        {/* Awaiting summaries + random article + index link */}
        <section aria-label="Index actions" className="space-y-4">
          <IndexBlocks onSelectEntity={onSelectEntity} onOpenIndex={onOpenIndex} />
        </section>
      </div>
    </div>
  )
}
