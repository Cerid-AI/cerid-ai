// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { cn } from "@/lib/utils"
import type { EntitySummary } from "@/lib/types/wiki"

interface EntityListItemProps {
  entity: EntitySummary
  selected: boolean
  onSelect: (slug: string) => void
}

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

export function EntityListItem({ entity, selected, onSelect }: EntityListItemProps) {
  const relativeTime = formatLastUpdated(entity.last_updated_at)

  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={`${entity.name}${selected ? " (selected)" : ""}`}
      onClick={() => onSelect(entity.slug)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          onSelect(entity.slug)
        }
      }}
      className={cn(
        "w-full rounded-md border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected
          ? "border-brand bg-brand/5 glow-teal"
          : "border-transparent hover:border-border hover:bg-muted/60",
      )}
    >
      {/* Entity name */}
      <p
        className={cn(
          "text-sm font-medium leading-tight",
          selected ? "text-brand" : "text-foreground",
        )}
      >
        {entity.name}
      </p>

      {/* Summary preview — 2-line clamp */}
      {entity.summary_preview && (
        <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
          {entity.summary_preview}
        </p>
      )}

      {/* Meta row */}
      <div className="mt-1.5 flex items-center gap-2 text-label-xs text-muted-foreground">
        {entity.mention_count > 0 && (
          <span>{entity.mention_count} mentions</span>
        )}
        {relativeTime && <span>Updated {relativeTime}</span>}
      </div>

      {/* Top tags (Slice 6.3) — salience-ranked vocabulary chips; display only,
          omit-if-absent. The filter bar above the list is the interactive control. */}
      {entity.top_tags && entity.top_tags.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {entity.top_tags.map((tag) => (
            <span
              key={tag}
              className="rounded bg-muted px-1.5 py-0.5 text-label-xxs text-muted-foreground"
            >
              {tag}
            </span>
          ))}
        </div>
      )}
    </button>
  )
}
