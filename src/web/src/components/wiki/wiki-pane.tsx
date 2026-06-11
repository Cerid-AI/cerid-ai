// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useMemo, useState } from "react"
import { BookOpen, Info } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { PaneError } from "@/components/ui/pane-error"
import { EmptyState } from "@/components/ui/empty-state"
import { EntityDetailView } from "./entity-detail-view"
import { useWikiEntities } from "@/hooks/use-wiki-entities"
import { fetchDomainCounts } from "@/lib/api/domains"
import { organizeByDomain } from "@/lib/graph/organize"
import { domainIcon } from "@/lib/graph/domain-icons"
import { domainSlot } from "@/lib/graph/identity"
import { SectionedEntityListWiki } from "@/components/shared/sectioned-entity-list"

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

function titleCase(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function WikiPane() {
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null)
  // Client-side ?domain= filter
  const [domainFilter, setDomainFilter] = useState<string | null>(null)

  const { data: entities, isLoading, isError, refetch } = useWikiEntities({ limit: 100 })
  const { data: domainCounts } = useQuery({
    queryKey: ["graph-domains"],
    queryFn: fetchDomainCounts,
    staleTime: 10 * 60_000,
    retry: 1,
  })

  // Cross-pane deep link: ?entity=<canonical_id> preselects entity; ?domain= pre-filters.
  useEffect(() => {
    if (typeof window === "undefined") return
    const params = new URLSearchParams(window.location.search)
    const entityParam = params.get("entity")
    const domainParam = params.get("domain")
    if (entityParam) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setSelectedSlug(entityParam)
      params.delete("entity")
    }
    if (domainParam) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setDomainFilter(domainParam)
      params.delete("domain")
    }
    if (entityParam || domainParam) {
      const next = params.toString()
      const url = `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`
      window.history.replaceState({}, "", url)
    }
  }, [])

  // Most-recently-updated entity for the header timestamp
  const mostRecentUpdated =
    entities && entities.length > 0
      ? entities.reduce(
          (latest, e) =>
            e.last_updated_at &&
            (!latest || e.last_updated_at > latest)
              ? e.last_updated_at
              : latest,
          null as string | null,
        )
      : null

  const relativeUpdated = formatLastUpdated(mostRecentUpdated)
  const entityCount = entities?.length ?? 0

  const handleSelect = (slug: string) => setSelectedSlug(slug)

  // Apply domain filter client-side
  const filteredEntities = useMemo(() => {
    if (!entities) return undefined
    if (!domainFilter) return entities
    return entities.filter((e) => e.primary_domain === domainFilter)
  }, [entities, domainFilter])

  // Organize entities into domain sections ordered by /graph/domains entity_count
  const { sections, headerless } = useMemo(() => {
    return organizeByDomain(
      filteredEntities ?? [],
      domainCounts?.domains ?? [],
    )
  }, [filteredEntities, domainCounts])

  // A7: show derivation hint only when /graph/domains derived_at === null
  const showDerivedAtHint = domainCounts !== undefined && domainCounts.derived_at === null

  return (
    <div className="flex h-full flex-col">
      {/* ------------------------------------------------------------------- */}
      {/* Pane header                                                         */}
      {/* ------------------------------------------------------------------- */}
      <div className="flex shrink-0 items-center gap-3 border-b px-4 py-3">
        <BookOpen className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <h1 className="text-sm font-semibold text-foreground">Wiki</h1>
        {!isLoading && !isError && (
          <>
            <span className="text-xs text-muted-foreground">
              {entityCount} {entityCount === 1 ? "entity" : "entities"}
            </span>
            {relativeUpdated && (
              <span className="ml-auto text-xs text-muted-foreground">
                Updated {relativeUpdated}
              </span>
            )}
          </>
        )}
      </div>

      {/* ------------------------------------------------------------------- */}
      {/* Two-column layout                                                   */}
      {/* ------------------------------------------------------------------- */}
      <div className="grid min-h-0 flex-1 grid-cols-[280px_1fr]">
        {/* ----------------------------------------------------------------- */}
        {/* Left: entity list + Categories index header                       */}
        {/* ----------------------------------------------------------------- */}
        <div className="flex flex-col border-r">
          {/* Categories index header — consumes /graph/domains */}
          {domainCounts && domainCounts.domains.length > 0 && (
            <div className="shrink-0 border-b px-3 py-2">
              <p className="mb-1 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Categories
              </p>
              <div className="flex flex-wrap gap-1">
                {/* "All" chip */}
                <button
                  type="button"
                  onClick={() => setDomainFilter(null)}
                  aria-pressed={domainFilter === null}
                  aria-label="Show all entities"
                  className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-label-xs transition-colors ${
                    domainFilter === null
                      ? "bg-foreground/10 font-medium text-foreground"
                      : "text-muted-foreground hover:bg-accent/40 hover:text-foreground"
                  }`}
                >
                  All
                </button>
                {domainCounts.domains.map((dc) => {
                  const Icon = domainIcon(dc.icon)
                  const slot = domainSlot(dc.name)
                  const isActive = domainFilter === dc.name
                  return (
                    <button
                      key={dc.name}
                      type="button"
                      onClick={() => setDomainFilter(isActive ? null : dc.name)}
                      aria-pressed={isActive}
                      aria-label={`Filter by ${titleCase(dc.name)} — ${dc.entity_count} entities`}
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-label-xs transition-colors ${
                        isActive
                          ? "font-medium"
                          : "text-muted-foreground hover:bg-accent/40 hover:text-foreground"
                      }`}
                      // drift-allowed: runtime domain slot color — CSS var resolved at paint
                      style={
                        isActive
                          ? {
                              color: `var(--color-domain-${slot})`,
                              backgroundColor: `color-mix(in oklab, var(--color-domain-${slot}) 12%, transparent)`,
                            }
                          : undefined
                      }
                    >
                      <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
                      <span>{titleCase(dc.name)}</span>
                      <span className="tabular-nums opacity-70">{dc.entity_count}</span>
                    </button>
                  )
                })}
              </div>
              {/* A7: derivation hint — only when job has never run */}
              {showDerivedAtHint && (
                <p className="mt-1.5 flex items-center gap-1 text-label-xs text-muted-foreground">
                  <Info className="h-3 w-3 shrink-0" aria-hidden="true" />
                  Domain data is derived nightly and shortly after ingest.
                </p>
              )}
            </div>
          )}

          <ScrollArea className="flex-1">
            {isLoading && (
              <div className="space-y-1 p-2" aria-label="Loading entities" role="status">
                <Skeleton className="h-16 w-full rounded-md" />
                <Skeleton className="h-16 w-full rounded-md" />
                <Skeleton className="h-16 w-full rounded-md" />
                <Skeleton className="h-16 w-full rounded-md" />
              </div>
            )}

            {isError && (
              <div className="p-2">
                <PaneError
                  title="Failed to load entities"
                  description="The backend may be unavailable. Try again."
                  onRetry={() => void refetch()}
                />
              </div>
            )}

            {!isLoading && !isError && (filteredEntities?.length ?? 0) === 0 && (
              <div className="p-2">
                <EmptyState
                  icon={BookOpen}
                  title={domainFilter ? `No entities in ${titleCase(domainFilter)}` : "No entities yet"}
                  description={
                    domainFilter
                      ? "Try a different category or clear the filter."
                      : "Add documents to your knowledge base to build entity pages."
                  }
                />
              </div>
            )}

            {!isLoading && !isError && (filteredEntities?.length ?? 0) > 0 && (
              <SectionedEntityListWiki
                variant="wiki"
                sections={sections}
                headerless={headerless}
                selectedSlug={selectedSlug}
                onSelect={handleSelect}
              />
            )}
          </ScrollArea>
        </div>

        {/* ----------------------------------------------------------------- */}
        {/* Right: detail view                                                */}
        {/* ----------------------------------------------------------------- */}
        <div className="min-h-0 overflow-hidden">
          {selectedSlug ? (
            <EntityDetailView
              slug={selectedSlug}
              onSelectRelated={handleSelect}
              onSelectDomain={(domain) => {
                setDomainFilter(domain)
                setSelectedSlug(null)
              }}
            />
          ) : (
            <div className="flex h-full items-center justify-center p-8">
              <EmptyState
                icon={BookOpen}
                title="Select an entity"
                description="Choose an entity from the list to view its wiki page."
              />
            </div>
          )}
        </div>
      </div>

      <Separator />
    </div>
  )
}
