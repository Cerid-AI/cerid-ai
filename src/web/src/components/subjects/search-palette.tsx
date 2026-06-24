// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Cmd-K search palette for the Subjects pane. Typing filters by name;
// results are grouped into a pinned "Best Matches" section (first 5 in
// server relevance order) followed by domain sections.
//
// Section-aware keyboard navigation: the highlight integer indexes entity
// rows only (headers are not focusable); flatItems from organizeWithPinned
// drives the index space.

import { useEffect, useMemo, useRef, useState, useId } from "react"
import { Search, X } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { useWikiEntities } from "@/hooks/use-wiki-entities"
import { fetchDomainCounts } from "@/lib/api/domains"
import { organizeWithPinned } from "@/lib/graph/organize"
import {
  SectionedEntityListPalette,
  BEST_MATCHES_DOMAIN,
} from "@/components/shared/sectioned-entity-list"
import { useNavigation } from "@/contexts/navigation-context"
import type { DomainSection } from "@/lib/graph/organize"

export interface SubjectsSearchPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onPick: (entityId: string) => void
}

export function SubjectsSearchPalette({ open, onOpenChange, onPick }: SubjectsSearchPaletteProps) {
  const [query, setQuery] = useState("")
  const [debouncedQuery, setDebouncedQuery] = useState("")
  // Section-aware flat highlight index (counts entity rows, not headers).
  const [highlight, setHighlight] = useState(0)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const listboxId = useId()
  const navigation = useNavigation()

  // Server-side search (F5): the query is pushed to /wiki/entities?q= so the
  // match spans the whole corpus, not just the first page the client fetched.
  const { data: entities } = useWikiEntities({ limit: 50, q: debouncedQuery })

  // /graph/domains for section ordering and counts
  const { data: domainCounts } = useQuery({
    queryKey: ["graph-domains"],
    queryFn: () => fetchDomainCounts(),
    staleTime: 10 * 60_000,
    retry: 1,
  })

  // Debounce the typed query into the server fetch (200 ms).
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 200)
    return () => clearTimeout(t)
  }, [query])

  // Focus on open; reset state.
  useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setQuery("")
      setHighlight(0)
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [open])

  // Client-side narrow while debounce settles; cap at 25 results.
  // When match_rank is present on any result, prefer it for the Best Matches
  // ordering (server relevance trumps client-side substring position).
  const filtered = useMemo(() => {
    const all = entities ?? []
    if (!query.trim()) return all.slice(0, 25)
    const lower = query.toLowerCase()
    const matches = all.filter(
      (e) => e.name.toLowerCase().includes(lower) || e.slug.toLowerCase().includes(lower),
    )
    const hasRank = matches.some((e) => e.match_rank != null)
    if (hasRank) {
      matches.sort((a, b) => {
        const ra = a.match_rank ?? 99
        const rb = b.match_rank ?? 99
        if (ra !== rb) return ra - rb
        return b.recent_activity_score - a.recent_activity_score
      })
    }
    return matches.slice(0, 25)
  }, [entities, query])

  // Reset highlight when filtered list changes.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    setHighlight(0)
  }, [query])

  // Organize into Best Matches + domain sections.
  // The combined section list: [Best Matches, ...domain sections].
  const { allSections, flatEntityCount } = useMemo(() => {
    if (filtered.length === 0) {
      return { allSections: [] as DomainSection[], flatEntityCount: 0 }
    }

    const { pinned, rest } = organizeWithPinned(
      filtered,
      domainCounts?.domains ?? [],
      { pinCount: 5, dedupeThreshold: 10, cap: 5 },
    )

    const sections: DomainSection[] = []
    if (pinned.entities.length > 0) {
      sections.push({
        domain: BEST_MATCHES_DOMAIN,
        label: "Best Matches",
        icon: null,
        count: pinned.entities.length,
        entities: pinned.entities,
        overflow: 0,
      })
    }
    sections.push(...rest.sections)

    const total = sections.reduce((n, s) => n + s.entities.length, 0)
    return { allSections: sections, flatEntityCount: total }
  }, [filtered, domainCounts])

  // Resolve the entity at a given flat index (for Enter key).
  function entityAtFlatIndex(idx: number) {
    let counter = 0
    for (const section of allSections) {
      for (const entity of section.entities) {
        if (counter === idx) return entity
        counter++
      }
    }
    return null
  }

  const handleNavigateToDomain = (domain: string | null) => {
    // Deep-link to wiki pane filtered to domain
    const params: Record<string, string> = { mode: "wiki" }
    if (domain) params.domain = domain
    navigation.goTo("subjects", params)
    onOpenChange(false)
  }

  if (!open) return null

  const isEmpty = allSections.length === 0

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- modal backdrop; handlers dismiss on outside-click / Escape
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Search subjects"
      className="fixed inset-0 z-50 flex items-start justify-center bg-background/80 backdrop-blur-sm pt-24"
      onClick={(e) => {
        if (e.target === e.currentTarget) onOpenChange(false)
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onOpenChange(false)
      }}
    >
      <div className="liquid-glass w-full max-w-lg rounded-xl border border-border/60 shadow-2xl">
        {/* Input row */}
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <Search className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded={!isEmpty}
            aria-controls={listboxId}
            aria-activedescendant={
              !isEmpty && flatEntityCount > 0
                ? `${listboxId}-option-${highlight}`
                : undefined
            }
            aria-autocomplete="list"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault()
                onOpenChange(false)
              } else if (e.key === "ArrowDown") {
                e.preventDefault()
                setHighlight((h) => Math.min(flatEntityCount - 1, h + 1))
              } else if (e.key === "ArrowUp") {
                e.preventDefault()
                setHighlight((h) => Math.max(0, h - 1))
              } else if (e.key === "Enter") {
                e.preventDefault()
                const pick = entityAtFlatIndex(highlight)
                if (pick) onPick(pick.slug)
              }
            }}
            placeholder="Search entities by name or canonical id…"
            className="grow bg-transparent text-sm placeholder:text-muted-foreground focus:outline-none"
            aria-label="Search query"
          />
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="rounded p-1 text-muted-foreground hover:bg-accent/40"
            aria-label="Close search"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Results */}
        {isEmpty ? (
          <div role="listbox" id={listboxId} aria-label="Search results">
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">
              {query ? "No entities match" : "Type to search"}
            </div>
          </div>
        ) : (
          <SectionedEntityListPalette
            variant="palette"
            sections={allSections}
            highlightIndex={highlight}
            onHighlight={setHighlight}
            onPick={onPick}
            onNavigateToDomain={handleNavigateToDomain}
            listboxId={listboxId}
            headerless={allSections.length <= 1}
          />
        )}

        {/* Footer hints */}
        <div className="flex items-center justify-between border-t px-4 py-2 text-label-xs text-muted-foreground">
          <div className="flex items-center gap-2">
            <kbd className="rounded border bg-background px-1">↑</kbd>
            <kbd className="rounded border bg-background px-1">↓</kbd>
            <span>navigate</span>
          </div>
          <div className="flex items-center gap-2">
            <kbd className="rounded border bg-background px-1">↵</kbd>
            <span>select</span>
            <span className="mx-1">·</span>
            <kbd className="rounded border bg-background px-1">esc</kbd>
            <span>close</span>
          </div>
        </div>
      </div>
    </div>
  )
}
