// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * WikiLanding — dashboard-style wiki home.
 *
 * Layout (top → bottom):
 *   1. Hero search (server-side q over /wiki/entities, see WikiHeroSearch)
 *   2. Stats strip — articles / domains / stubs / uncategorized
 *   3. Domain tiles — browse by domain
 *   4. Recently updated + Most active (two-column)
 *   5. Browse affordances — A–Z index, random article, ask-in-chat bridge
 *
 * Each block renders its own loading / error / empty / data states so one
 * failed source degrades a block, not the page. When every source has
 * settled empty the page collapses to a single EmptyState so a fresh
 * install reads as "not grown yet", not broken.
 */

import { useId, useMemo, useState } from "react"
import {
  Activity,
  AlertCircle,
  BookOpen,
  CircleDashed,
  FileWarning,
  History,
  Layers,
  List,
  MessageSquareText,
  ShieldAlert,
  Shuffle,
  type LucideIcon,
} from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { EmptyState } from "@/components/ui/empty-state"
import { DomainBadge } from "@/components/ui/domain-badge"
import { LastUpdated } from "@/components/ui/last-updated"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { fetchDomainCounts, type DomainCountsResponse } from "@/lib/api/domains"
import { fetchWikiIndex, type WikiIndexResponse } from "@/lib/api/wiki-browse"
import { domainIcon } from "@/lib/graph/domain-icons"
import { domainSlot } from "@/lib/graph/identity"
import { useWikiEntities } from "@/hooks/use-wiki-entities"
import { useNavigation } from "@/contexts/navigation-context"
import { WikiHeroSearch } from "./wiki-hero-search"
import type { EntitySummary } from "@/lib/types/wiki"

// Backend /wiki/entities and /wiki/index cap `limit` at 200. Entities feed
// the recently-updated + most-active lists; the index feeds stats + random.
const WIKI_ENTITY_LIMIT = 100
const WIKI_INDEX_LIMIT = 200

const RECENT_LIST_SIZE = 8
const ACTIVE_LIST_SIZE = 8

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function titleCase(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Minimal structural shape shared by useQuery results and useWikiEntities. */
interface QueryLike<T> {
  data: T | undefined
  isLoading: boolean
  isError: boolean
  refetch: () => Promise<unknown>
}

// ---------------------------------------------------------------------------
// BlockError — per-block error with retry
// ---------------------------------------------------------------------------

function BlockError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Alert variant="destructive">
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
// SectionHeading — shared uppercase block label
// ---------------------------------------------------------------------------

function SectionHeading({
  id,
  icon: Icon,
  children,
}: {
  id: string
  icon: LucideIcon
  children: React.ReactNode
}) {
  return (
    <h2
      id={id}
      className="flex items-center gap-1.5 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground"
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {children}
    </h2>
  )
}

// ---------------------------------------------------------------------------
// Stats strip
// ---------------------------------------------------------------------------

function StatCard({
  icon: Icon,
  label,
  value,
  tone = "neutral",
}: {
  icon: LucideIcon
  label: string
  value: string
  /** "amber" renders the quality call-out burst behind the icon. */
  tone?: "neutral" | "amber"
}) {
  return (
    <Card className="py-4">
      <CardContent className="flex items-center gap-3 px-4">
        <span
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${
            tone === "amber"
              ? "bg-amber-500/10 text-amber-600 dark:text-amber-500"
              : "bg-muted text-muted-foreground"
          }`}
        >
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
        <span className="min-w-0">
          <span className="block text-xl font-semibold leading-tight tabular-nums">{value}</span>
          <span className="block truncate text-label-xs font-medium uppercase tracking-wider text-muted-foreground">
            {label}
          </span>
        </span>
      </CardContent>
    </Card>
  )
}

function StatsStrip({
  domainsQ,
  indexQ,
}: {
  domainsQ: QueryLike<DomainCountsResponse>
  indexQ: QueryLike<WikiIndexResponse>
}) {
  if (domainsQ.isLoading || indexQ.isLoading) {
    return (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" role="status" aria-label="Loading stats">
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} className="h-20 w-full rounded-xl" />
        ))}
      </div>
    )
  }

  if (domainsQ.isError || indexQ.isError) {
    return (
      <BlockError
        message="Could not load wiki stats."
        onRetry={() => {
          void domainsQ.refetch()
          void indexQ.refetch()
        }}
      />
    )
  }

  const entries = indexQ.data?.entries ?? []
  const articleCount = entries.length
  const stubCount = entries.filter(
    (e) => (e.completeness ?? (e.has_summary ? "full" : "stub")) === "stub",
  ).length
  const domainCount = domainsQ.data?.domains.length ?? 0
  const uncategorized = domainsQ.data?.uncategorized_entities ?? 0

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard
        icon={BookOpen}
        label="Articles"
        value={articleCount >= WIKI_INDEX_LIMIT ? `${WIKI_INDEX_LIMIT}+` : String(articleCount)}
      />
      <StatCard icon={Layers} label="Domains" value={String(domainCount)} />
      <StatCard
        icon={FileWarning}
        label="Stub articles"
        value={String(stubCount)}
        tone={stubCount > 0 ? "amber" : "neutral"}
      />
      <StatCard
        icon={CircleDashed}
        label="Uncategorized"
        value={String(uncategorized)}
        tone={uncategorized > 0 ? "amber" : "neutral"}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Domain tiles
// ---------------------------------------------------------------------------

function DomainTilesBlock({
  domainsQ,
  onSelectDomain,
}: {
  domainsQ: QueryLike<DomainCountsResponse>
  onSelectDomain: (domain: string) => void
}) {
  if (domainsQ.isLoading) {
    return (
      <div
        className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"
        role="status"
        aria-label="Loading domains"
      >
        {[...Array(8)].map((_, i) => (
          <Skeleton key={i} className="h-24 w-full rounded-lg" />
        ))}
      </div>
    )
  }

  if (domainsQ.isError) {
    return (
      <BlockError
        message="Could not load domain categories."
        onRetry={() => void domainsQ.refetch()}
      />
    )
  }

  const domains = domainsQ.data?.domains ?? []
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
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {domains.map((dc) => {
        const Icon = domainIcon(dc.icon)
        const slot = domainSlot(dc.name)
        return (
          <button
            key={dc.name}
            type="button"
            onClick={() => onSelectDomain(dc.name)}
            aria-label={`Browse ${titleCase(dc.name)} — ${dc.entity_count} articles`}
            className="flex flex-col gap-3 rounded-lg border bg-card p-4 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span
              className="flex h-9 w-9 items-center justify-center rounded-md"
              style={{ // drift-allowed: runtime domain slot color — CSS var resolved at paint
                color: `var(--color-domain-${slot})`,
                backgroundColor: `color-mix(in oklab, var(--color-domain-${slot}) 12%, transparent)`,
              }}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium">{titleCase(dc.name)}</span>
              <span className="block text-label-xs tabular-nums text-muted-foreground">
                {dc.entity_count} {dc.entity_count === 1 ? "article" : "articles"}
              </span>
            </span>
          </button>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Recently updated + Most active lists
// ---------------------------------------------------------------------------

function ListSkeleton({ rows }: { rows: number }) {
  return (
    <div className="space-y-2 px-3 pb-3" role="status" aria-label="Loading">
      {[...Array(rows)].map((_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  )
}

function RecentlyUpdatedBlock({
  entitiesQ,
  onSelectEntity,
}: {
  entitiesQ: QueryLike<EntitySummary[]>
  onSelectEntity: (slug: string) => void
}) {
  const recent = useMemo(() => {
    const withDates = (entitiesQ.data ?? []).filter(
      (e): e is EntitySummary & { last_updated_at: string } => e.last_updated_at != null,
    )
    return [...withDates]
      .sort((a, b) => b.last_updated_at.localeCompare(a.last_updated_at))
      .slice(0, RECENT_LIST_SIZE)
  }, [entitiesQ.data])

  if (entitiesQ.isLoading) return <ListSkeleton rows={5} />
  if (entitiesQ.isError) {
    return (
      <div className="px-3 pb-3">
        <BlockError
          message="Could not load recent updates."
          onRetry={() => void entitiesQ.refetch()}
        />
      </div>
    )
  }
  if (recent.length === 0) {
    return (
      <EmptyState
        icon={History}
        title="No updates yet"
        description="Articles refresh as new sources are ingested."
      />
    )
  }

  return (
    <ul className="divide-y">
      {recent.map((entity) => (
        <li key={entity.slug}>
          <button
            type="button"
            onClick={() => onSelectEntity(entity.slug)}
            className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          >
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{entity.name}</span>
            {entity.primary_domain && <DomainBadge domain={entity.primary_domain} />}
            <span className="shrink-0 whitespace-nowrap">
              <LastUpdated timestamp={Date.parse(entity.last_updated_at)} />
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

function MostActiveBlock({
  entitiesQ,
  onSelectEntity,
}: {
  entitiesQ: QueryLike<EntitySummary[]>
  onSelectEntity: (slug: string) => void
}) {
  // /wiki/entities is already ordered by recent activity — take the head.
  const active = (entitiesQ.data ?? []).slice(0, ACTIVE_LIST_SIZE)

  if (entitiesQ.isLoading) return <ListSkeleton rows={5} />
  if (entitiesQ.isError) {
    return (
      <div className="px-3 pb-3">
        <BlockError
          message="Could not load activity data."
          onRetry={() => void entitiesQ.refetch()}
        />
      </div>
    )
  }
  if (active.length === 0) {
    return (
      <EmptyState
        icon={Activity}
        title="No articles yet"
        description="Add documents to start building articles."
      />
    )
  }

  return (
    <ol className="divide-y">
      {active.map((entity, idx) => (
        <li key={entity.slug}>
          <button
            type="button"
            onClick={() => onSelectEntity(entity.slug)}
            className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          >
            <span className="w-5 shrink-0 text-right text-label-xs tabular-nums text-muted-foreground">
              {idx + 1}.
            </span>
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{entity.name}</span>
            {entity.primary_domain && <DomainBadge domain={entity.primary_domain} />}
            <span className="shrink-0 text-label-xs tabular-nums text-muted-foreground">
              {entity.recent_activity_score}
            </span>
          </button>
        </li>
      ))}
    </ol>
  )
}

// ---------------------------------------------------------------------------
// Browse affordances — A–Z index, random article, ask-in-chat bridge
// ---------------------------------------------------------------------------

function BrowseBlock({
  indexQ,
  onSelectEntity,
  onOpenIndex,
}: {
  indexQ: QueryLike<WikiIndexResponse>
  onSelectEntity: (slug: string) => void
  onOpenIndex?: () => void
}) {
  const { composeChat } = useNavigation()

  const randomSlug = useMemo(() => {
    const entries = indexQ.data?.entries ?? []
    if (entries.length === 0) return null
    const withSummary = entries.filter((e) => e.has_summary)
    const pool = withSummary.length > 0 ? withSummary : entries
    return pool[Math.floor(Math.random() * pool.length)].slug
  }, [indexQ.data])

  return (
    <Card className="py-2">
      <CardContent className="flex flex-col gap-1 px-2 sm:flex-row sm:items-center sm:gap-2">
        {onOpenIndex && (
          <Button variant="ghost" size="sm" className="justify-start sm:flex-1" onClick={onOpenIndex}>
            <List className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            A–Z index — all articles
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="justify-start sm:flex-1"
          disabled={!randomSlug}
          onClick={() => randomSlug && onSelectEntity(randomSlug)}
        >
          <Shuffle className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          Random article
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="justify-start sm:flex-1"
          onClick={() => composeChat({ text: "What are the main topics in my wiki right now?" })}
        >
          <MessageSquareText className="h-4 w-4 text-brand" aria-hidden="true" />
          Ask about your wiki
        </Button>
      </CardContent>
    </Card>
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
  // WK2: default-OFF toggle. When off, queries send nothing and the server
  // hides the client-data domains; when on, they pass include_internal=true.
  const [includeInternal, setIncludeInternal] = useState(false)
  const toggleId = useId()

  const domainsQ = useQuery({
    queryKey: ["graph-domains", includeInternal],
    queryFn: () => fetchDomainCounts({ includeInternal }),
    staleTime: 10 * 60_000,
    retry: 1,
  })
  const indexQ = useQuery({
    queryKey: ["wiki-index-browse", includeInternal],
    queryFn: () => fetchWikiIndex({ order: "activity", limit: WIKI_INDEX_LIMIT, includeInternal }),
    staleTime: 5 * 60_000,
    retry: 1,
  })
  const entitiesQ = useWikiEntities({ limit: WIKI_ENTITY_LIMIT, includeInternal })

  // Fresh-install collapse: every source settled, nothing anywhere, no errors.
  const isEmptyWiki =
    !domainsQ.isLoading &&
    !entitiesQ.isLoading &&
    !domainsQ.isError &&
    !entitiesQ.isError &&
    (domainsQ.data?.domains.length ?? 0) === 0 &&
    (entitiesQ.data?.length ?? 0) === 0

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl space-y-8 px-6 py-8">
        {/* WK2: advanced "show internal / client data" toggle */}
        <div className="flex items-center justify-end gap-2">
          <ShieldAlert className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
          <Label htmlFor={toggleId} className="text-label-xs font-medium text-muted-foreground">
            Show internal / client data
          </Label>
          <Switch
            id={toggleId}
            checked={includeInternal}
            onCheckedChange={setIncludeInternal}
            aria-label="Show internal / client data"
          />
        </div>

        {isEmptyWiki ? (
          <EmptyState
            icon={BookOpen}
            title="No wiki articles yet"
            description="Ingest some sources to grow your wiki — articles appear as entities are extracted from your documents."
          />
        ) : (
          <>
            {/* Hero search */}
            <WikiHeroSearch onSelectEntity={onSelectEntity} includeInternal={includeInternal} />

            {/* Stats strip */}
            <section aria-label="Wiki stats">
              <StatsStrip domainsQ={domainsQ} indexQ={indexQ} />
            </section>

            {/* Domain tiles */}
            <section aria-labelledby="landing-domains-heading" className="space-y-3">
              <SectionHeading id="landing-domains-heading" icon={Layers}>
                Browse by domain
              </SectionHeading>
              <DomainTilesBlock domainsQ={domainsQ} onSelectDomain={onSelectDomain} />
            </section>

            {/* Two-column: recently updated + most active */}
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <section aria-labelledby="landing-recent-heading">
                <Card className="h-full gap-0 py-0">
                  <CardHeader className="px-3 pb-2 pt-3">
                    <SectionHeading id="landing-recent-heading" icon={History}>
                      Recently updated
                    </SectionHeading>
                  </CardHeader>
                  <CardContent className="px-0 pb-2">
                    <RecentlyUpdatedBlock entitiesQ={entitiesQ} onSelectEntity={onSelectEntity} />
                  </CardContent>
                </Card>
              </section>

              <section aria-labelledby="landing-active-heading">
                <Card className="h-full gap-0 py-0">
                  <CardHeader className="px-3 pb-2 pt-3">
                    <SectionHeading id="landing-active-heading" icon={Activity}>
                      Most active
                    </SectionHeading>
                  </CardHeader>
                  <CardContent className="px-0 pb-2">
                    <MostActiveBlock entitiesQ={entitiesQ} onSelectEntity={onSelectEntity} />
                  </CardContent>
                </Card>
              </section>
            </div>

            {/* Browse affordances */}
            <section aria-label="Browse actions">
              <BrowseBlock
                indexQ={indexQ}
                onSelectEntity={onSelectEntity}
                onOpenIndex={onOpenIndex}
              />
            </section>
          </>
        )}
      </div>
    </div>
  )
}
