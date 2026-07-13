// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * WikiHeroSearch — prominent landing-page search over the wiki corpus.
 *
 * Reuses the existing server-side search machinery (GET /wiki/entities?q=)
 * behind a short debounce. Results render in an overlay panel where every
 * row is a real <button>, so the whole flow is keyboard-reachable. The
 * panel footer bridges to chat via useNavigation().composeChat so a query
 * with no good article can become a question instead.
 */

import { useEffect, useRef, useState } from "react"
import { AlertCircle, MessageSquareText, Search, X } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { DomainBadge } from "@/components/ui/domain-badge"
import { fetchWikiEntities } from "@/lib/api/wiki"
import { useNavigation } from "@/contexts/navigation-context"

const SEARCH_DEBOUNCE_MS = 200
const MIN_QUERY_LENGTH = 2
const RESULT_LIMIT = 8

export interface WikiHeroSearchProps {
  onSelectEntity: (slug: string) => void
  /** WK2 advanced toggle — forwarded to the search query. */
  includeInternal: boolean
}

export function WikiHeroSearch({ onSelectEntity, includeInternal }: WikiHeroSearchProps) {
  const [query, setQuery] = useState("")
  const [debounced, setDebounced] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)
  const { composeChat } = useNavigation()

  useEffect(() => {
    const id = setTimeout(() => setDebounced(query.trim()), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [query])

  const active = debounced.length >= MIN_QUERY_LENGTH

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["wiki-hero-search", debounced, includeInternal],
    queryFn: () => fetchWikiEntities({ q: debounced, limit: RESULT_LIMIT, includeInternal }),
    enabled: active,
    staleTime: 60_000,
    retry: 1,
  })

  const clear = () => {
    setQuery("")
    setDebounced("")
    inputRef.current?.focus()
  }

  const results = data ?? []

  return (
    <div role="search" className="relative">
      <Search
        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
      <Input
        ref={inputRef}
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") clear()
        }}
        placeholder="Search the wiki…"
        aria-label="Search the wiki"
        className="h-11 pl-9 pr-9 text-base [&::-webkit-search-cancel-button]:hidden"
      />
      {query.length > 0 && (
        <button
          type="button"
          onClick={clear}
          aria-label="Clear search"
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      )}

      {active && (
        <div
          role="region"
          aria-label="Search results"
          className="absolute left-0 right-0 top-full z-20 mt-2 overflow-hidden rounded-lg border bg-popover text-popover-foreground shadow-md"
        >
          {isLoading && (
            <div className="space-y-2 p-3" role="status" aria-label="Searching">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-5/6" />
              <Skeleton className="h-9 w-4/6" />
            </div>
          )}

          {isError && (
            <Alert variant="destructive" className="m-3 w-auto">
              <AlertCircle className="h-4 w-4" aria-hidden="true" />
              <AlertTitle>Search failed</AlertTitle>
              <AlertDescription className="flex items-center justify-between gap-2">
                <span>Could not search the wiki.</span>
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

          {!isLoading && !isError && results.length === 0 && (
            <p className="px-3 py-2.5 text-sm text-muted-foreground">
              No articles match &ldquo;{debounced}&rdquo;.
            </p>
          )}

          {!isLoading && !isError && results.length > 0 && (
            <ul className="max-h-80 divide-y overflow-y-auto">
              {results.map((entity) => (
                <li key={entity.slug}>
                  <button
                    type="button"
                    onClick={() => onSelectEntity(entity.slug)}
                    className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{entity.name}</span>
                      {entity.summary_preview && (
                        <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                          {entity.summary_preview}
                        </span>
                      )}
                    </span>
                    {entity.primary_domain && <DomainBadge domain={entity.primary_domain} />}
                  </button>
                </li>
              ))}
            </ul>
          )}

          {!isLoading && !isError && (
            <button
              type="button"
              onClick={() => composeChat({ text: debounced })}
              className="flex w-full items-center gap-2 border-t px-3 py-2.5 text-left text-sm transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <MessageSquareText className="h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
              <span>
                Ask about <span className="font-medium">&ldquo;{debounced}&rdquo;</span> in chat
              </span>
            </button>
          )}
        </div>
      )}
    </div>
  )
}
