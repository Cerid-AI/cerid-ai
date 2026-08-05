// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useEffect, useRef, useState } from "react"
import { cn } from "@/lib/utils"

// ---------------------------------------------------------------------------
// Article TOC — Wikipedia-style sticky Table of Contents.
//
// Design rules:
//   - sticky <nav aria-label="Contents">
//   - IntersectionObserver current-section highlight
//   - Entries render only for sections that have data (callers pass the list)
//   - <details> disclosure below lg (collapses on mobile)
// ---------------------------------------------------------------------------

export interface TocEntry {
  id: string
  label: string
  /** Optional sub-entries for a two-level TOC. Not used in v1 but accepted. */
  children?: TocEntry[]
}

interface ArticleTocProps {
  entries: TocEntry[]
  className?: string
  /** Override aria-label for the nav landmark (use to disambiguate multiple TOC instances). */
  ariaLabel?: string
}

export function ArticleToc({ entries, className, ariaLabel = "Contents" }: ArticleTocProps) {
  const [activeId, setActiveId] = useState<string | null>(null)
  const observerRef = useRef<IntersectionObserver | null>(null)

  useEffect(() => {
    if (typeof window === "undefined") return
    if (entries.length === 0) return

    const ids = entries.map((e) => e.id)

    // Build an ordered list so we can pick the topmost visible section.
    const visibleSections = new Map<string, boolean>()

    observerRef.current?.disconnect()

    observerRef.current = new IntersectionObserver(
      (entries_) => {
        for (const entry of entries_) {
          visibleSections.set(entry.target.id, entry.isIntersecting)
        }
        // Pick the first id (in document order) that is visible.
        const firstVisible = ids.find((id) => visibleSections.get(id))
        if (firstVisible) setActiveId(firstVisible)
      },
      {
        // Trigger when the top of a section enters the top 20% of the viewport.
        rootMargin: "-8px 0px -80% 0px",
      },
    )

    for (const id of ids) {
      const el = document.getElementById(id)
      if (el) observerRef.current.observe(el)
    }

    return () => {
      observerRef.current?.disconnect()
    }
  }, [entries])

  if (entries.length === 0) return null

  const tocList = (
    <ol className="space-y-0.5 text-xs" aria-label="Table of contents">
      {entries.map((entry, i) => (
        <li key={entry.id}>
          <a
            href={`#${entry.id}`}
            className={cn(
              "block rounded px-2 py-1 underline-offset-2 transition-colors hover:bg-muted/50 hover:text-foreground",
              activeId === entry.id
                ? "bg-brand/10 font-medium text-brand"
                : "text-muted-foreground",
            )}
            aria-current={activeId === entry.id ? "location" : undefined}
            onClick={(e) => {
              e.preventDefault()
              const target = document.getElementById(entry.id)
              if (target) {
                target.scrollIntoView({ behavior: "smooth", block: "start" })
                setActiveId(entry.id)
              }
            }}
          >
            <span className="mr-1 tabular-nums text-muted-foreground/60">{i + 1}</span>
            {entry.label}
          </a>
          {entry.children && entry.children.length > 0 && (
            <ol className="ml-3 mt-0.5 space-y-0.5">
              {entry.children.map((child) => (
                <li key={child.id}>
                  <a
                    href={`#${child.id}`}
                    className={cn(
                      "block rounded px-2 py-1 text-label-xs underline-offset-2 transition-colors hover:bg-muted/50 hover:text-foreground",
                      activeId === child.id
                        ? "bg-brand/10 font-medium text-brand"
                        : "text-muted-foreground",
                    )}
                    aria-current={activeId === child.id ? "location" : undefined}
                    onClick={(e) => {
                      e.preventDefault()
                      const target = document.getElementById(child.id)
                      if (target) {
                        target.scrollIntoView({ behavior: "smooth", block: "start" })
                        setActiveId(child.id)
                      }
                    }}
                  >
                    {child.label}
                  </a>
                </li>
              ))}
            </ol>
          )}
        </li>
      ))}
    </ol>
  )

  return (
    <nav
      aria-label={ariaLabel}
      className={cn("text-sm", className)}
    >
      {/* On lg+: always visible. Below lg: collapsed in a <details> element. */}
      <div className="hidden lg:block">
        <p className="mb-2 px-2 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Contents
        </p>
        {tocList}
      </div>
      <details className="lg:hidden">
        <summary className="cursor-pointer list-none px-2 py-1 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Contents
        </summary>
        <div className="mt-1">{tocList}</div>
      </details>
    </nav>
  )
}
