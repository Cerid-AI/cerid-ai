// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useMemo, useState } from "react"
import { List, AlertCircle } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { EmptyState } from "@/components/ui/empty-state"
import { Badge } from "@/components/ui/badge"
import { fetchWikiIndex } from "@/lib/api/wiki-browse"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function firstLetter(name: string): string {
  const ch = name.trim()[0]
  if (!ch) return "#"
  const up = ch.toUpperCase()
  return /[A-Z]/.test(up) ? up : "#"
}

function entityTypeLabel(t: string): string {
  const map: Record<string, string> = {
    ORG: "Org",
    PERSON: "Person",
    OTHER: "Other",
    CONCEPT: "Concept",
    EVENT: "Event",
    PLACE: "Place",
  }
  return map[t.toUpperCase()] ?? t
}

// ---------------------------------------------------------------------------
// WikiIndexView
// ---------------------------------------------------------------------------

export interface WikiIndexViewProps {
  onSelectEntity: (slug: string) => void
}

export function WikiIndexView({ onSelectEntity }: WikiIndexViewProps) {
  const [search, setSearch] = useState("")

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["wiki-index-az"],
    queryFn: () => fetchWikiIndex({ order: "name" }),
    staleTime: 5 * 60_000,
    retry: 1,
  })

  // Client-side search filter while debounce is not needed here (sync)
  const filtered = useMemo(() => {
    if (!data) return []
    const q = search.trim().toLowerCase()
    if (!q) return data.entries
    return data.entries.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        e.slug.toLowerCase().includes(q),
    )
  }, [data, search])

  // Group into A-Z buckets
  const grouped = useMemo(() => {
    const buckets = new Map<string, typeof filtered>()
    for (const entry of filtered) {
      const letter = firstLetter(entry.name)
      const existing = buckets.get(letter)
      if (existing) {
        existing.push(entry)
      } else {
        buckets.set(letter, [entry])
      }
    }
    // Sort keys: A-Z then #
    return [...buckets.entries()].sort(([a], [b]) => {
      if (a === "#") return 1
      if (b === "#") return -1
      return a.localeCompare(b)
    })
  }, [filtered])

  // Amendment #7 guard: show honest "N of M" when totals indicate truncation
  const isTruncated =
    data?.total !== null &&
    data?.total !== undefined &&
    data.entries.length < data.total

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl px-4 py-6">
        {/* Header */}
        <div className="mb-4 flex items-center justify-between gap-4">
          <h1 className="flex items-center gap-2 text-lg font-semibold">
            <List className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
            A–Z Index
          </h1>
          {data && (
            <span className="text-sm text-muted-foreground tabular-nums">
              {isTruncated
                ? `Showing ${data.entries.length} of ${data.total}`
                : `${data.entries.length} entities`}
            </span>
          )}
        </div>

        {/* Search within index */}
        <div className="mb-4">
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter by name or id…"
            aria-label="Filter entities"
            className="w-full rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        {/* 4-state matrix */}
        {isLoading && (
          <div className="space-y-4" role="status" aria-label="Loading index">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="space-y-1">
                <Skeleton className="h-5 w-8" />
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-5/6" />
              </div>
            ))}
          </div>
        )}

        {isError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" aria-hidden="true" />
            <AlertTitle>Failed to load index</AlertTitle>
            <AlertDescription className="flex items-center justify-between gap-2">
              <span>{error instanceof Error ? error.message : "An error occurred."}</span>
              <button
                type="button"
                onClick={() => void refetch()}
                className="shrink-0 text-xs underline underline-offset-2 hover:no-underline"
              >
                Retry
              </button>
            </AlertDescription>
          </Alert>
        )}

        {!isLoading && !isError && filtered.length === 0 && (
          <EmptyState
            icon={List}
            title={search ? "No matches" : "No entities yet"}
            description={
              search
                ? "Try a different search term."
                : "Add documents to your knowledge base to build entity pages."
            }
          />
        )}

        {!isLoading && !isError && filtered.length > 0 && (
          <div className="space-y-6">
            {grouped.map(([letter, entries]) => (
              <section key={letter} aria-labelledby={`az-section-${letter}`}>
                <h2
                  id={`az-section-${letter}`}
                  className="mb-2 border-b pb-1 text-sm font-bold text-muted-foreground"
                >
                  {letter}
                </h2>
                <ul className="divide-y rounded-md border">
                  {entries.map((entry) => (
                    <li key={entry.slug}>
                      <button
                        type="button"
                        onClick={() => onSelectEntity(entry.slug)}
                        className="flex w-full items-start gap-3 px-3 py-2.5 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        aria-label={`${entry.name}${entry.has_summary ? "" : " — summary pending"}`}
                      >
                        <span className="min-w-0 flex-1">
                          <span
                            className={
                              entry.has_summary
                                ? "text-sm font-medium text-foreground"
                                : "text-sm font-medium text-muted-foreground [text-decoration:underline_dashed] underline-offset-2"
                            }
                          >
                            {entry.name}
                          </span>
                          {entry.one_liner && (
                            <span className="mt-0.5 block text-xs text-muted-foreground line-clamp-1">
                              {entry.one_liner}
                            </span>
                          )}
                          {!entry.has_summary && (
                            <span className="mt-0.5 block text-label-xs text-muted-foreground/70 italic">
                              Summary pending nightly refresh
                            </span>
                          )}
                        </span>
                        <Badge variant="outline" className="shrink-0 text-label-xs mt-0.5">
                          {entityTypeLabel(entry.entity_type)}
                        </Badge>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
