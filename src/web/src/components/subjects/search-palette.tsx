// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Cmd-K search palette for the Subjects pane. Lightweight Dialog over
// the Wiki entity list — typing filters by name; Enter selects.
// Future iterations (Day 11+) can swap the data source to a richer
// /search endpoint with cross-entity fuzzy matching + provenance previews.

import { useEffect, useMemo, useRef, useState } from "react"
import { Search, X } from "lucide-react"
import { useWikiEntities } from "@/hooks/use-wiki-entities"

export interface SubjectsSearchPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onPick: (entityId: string) => void
}

export function SubjectsSearchPalette({ open, onOpenChange, onPick }: SubjectsSearchPaletteProps) {
  const [query, setQuery] = useState("")
  const [highlight, setHighlight] = useState(0)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const { data: entities } = useWikiEntities({ limit: 50 })

  // Focus on open
  useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setQuery("")
      setHighlight(0)
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [open])

  const filtered = useMemo(() => {
    const all = entities ?? []
    if (!query.trim()) return all.slice(0, 25)
    const lower = query.toLowerCase()
    return all
      .filter((e) => e.name.toLowerCase().includes(lower) || e.slug.toLowerCase().includes(lower))
      .slice(0, 25)
  }, [entities, query])

  // Reset highlight when filtered list changes
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    setHighlight(0)
  }, [query])

  if (!open) return null

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
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault()
                onOpenChange(false)
              } else if (e.key === "ArrowDown") {
                e.preventDefault()
                setHighlight((h) => Math.min(filtered.length - 1, h + 1))
              } else if (e.key === "ArrowUp") {
                e.preventDefault()
                setHighlight((h) => Math.max(0, h - 1))
              } else if (e.key === "Enter") {
                e.preventDefault()
                const pick = filtered[highlight]
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
        <ul role="listbox" aria-label="Search results" className="max-h-80 overflow-y-auto py-2">
          {filtered.length === 0 ? (
            <li className="px-4 py-6 text-center text-sm text-muted-foreground">
              {query ? "No entities match" : "Type to search"}
            </li>
          ) : (
            filtered.map((entity, idx) => (
              <li
                key={entity.slug}
                role="option"
                aria-selected={idx === highlight}
                style={{ ["--i" as string]: Math.min(idx, 8) }}
                className="cerid-stagger-fast"
              >
                <button
                  type="button"
                  onClick={() => onPick(entity.slug)}
                  onMouseEnter={() => setHighlight(idx)}
                  className={`flex w-full items-center justify-between px-4 py-2 text-left text-sm transition-colors ${
                    idx === highlight ? "bg-accent text-accent-foreground" : "text-foreground/85 hover:bg-accent/40"
                  }`}
                >
                  <span className="truncate">{entity.name}</span>
                  <span className="ml-2 shrink-0 text-label-xs text-muted-foreground">
                    {entity.slug}
                  </span>
                </button>
              </li>
            ))
          )}
        </ul>

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
