// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react"
import { fetchStorageMetrics } from "@/lib/api"
import type { StorageMetrics } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { HardDrive, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

const SERVICE_COLORS = {
  chromadb: { bg: "bg-blue-500", label: "ChromaDB" },
  neo4j: { bg: "bg-emerald-500", label: "Neo4j" },
  redis: { bg: "bg-amber-500", label: "Redis" },
  bm25: { bg: "bg-slate-500", label: "BM25" },
} as const

function statusColor(status: string): string {
  if (status === "critical") return "text-red-500"
  if (status === "warning") return "text-yellow-500"
  return "text-emerald-500"
}

function statusBadgeVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "critical") return "destructive"
  if (status === "warning") return "outline"
  return "secondary"
}

export function StorageBar() {
  const [metrics, setMetrics] = useState<StorageMetrics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    try {
      const data = await fetchStorageMetrics()
      setMetrics(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load storage metrics")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, 60_000) // refresh every 60s
    return () => clearInterval(interval)
  }, [])

  if (loading) {
    return (
      <Card className="mb-4">
        <CardContent className="flex items-center justify-center py-6">
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          <span className="ml-2 text-sm text-muted-foreground">Loading storage metrics...</span>
        </CardContent>
      </Card>
    )
  }

  if (error || !metrics) {
    return null // silent fail — don't clutter UI if metrics unavailable
  }

  const { chromadb, neo4j, redis, bm25, total_mb, limit_mb, usage_pct, status } = metrics

  // Compute per-service widths as percentage of limit
  const chromaPct = limit_mb > 0 ? (chromadb.disk_mb / limit_mb) * 100 : 0
  const neo4jPct = limit_mb > 0 ? (neo4j.disk_mb / limit_mb) * 100 : 0
  const redisPct = limit_mb > 0 ? (redis.memory_mb / limit_mb) * 100 : 0
  const bm25Pct = limit_mb > 0 ? (bm25.disk_mb / limit_mb) * 100 : 0

  const segments = [
    { key: "chromadb" as const, pct: chromaPct, mb: chromadb.disk_mb, detail: `${chromadb.collections} collections, ${chromadb.chunks.toLocaleString()} chunks` },
    { key: "neo4j" as const, pct: neo4jPct, mb: neo4j.disk_mb, detail: `${neo4j.nodes.toLocaleString()} nodes, ${neo4j.relationships.toLocaleString()} rels` },
    { key: "redis" as const, pct: redisPct, mb: redis.memory_mb, detail: `${redis.keys.toLocaleString()} keys, peak ${redis.peak_mb} MB` },
    { key: "bm25" as const, pct: bm25Pct, mb: bm25.disk_mb, detail: `${bm25.index_count} indexes` },
  ]

  return (
    <Card className="mb-4">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <HardDrive className="h-4 w-4" />
            Storage Usage
          </CardTitle>
          <div className="flex items-center gap-2">
            <span className={cn("text-sm font-mono font-medium", statusColor(status))}>
              {total_mb.toFixed(1)} / {limit_mb} MB
            </span>
            <Badge variant={statusBadgeVariant(status)}>
              {usage_pct.toFixed(0)}%
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {/* Stacked bar */}
        <div className="relative h-3 w-full rounded-full bg-zinc-200 dark:bg-zinc-800 overflow-hidden">
          <div className="absolute inset-0 flex">
            {segments.map((seg) => (
              <Tooltip key={seg.key}>
                <TooltipTrigger asChild>
                  <div
                    className={cn(SERVICE_COLORS[seg.key].bg, "h-full transition-all duration-500")}
                    style={{ width: `${Math.max(seg.pct, seg.mb > 0 ? 0.5 : 0)}%` }}
                  />
                </TooltipTrigger>
                <TooltipContent side="bottom" className="text-xs">
                  <p className="font-medium">{SERVICE_COLORS[seg.key].label}: {seg.mb.toFixed(1)} MB</p>
                  <p className="text-muted-foreground">{seg.detail}</p>
                </TooltipContent>
              </Tooltip>
            ))}
          </div>
        </div>

        {/* Legend */}
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          {segments.map((seg) => (
            <div key={seg.key} className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <div className={cn("h-2 w-2 rounded-full", SERVICE_COLORS[seg.key].bg)} />
              <span>{SERVICE_COLORS[seg.key].label}</span>
              <span className="font-mono">{seg.mb.toFixed(1)} MB</span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
