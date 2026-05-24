// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react"
import { BookOpen } from "lucide-react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { PaneError } from "@/components/ui/pane-error"
import { EmptyState } from "@/components/ui/empty-state"
import { EntityListItem } from "./entity-list-item"
import { EntityDetailView } from "./entity-detail-view"
import { useWikiEntities } from "@/hooks/use-wiki-entities"

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

export default function WikiPane() {
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null)
  const { data: entities, isLoading, isError, refetch } = useWikiEntities({ limit: 30 })

  // Cross-pane deep link: ?entity=<canonical_id> from the Communities
  // pane preselects the matching entity. We strip the param so reloads
  // don't re-select. Canonical id == slug for the wiki backend.
  useEffect(() => {
    if (typeof window === "undefined") return
    const params = new URLSearchParams(window.location.search)
    const entityParam = params.get("entity")
    if (!entityParam) return
    setSelectedSlug(entityParam)
    params.delete("entity")
    const next = params.toString()
    const url = `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`
    window.history.replaceState({}, "", url)
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
        {/* Left: entity list                                                 */}
        {/* ----------------------------------------------------------------- */}
        <div className="flex flex-col border-r">
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

            {!isLoading && !isError && entities?.length === 0 && (
              <div className="p-2">
                <EmptyState
                  icon={BookOpen}
                  title="No entities yet"
                  description="Add documents to your knowledge base to build entity pages."
                />
              </div>
            )}

            {!isLoading && !isError && entities && entities.length > 0 && (
              <ul className="space-y-1 p-2" aria-label="Entity list">
                {entities.map((entity, idx) => (
                  <li
                    key={entity.slug}
                    style={{ ["--i" as string]: Math.min(idx, 8) }}
                    className="cerid-stagger-fast"
                  >
                    <EntityListItem
                      entity={entity}
                      selected={entity.slug === selectedSlug}
                      onSelect={handleSelect}
                    />
                  </li>
                ))}
              </ul>
            )}
          </ScrollArea>
        </div>

        {/* ----------------------------------------------------------------- */}
        {/* Right: detail view                                                */}
        {/* ----------------------------------------------------------------- */}
        <div className="min-h-0 overflow-hidden">
          {selectedSlug ? (
            <EntityDetailView slug={selectedSlug} onSelectRelated={handleSelect} />
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
