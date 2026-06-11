// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react"
import { Alert } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/ui/empty-state"
import { Badge } from "@/components/ui/badge"
import { fetchWikiLog } from "@/lib/api/wiki"
import type { WikiLogEntry } from "@/lib/types/wiki"

// ---------------------------------------------------------------------------
// Page history — per-entity revision ledger from GET /wiki/log?entity_slug=
// Shows: action verb, relative timestamp, collapsed snapshot, source_artifact_id.
// ---------------------------------------------------------------------------

function formatRelativeTime(iso: string): string {
  try {
    const ms = Date.parse(iso)
    if (Number.isNaN(ms)) return iso
    const seconds = Math.floor((Date.now() - ms) / 1000)
    if (seconds < 60) return "just now"
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
    const days = Math.floor(seconds / 86400)
    if (days < 30) return `${days}d ago`
    return new Date(ms).toLocaleDateString()
  } catch {
    return iso
  }
}

const ACTION_LABELS: Record<string, string> = {
  refresh: "Refreshed",
  enrich: "Enriched",
  contradict: "Contradiction noted",
}

function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action.charAt(0).toUpperCase() + action.slice(1)
}

interface LogEntryRowProps {
  entry: WikiLogEntry
}

function LogEntryRow({ entry }: LogEntryRowProps) {
  const [expanded, setExpanded] = useState(false)
  const hasSummary = Boolean(entry.summary)

  return (
    <li className="border-b border-border/30 pb-2 last:border-0 last:pb-0">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-1 items-start gap-2">
          <Badge
            variant="outline"
            className="mt-0.5 shrink-0 font-mono text-label-xxs uppercase"
          >
            {actionLabel(entry.action)}
          </Badge>
          <div className="min-w-0 flex-1">
            <p className="text-xs text-muted-foreground">{formatRelativeTime(entry.ts)}</p>
            {entry.source_artifact_id && (
              <p className="mt-0.5 truncate font-mono text-label-xxs text-muted-foreground/70">
                src: {entry.source_artifact_id.slice(0, 12)}…
              </p>
            )}
          </div>
        </div>
        {hasSummary && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-label={expanded ? "Hide snapshot" : "Show snapshot"}
            className="shrink-0 text-muted-foreground hover:text-foreground"
          >
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            )}
          </button>
        )}
      </div>
      {hasSummary && expanded && (
        <div className="mt-2 rounded-md bg-muted/30 px-3 py-2">
          <p className="line-clamp-6 text-xs text-muted-foreground">{entry.summary}</p>
        </div>
      )}
    </li>
  )
}

export interface PageHistoryProps {
  entitySlug: string
}

export function PageHistory({ entitySlug }: PageHistoryProps) {
  const { data, isLoading, isError, refetch } = useQuery<WikiLogEntry[]>({
    queryKey: ["wiki-log", entitySlug],
    queryFn: () => fetchWikiLog({ entity_slug: entitySlug }),
    staleTime: 5 * 60_000,
    retry: 1,
  })

  if (isLoading) {
    return (
      <div role="status" aria-busy="true" aria-label="Loading page history" className="space-y-2">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-3/4" />
      </div>
    )
  }

  if (isError) {
    return (
      <Alert variant="destructive" className="py-2">
        <p className="text-xs">Failed to load page history.</p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void refetch()}
          className="mt-1 h-6 px-2 text-label-xs"
        >
          <RefreshCw className="mr-1 h-3 w-3" aria-hidden="true" />
          Retry
        </Button>
      </Alert>
    )
  }

  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={RefreshCw}
        title="No recorded changes yet"
        description="History begins with the next refresh."
      />
    )
  }

  return (
    <ul className="space-y-2" aria-label="Page history">
      {data.map((entry) => (
        <LogEntryRow key={entry.log_id} entry={entry} />
      ))}
    </ul>
  )
}
