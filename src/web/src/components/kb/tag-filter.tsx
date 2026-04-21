// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useRef, useEffect, useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tag, X, Sparkles, Hash } from "lucide-react"
import { fetchTagSuggestions } from "@/lib/api"
import type { TagSuggestion } from "@/lib/types"
import { cn } from "@/lib/utils"

interface TagFilterProps {
  activeTags: string[]
  onToggleTag: (tag: string) => void
  domain?: string | null
}

export function TagFilter({ activeTags, onToggleTag, domain }: TagFilterProps) {
  const [open, setOpen] = useState(false)
  const [prefix, setPrefix] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  const { data: suggestions = [] } = useQuery<TagSuggestion[]>({
    queryKey: ["tag-suggestions", domain ?? "all", prefix],
    queryFn: () => fetchTagSuggestions(domain ?? undefined, prefix, 20),
    staleTime: 60_000,
    enabled: open,
  })

  // Close dropdown on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node) &&
        inputRef.current &&
        !inputRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  const handleSelect = useCallback(
    (tag: string) => {
      onToggleTag(tag)
      setPrefix("")
    },
    [onToggleTag],
  )

  const filteredSuggestions = suggestions.filter(
    (s) => !activeTags.includes(s.name),
  )

  return (
    <div className="space-y-1">
      {/* Active tag pills */}
      {activeTags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {activeTags.map((tag) => (
            <Badge
              key={tag}
              variant="default"
              className="gap-1 py-0 text-[10px]"
            >
              {tag}
              <button
                className="ml-0.5 rounded-full hover:bg-primary-foreground/20"
                onClick={() => onToggleTag(tag)}
                aria-label={`Remove ${tag} filter`}
              >
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          ))}
          <Button
            variant="ghost"
            size="sm"
            className="h-5 text-[10px]"
            onClick={() => activeTags.forEach(onToggleTag)}
          >
            Clear
          </Button>
        </div>
      )}

      {/* Typeahead input */}
      <div className="relative">
        <div className="flex items-center gap-1">
          <Tag className="h-3 w-3 shrink-0 text-muted-foreground" />
          <Input
            ref={inputRef}
            placeholder="Filter by tag..."
            value={prefix}
            onChange={(e) => {
              setPrefix(e.target.value)
              if (!open) setOpen(true)
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setOpen(false)
                setPrefix("")
              }
              if (e.key === "Enter" && filteredSuggestions.length > 0) {
                handleSelect(filteredSuggestions[0].name)
              }
            }}
            className="h-6 flex-1 text-[11px]"
            aria-label="Filter by tag"
          />
        </div>

        {/* Dropdown */}
        {open && filteredSuggestions.length > 0 && (
          <div
            ref={dropdownRef}
            className="absolute left-0 right-0 top-full z-50 mt-1 rounded-md border bg-popover shadow-md"
          >
            <ScrollArea className="max-h-[200px]">
              <div className="p-1">
                {filteredSuggestions.map((suggestion) => (
                  <button
                    key={suggestion.name}
                    className={cn(
                      "flex w-full items-center gap-2 rounded px-2 py-1 text-left text-[11px] transition-colors",
                      "hover:bg-muted/50",
                    )}
                    onClick={() => handleSelect(suggestion.name)}
                  >
                    {suggestion.source === "vocabulary" ? (
                      <Sparkles className="h-3 w-3 shrink-0 text-amber-500" />
                    ) : (
                      <Hash className="h-3 w-3 shrink-0 text-muted-foreground" />
                    )}
                    <span className="flex-1 truncate">{suggestion.name}</span>
                    {suggestion.usage_count > 0 && (
                      <span className="text-[9px] text-muted-foreground">
                        {suggestion.usage_count}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </ScrollArea>
          </div>
        )}
      </div>
    </div>
  )
}
