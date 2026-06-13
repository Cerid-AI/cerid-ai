// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

interface TagFilterBarProps {
  /** All available tags (union across the listed entities), pre-ordered. */
  tags: string[]
  /** Currently-selected tags. */
  selected: ReadonlySet<string>
  /** Toggle a single tag's membership in the selection. */
  onToggle: (tag: string) => void
  /** Clear the whole selection. */
  onClear: () => void
}

/**
 * Slice 6.3 — tag filter affordance for the entity list. A row of toggle
 * chips (NOT section headers — sectioning stays taxonomy). Selecting tags
 * narrows the list to entities carrying any selected tag. Omit-if-absent:
 * renders nothing when there are no tags to filter by.
 */
export function TagFilterBar({ tags, selected, onToggle, onClear }: TagFilterBarProps) {
  if (tags.length === 0) return null

  return (
    <div
      className="flex flex-wrap items-center gap-1.5 px-3 py-2"
      role="group"
      aria-label="Filter entities by tag"
    >
      {tags.map((tag) => {
        const active = selected.has(tag)
        return (
          <button
            key={tag}
            type="button"
            aria-pressed={active}
            onClick={() => onToggle(tag)}
            className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Badge
              variant={active ? "default" : "outline"}
              className={cn(
                "cursor-pointer text-label-xxs font-normal transition-colors",
                !active && "text-muted-foreground hover:text-foreground",
              )}
            >
              {tag}
            </Badge>
          </button>
        )
      })}
      {selected.size > 0 && (
        <button
          type="button"
          onClick={onClear}
          aria-label="Clear tag filter"
          className="inline-flex items-center gap-0.5 rounded-full px-1.5 text-label-xxs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="h-3 w-3" aria-hidden="true" />
          Clear
        </button>
      )}
    </div>
  )
}
