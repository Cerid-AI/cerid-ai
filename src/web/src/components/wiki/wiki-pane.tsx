// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useMemo, useState } from "react"
import { BookOpen, ChevronLeft, ChevronRight, Info } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { PaneError } from "@/components/ui/pane-error"
import { EmptyState } from "@/components/ui/empty-state"
import { EntityDetailView } from "./entity-detail-view"
import { WikiLanding } from "./wiki-landing"
import { ConceptPage } from "./concept-page"
import { WikiIndexView } from "./wiki-index-view"
import { useWikiEntities } from "@/hooks/use-wiki-entities"
import { fetchDomainCounts } from "@/lib/api/domains"
import { collectTopTags, filterEntitiesByTags, organizeByDomain } from "@/lib/graph/organize"
import { TagFilterBar } from "@/components/wiki/tag-filter-bar"
import { domainIcon } from "@/lib/graph/domain-icons"
import { domainSlot } from "@/lib/graph/identity"
import { SectionedEntityListWiki } from "@/components/shared/sectioned-entity-list"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

const RAIL_OPEN_KEY = "cerid-wiki-rail-open"

// Backend /wiki/entities caps `limit` at 200; request the full cap for the rail.
const WIKI_RAIL_LIMIT = 200

function readRailOpen(): boolean {
  try {
    const v = localStorage.getItem(RAIL_OPEN_KEY)
    if (v === "false") return false
    if (v === "true") return true
  } catch { /* SSR */ }
  return true // default open at root
}

function writeRailOpen(open: boolean): void {
  try {
    localStorage.setItem(RAIL_OPEN_KEY, String(open))
  } catch { /* SSR */ }
}

// ---------------------------------------------------------------------------
// WikiPane view modes
// ---------------------------------------------------------------------------

type WikiView =
  | { kind: "landing" }
  | { kind: "entity"; slug: string }
  | { kind: "concept"; conceptId: string }
  | { kind: "index" }

// Derive a breadcrumb label for the current view
function breadcrumbLabel(
  view: WikiView,
  entityName: string | null,
  conceptLabel: string | null,
): string | null {
  switch (view.kind) {
    case "landing": return null
    case "index": return "A–Z Index"
    case "concept":
      return conceptLabel ?? "Community"
    case "entity":
      return entityName ?? view.slug
  }
}

// ---------------------------------------------------------------------------
// WikiPane
// ---------------------------------------------------------------------------

export default function WikiPane() {
  const [view, setView] = useState<WikiView>({ kind: "landing" })
  const [domainFilter, setDomainFilter] = useState<string | null>(null)
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set())
  const [railOpen, setRailOpen] = useState<boolean>(readRailOpen)

  // Entity name for breadcrumb (resolved from the entity list when available)
  const [entityName, setEntityName] = useState<string | null>(null)
  const [conceptLabel, setConceptLabel] = useState<string | null>(null)

  // 200 is the backend max for /wiki/entities. When the result fills the cap
  // there may be more, so the header labels it "Showing first N" rather than
  // claiming an exact corpus size. (Full pagination/virtualization with a
  // backend total is a deferred enhancement — see audit follow-ups.)
  const { data: entities, isLoading, isError, refetch } = useWikiEntities({ limit: WIKI_RAIL_LIMIT })
  const { data: domainCounts } = useQuery({
    queryKey: ["graph-domains"],
    queryFn: () => fetchDomainCounts(),
    staleTime: 10 * 60_000,
    retry: 1,
  })

  // ---- URL-param deep links ----
  useEffect(() => {
    if (typeof window === "undefined") return
    const params = new URLSearchParams(window.location.search)
    const entityParam = params.get("entity")
    const domainParam = params.get("domain")
    const conceptParam = params.get("concept")
    const indexParam = params.get("wiki_index")

    let changed = false
    if (entityParam) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setView({ kind: "entity", slug: entityParam })
      params.delete("entity")
      changed = true
    } else if (conceptParam) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setView({ kind: "concept", conceptId: conceptParam })
      params.delete("concept")
      changed = true
    } else if (indexParam) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setView({ kind: "index" })
      params.delete("wiki_index")
      changed = true
    }
    if (domainParam) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setDomainFilter(domainParam)
      params.delete("domain")
      changed = true
    }
    if (changed) {
      const next = params.toString()
      const url = `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`
      window.history.replaceState({}, "", url)
    }
  }, [])

  // Rail auto-collapses when an article/concept/index is selected
  useEffect(() => {
    if (view.kind !== "landing") {
      const wasOpen = readRailOpen()
      if (wasOpen) {
        setRailOpen(false)
        writeRailOpen(false)
      }
    } else {
      // restore on landing
      setRailOpen(true)
      writeRailOpen(true)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- only run when view.kind changes
  }, [view.kind])

  // Track entity name for breadcrumb
  useEffect(() => {
    if (view.kind === "entity" && entities) {
      const found = entities.find((e) => e.slug === view.slug)
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setEntityName(found?.name ?? null)
    } else {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setEntityName(null)
    }
  }, [view, entities])

  const mostRecentUpdated =
    entities && entities.length > 0
      ? entities.reduce(
          (latest, e) =>
            e.last_updated_at && (!latest || e.last_updated_at > latest)
              ? e.last_updated_at
              : latest,
          null as string | null,
        )
      : null

  const relativeUpdated = formatLastUpdated(mostRecentUpdated)
  const entityCount = entities?.length ?? 0

  const handleSelectEntity = (slug: string) => {
    setView({ kind: "entity", slug })
  }

  const handleSelectDomain = (domain: string) => {
    setDomainFilter(domain)
    setView({ kind: "landing" })
  }

  const handleSelectConcept = (conceptId: string, label?: string) => {
    setView({ kind: "concept", conceptId })
    if (label) setConceptLabel(label)
  }

  const handleOpenIndex = () => {
    setView({ kind: "index" })
  }

  const toggleRail = () => {
    setRailOpen((prev) => {
      writeRailOpen(!prev)
      return !prev
    })
  }

  // Apply domain + tag filters client-side (Slice 6.3 adds the tag filter)
  const filteredEntities = useMemo(() => {
    if (!entities) return undefined
    const byDomain = domainFilter
      ? entities.filter((e) => e.primary_domain === domainFilter)
      : entities
    return filterEntitiesByTags(byDomain, selectedTags)
  }, [entities, domainFilter, selectedTags])

  // Available tag chips — union across the domain-filtered set (so the bar
  // reflects what's actually visible), salience/frequency-ordered.
  const availableTags = useMemo(() => {
    if (!entities) return []
    const byDomain = domainFilter
      ? entities.filter((e) => e.primary_domain === domainFilter)
      : entities
    return collectTopTags(byDomain)
  }, [entities, domainFilter])

  const toggleTag = (tag: string) =>
    setSelectedTags((prev) => {
      const next = new Set(prev)
      if (next.has(tag)) next.delete(tag)
      else next.add(tag)
      return next
    })

  const { sections, headerless } = useMemo(() => {
    return organizeByDomain(
      filteredEntities ?? [],
      domainCounts?.domains ?? [],
    )
  }, [filteredEntities, domainCounts])

  const showDerivedAtHint = domainCounts !== undefined && domainCounts.derived_at === null

  // Breadcrumb
  const bcLabel = breadcrumbLabel(
    view,
    entityName,
    conceptLabel,
  )
  const domainForBc =
    view.kind === "entity" && entityName && entities
      ? (entities.find((e) => e.slug === (view as { kind: "entity"; slug: string }).slug)?.primary_domain ?? null)
      : domainFilter

  return (
    <div className="flex h-full flex-col">
      {/* ------------------------------------------------------------------- */}
      {/* Pane header                                                         */}
      {/* ------------------------------------------------------------------- */}
      <div className="flex shrink-0 items-center gap-2 border-b px-4 py-3">
        <BookOpen className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />

        {/* Breadcrumb */}
        {view.kind === "landing" ? (
          <h1 className="text-sm font-semibold text-foreground">Wiki</h1>
        ) : (
          <nav aria-label="Wiki breadcrumb" className="flex min-w-0 items-center gap-1 text-sm">
            <button
              type="button"
              onClick={() => setView({ kind: "landing" })}
              className="font-semibold text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
            >
              Wiki
            </button>
            {domainForBc && (
              <>
                <span className="text-muted-foreground" aria-hidden="true">›</span>
                <button
                  type="button"
                  onClick={() => handleSelectDomain(domainForBc)}
                  className="text-muted-foreground hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded truncate max-w-[120px]" // drift-allowed: truncated breadcrumb domain-segment width pin
                >
                  {titleCase(domainForBc)}
                </button>
              </>
            )}
            {bcLabel && (
              <>
                <span className="text-muted-foreground" aria-hidden="true">›</span>
                <span className="truncate max-w-[180px] text-muted-foreground font-medium" aria-current="page"> {/* drift-allowed: truncated breadcrumb current-page width pin */}
                  {bcLabel}
                </span>
              </>
            )}
          </nav>
        )}

        {!isLoading && !isError && view.kind === "landing" && (
          <>
            <span className="text-xs text-muted-foreground">
              {entityCount === WIKI_RAIL_LIMIT
                ? `Showing first ${WIKI_RAIL_LIMIT}`
                : `${entityCount} ${entityCount === 1 ? "entity" : "entities"}`}
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
      {/* Two-column layout (rail + main)                                     */}
      {/* ------------------------------------------------------------------- */}
      <div className={`grid min-h-0 flex-1 ${railOpen ? "grid-cols-[280px_1fr]" : "grid-cols-[0px_1fr]"} transition-[grid-template-columns] duration-200`}>
        {/* ----------------------------------------------------------------- */}
        {/* Left: entity list + Categories index header (collapsible)         */}
        {/* ----------------------------------------------------------------- */}
        <div
          className={`flex flex-col border-r overflow-hidden transition-all duration-200 ${railOpen ? "opacity-100" : "opacity-0 pointer-events-none"}`}
          aria-hidden={!railOpen}
        >
          {/* Categories index header — consumes /graph/domains */}
          {domainCounts && domainCounts.domains.length > 0 && (
            <div className="shrink-0 border-b px-3 py-2">
              <p className="mb-1 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Categories
              </p>
              <div className="flex flex-wrap gap-1">
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

            {!isLoading && !isError && availableTags.length > 0 && (
              <TagFilterBar
                tags={availableTags}
                selected={selectedTags}
                onToggle={toggleTag}
                onClear={() => setSelectedTags(new Set())}
              />
            )}

            {!isLoading && !isError && (filteredEntities?.length ?? 0) > 0 && (
              <SectionedEntityListWiki
                variant="wiki"
                sections={sections}
                headerless={headerless}
                selectedSlug={view.kind === "entity" ? view.slug : null}
                onSelect={handleSelectEntity}
              />
            )}
          </ScrollArea>
        </div>

        {/* ----------------------------------------------------------------- */}
        {/* Main content area                                                 */}
        {/* ----------------------------------------------------------------- */}
        <div className="relative min-h-0 overflow-hidden">
          {/* Rail toggle button — floats at the edge of the content area */}
          <button
            type="button"
            onClick={toggleRail}
            aria-label={railOpen ? "Collapse navigation rail" : "Expand navigation rail"}
            className="absolute left-0 top-1/2 z-10 -translate-y-1/2 flex h-8 w-4 items-center justify-center rounded-r-md border border-l-0 bg-background text-muted-foreground shadow-sm transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {railOpen
              ? <ChevronLeft className="h-3 w-3" aria-hidden="true" />
              : <ChevronRight className="h-3 w-3" aria-hidden="true" />
            }
          </button>

          {/* View content */}
          {view.kind === "landing" && (
            <WikiLanding
              onSelectEntity={handleSelectEntity}
              onSelectDomain={handleSelectDomain}
              onOpenIndex={handleOpenIndex}
            />
          )}

          {view.kind === "entity" && (
            <EntityDetailView
              slug={view.slug}
              onSelectRelated={handleSelectEntity}
              onSelectDomain={(domain) => {
                setDomainFilter(domain)
                setView({ kind: "landing" })
              }}
              onSelectConcept={handleSelectConcept}
            />
          )}

          {view.kind === "concept" && (
            <ConceptPage
              conceptId={view.conceptId}
              onSelectEntity={handleSelectEntity}
            />
          )}

          {view.kind === "index" && (
            <WikiIndexView onSelectEntity={handleSelectEntity} />
          )}
        </div>
      </div>
    </div>
  )
}
