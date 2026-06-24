// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * WK1 — "What links here" collapsible section.
 *
 * Fetches backlinks for the current entity and renders them grouped by
 * `via` source (wikilink > mention > related). Each row is a button that
 * navigates to the source entity's wiki page.
 *
 * Four-state: loading / empty / error / loaded.
 */

import { useState, useEffect } from "react"
import { Link2, ChevronDown, ChevronRight } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { PaneError } from "@/components/ui/pane-error"
import { Button } from "@/components/ui/button"
import { fetchBacklinks } from "@/lib/api/wiki"
import type { BacklinkItem, BacklinkVia } from "@/lib/types/wiki"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface WhatLinksHereProps {
  /** Canonical slug of the entity whose backlinks to show. */
  entitySlug: string
  /** Called when a backlink row is clicked — navigate to that entity. */
  onSelectEntity: (slug: string) => void
}

// ---------------------------------------------------------------------------
// Via-source metadata
// ---------------------------------------------------------------------------

const VIA_LABEL: Record<BacklinkVia, string> = {
  wikilink: "Wikilinks",
  mention: "Co-mentioned in sources",
  related: "Related (graph edge)",
}

const VIA_ORDER: BacklinkVia[] = ["wikilink", "mention", "related"]

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function WhatLinksHere({ entitySlug, onSelectEntity }: WhatLinksHereProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [status, setStatus] = useState<"idle" | "loading" | "loaded" | "error">("idle")
  const [items, setItems] = useState<BacklinkItem[]>([])
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  // Fetch when expanded for the first time.
  useEffect(() => {
    if (!isOpen || status !== "idle") return

    setStatus("loading")
    fetchBacklinks(entitySlug)
      .then(({ backlinks }) => {
        setItems(backlinks)
        setStatus("loaded")
      })
      .catch((err: unknown) => {
        setErrorMsg(err instanceof Error ? err.message : "Failed to load backlinks")
        setStatus("error")
      })
  }, [isOpen, entitySlug, status])

  // Reset when slug changes so the next expand re-fetches.
  useEffect(() => {
    setStatus("idle")
    setItems([])
    setErrorMsg(null)
    setIsOpen(false)
  }, [entitySlug])

  function handleRetry() {
    setStatus("idle")
    setErrorMsg(null)
  }

  // Group by via-source in canonical order.
  const groups: Record<BacklinkVia, BacklinkItem[]> = {
    wikilink: [],
    mention: [],
    related: [],
  }
  for (const item of items) {
    groups[item.via].push(item)
  }
  const hasAny = items.length > 0

  return (
    <section
      aria-labelledby="wiki-what-links-here-heading"
      data-testid="what-links-here-section"
    >
      {/* Collapsible trigger */}
      <button
        type="button"
        id="wiki-what-links-here-heading"
        aria-expanded={isOpen}
        aria-controls="wiki-what-links-here-body"
        onClick={() => setIsOpen((prev) => !prev)}
        className="flex w-full items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors"
      >
        {isOpen ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        )}
        <Link2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        What links here
        {status === "loaded" && items.length > 0 && (
          <span className="ml-1 font-normal normal-case tracking-normal text-muted-foreground/70">
            ({items.length})
          </span>
        )}
      </button>

      {isOpen && (
        <div
          id="wiki-what-links-here-body"
          className="mt-3"
        >
          {/* Loading state */}
          {status === "loading" && (
            <div role="status" aria-busy="true" aria-label="Loading backlinks" className="space-y-2">
              <Skeleton className="h-3 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
              <Skeleton className="h-3 w-2/3" />
            </div>
          )}

          {/* Error state */}
          {status === "error" && (
            <PaneError
              title="Could not load backlinks"
              description={errorMsg ?? "Try expanding this section again."}
              onRetry={handleRetry}
            />
          )}

          {/* Empty state */}
          {status === "loaded" && !hasAny && (
            <p className="text-xs text-muted-foreground">
              No other entities link to this one yet.
            </p>
          )}

          {/* Loaded state — grouped by via */}
          {status === "loaded" && hasAny && (
            <div className="space-y-4">
              {VIA_ORDER.filter((via) => groups[via].length > 0).map((via) => (
                <div key={via}>
                  <p className="mb-1.5 text-label-xs font-medium text-muted-foreground">
                    {VIA_LABEL[via]}
                  </p>
                  <ul className="space-y-1" aria-label={VIA_LABEL[via]}>
                    {groups[via].map((item) => (
                      <li key={item.slug}>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-auto w-full justify-start px-2 py-1 text-left text-xs"
                          onClick={() => onSelectEntity(item.slug)}
                          aria-label={`View entity: ${item.name}`}
                        >
                          <span className="truncate">{item.name}</span>
                          {item.entity_type && item.entity_type !== "OTHER" && (
                            <span className="ml-1.5 shrink-0 font-mono text-label-xxs uppercase opacity-50">
                              {item.entity_type.slice(0, 3)}
                            </span>
                          )}
                        </Button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
