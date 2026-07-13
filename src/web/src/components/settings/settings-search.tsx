// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Settings search — pinned sidebar input + flat cross-category result list.
 *
 * Matching is the registry's weighted token-AND substring search (no fuzzy).
 * Locked Pro/Enterprise settings appear in results (rendered locked —
 * discoverability); env-only read-only rows appear too; `visibleWhen`-hidden
 * defs never do. Clicking a result navigates to the owning category,
 * force-opens the containing Advanced expander, and scroll-targets the row.
 */

import { Fragment, type ReactNode, type RefObject } from "react"
import { Search, SearchX } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/ui/empty-state"
import { Input } from "@/components/ui/input"
import { useEntitlements } from "@/hooks/use-entitlements"
import { categoryLabel, type SearchMatch, type SettingDef } from "@/lib/settings-registry"
import { cn } from "@/lib/utils"
import { FOCUS_RING, TierLockBadge } from "./settings-primitives"

export function SettingsSearchInput({
  value,
  onChange,
  inputRef,
  placeholder = "Search settings…",
  ariaLabel = "Search settings",
  className,
}: {
  value: string
  onChange: (value: string) => void
  inputRef?: RefObject<HTMLInputElement | null>
  /** Override for elevated placements (e.g. the Overview page). */
  placeholder?: string
  /** Distinct accessible name when a second instance is mounted. */
  ariaLabel?: string
  className?: string
}) {
  return (
    <div className="relative">
      <Search
        className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
      <Input
        ref={inputRef}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
        className={cn("h-8 pl-8 text-sm", className)}
      />
    </div>
  )
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/** Wrap query-token matches in <mark>. */
// eslint-disable-next-line react-refresh/only-export-components -- co-located highlight helper used by the result list
export function highlightTokens(text: string, query: string): ReactNode {
  const tokens = query.toLowerCase().trim().split(/\s+/).filter(Boolean)
  if (tokens.length === 0) return text
  const re = new RegExp(`(${tokens.map(escapeRegExp).join("|")})`, "ig")
  const parts = text.split(re)
  if (parts.length === 1) return text
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="rounded-sm bg-primary/15 text-foreground">
        {part}
      </mark>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  )
}

export function SettingsSearchResults({
  query,
  matches,
  onSelect,
  onClear,
}: {
  query: string
  matches: SearchMatch[]
  onSelect: (def: SettingDef) => void
  onClear: () => void
}) {
  const { forDef } = useEntitlements()

  if (matches.length === 0) {
    return (
      <div className="density-stack">
        <EmptyState
          icon={SearchX}
          title={`No settings match "${query}"`}
          description="Try a different term — old tab names like Essentials or Pipeline also work."
        />
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={onClear}>
            Clear search
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="density-stack">
      <p className="text-label-sm text-muted-foreground">
        {matches.length} result{matches.length === 1 ? "" : "s"} for “{query}”
      </p>
      <ul aria-label="Search results" className="density-stack list-none p-0">
      {matches.map(({ def }) => {
        const entitlement = forDef(def)
        const locked = entitlement.state === "locked"
        return (
          <li key={def.id}>
          <button
            type="button"
            onClick={() => onSelect(def)}
            className={cn("w-full rounded-md border bg-card px-3 py-2 text-left transition-colors hover:border-primary/40 hover:bg-muted/60", FOCUS_RING)}
          >
            <p className="text-label-xs text-muted-foreground">
              {categoryLabel(def.category)} › {def.group}
            </p>
            <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
              {highlightTokens(def.label, query)}
              {locked && <TierLockBadge requiredTier={entitlement.requiredTier} />}
              {def.writer.kind === "env" && (
                <Badge variant="outline" className="font-mono text-label-xs">
                  {def.writer.envVar}
                </Badge>
              )}
            </p>
            <p className="text-label-sm text-muted-foreground">
              {highlightTokens(def.helpText, query)}
            </p>
          </button>
          </li>
        )
      })}
      </ul>
    </div>
  )
}
