// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react"
import { fetchIngestHistory } from "@/lib/api"
import type { IngestHistoryEntry } from "@/lib/types"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  Upload,
  FolderSearch,
  Webhook,
  Mail,
  Rss,
  Bookmark,
  Clipboard,
  ScanLine,
  CheckCircle2,
  XCircle,
  Minus,
  Loader2,
  RefreshCw,
  ChevronDown,
} from "lucide-react"

// ---------------------------------------------------------------------------
// Source type config
// ---------------------------------------------------------------------------

const SOURCE_CONFIG: Record<string, { icon: typeof Upload; color: string; label: string }> = {
  upload:    { icon: Upload,       color: "bg-blue-500/15 text-blue-600 dark:text-blue-400",   label: "Upload" },
  watcher:   { icon: FolderSearch, color: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400", label: "Watcher" },
  scanner:   { icon: ScanLine,     color: "bg-teal-500/15 text-teal-600 dark:text-teal-400",   label: "Scanner" },
  webhook:   { icon: Webhook,      color: "bg-amber-500/15 text-amber-600 dark:text-amber-400", label: "Webhook" },
  email:     { icon: Mail,         color: "bg-purple-500/15 text-purple-600 dark:text-purple-400", label: "Email" },
  rss:       { icon: Rss,          color: "bg-orange-500/15 text-orange-600 dark:text-orange-400", label: "RSS" },
  bookmark:  { icon: Bookmark,     color: "bg-pink-500/15 text-pink-600 dark:text-pink-400",   label: "Bookmark" },
  clipboard: { icon: Clipboard,    color: "bg-slate-500/15 text-slate-600 dark:text-slate-400", label: "Clipboard" },
}

const STATUS_CONFIG: Record<string, { icon: typeof CheckCircle2; color: string }> = {
  success: { icon: CheckCircle2, color: "text-emerald-500" },
  failed:  { icon: XCircle,      color: "text-red-500" },
  skipped: { icon: Minus,         color: "text-zinc-400" },
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(ts: string): string {
  if (!ts) return ""
  const diff = Date.now() - new Date(ts).getTime()
  const secs = Math.floor(diff / 1000)
  if (secs < 60) return "just now"
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

function truncateFilename(name: string, max = 32): string {
  if (name.length <= max) return name
  const ext = name.lastIndexOf(".")
  if (ext > 0 && name.length - ext <= 6) {
    const keep = max - (name.length - ext) - 3
    return name.slice(0, Math.max(keep, 5)) + "..." + name.slice(ext)
  }
  return name.slice(0, max - 3) + "..."
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface ActivityFeedProps {
  className?: string
  maxHeight?: string
}

export function ActivityFeed({ className, maxHeight = "400px" }: ActivityFeedProps) {
  const [items, setItems] = useState<IngestHistoryEntry[]>([])
  const [total, setTotal] = useState(0)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchIngestHistory(30)
      setItems(data.items)
      setTotal(data.total)
      setNextCursor(data.next_cursor)
    } catch {
      // silent — don't disrupt KB view
    } finally {
      setLoading(false)
    }
  }, [])

  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return
    setLoadingMore(true)
    try {
      const data = await fetchIngestHistory(30, nextCursor)
      setItems((prev) => [...prev, ...data.items])
      setNextCursor(data.next_cursor)
    } catch {
      // silent
    } finally {
      setLoadingMore(false)
    }
  }, [nextCursor, loadingMore])

  useEffect(() => {
    load()
    const interval = setInterval(load, 30_000) // refresh every 30s
    return () => clearInterval(interval)
  }, [load])

  if (loading && items.length === 0) {
    return (
      <div className={cn("flex items-center justify-center py-8", className)}>
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">Loading activity...</span>
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className={cn("py-6 text-center text-sm text-muted-foreground", className)}>
        No ingestion activity yet.
      </div>
    )
  }

  return (
    <div className={className}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-muted-foreground">
          Recent Activity ({total} total)
        </span>
        <Button variant="ghost" size="icon" className="h-6 w-6" onClick={load} disabled={loading}>
          <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
        </Button>
      </div>
      <ScrollArea style={{ maxHeight }}>
        <div className="space-y-1">
          {items.map((item) => {
            const source = SOURCE_CONFIG[item.source_type] ?? SOURCE_CONFIG.upload
            const statusCfg = STATUS_CONFIG[item.status] ?? STATUS_CONFIG.success
            const SourceIcon = source.icon
            const StatusIcon = statusCfg.icon

            return (
              <div
                key={item.id}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800/50 transition-colors"
              >
                <StatusIcon className={cn("h-3.5 w-3.5 shrink-0", statusCfg.color)} />
                <span className="min-w-0 flex-1 truncate text-xs" title={item.filename}>
                  {truncateFilename(item.filename)}
                </span>
                <Badge variant="outline" className={cn("shrink-0 text-[10px] px-1.5 py-0", source.color)}>
                  <SourceIcon className="h-2.5 w-2.5 mr-0.5" />
                  {source.label}
                </Badge>
                {item.chunks > 0 && (
                  <span className="shrink-0 text-[10px] font-mono text-muted-foreground">
                    {item.chunks}ch
                  </span>
                )}
                <span className="shrink-0 text-[10px] text-muted-foreground whitespace-nowrap">
                  {relativeTime(item.timestamp)}
                </span>
              </div>
            )
          })}
        </div>

        {nextCursor && (
          <div className="mt-2 flex justify-center">
            <Button
              variant="ghost"
              size="sm"
              className="text-xs h-7"
              onClick={loadMore}
              disabled={loadingMore}
            >
              {loadingMore ? (
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              ) : (
                <ChevronDown className="h-3 w-3 mr-1" />
              )}
              Load More
            </Button>
          </div>
        )}
      </ScrollArea>
    </div>
  )
}
