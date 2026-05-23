// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Knowledge growth heatmap — Phase L Day 2.
//
// GitHub-style commit grid showing artifact ingest activity over the
// last 365 days. Cells colored by intensity (count / peak_count) using
// the brand's amber/teal scale. Click a cell to deep-link to Sources →
// Activity with a date filter.

import { useEffect, useMemo, useState } from "react"
import { Card } from "@/components/ui/card"
import { Activity, Loader2 } from "lucide-react"
import { fetchIngestionByDay, type IngestionByDayResponse } from "@/lib/api/analytics"
import { cn } from "@/lib/utils"

const WEEKS = 53
const DAYS = 7

interface GrowthHeatmapProps {
  windowDays?: number
  onCellClick?: (date: string, count: number) => void
}

export function GrowthHeatmap({ windowDays = 365, onCellClick }: GrowthHeatmapProps) {
  const [data, setData] = useState<IngestionByDayResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchIngestionByDay(windowDays)
      .then((d) => {
        if (!cancelled) {
          setData(d)
          setError(null)
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load")
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [windowDays])

  // Build a grid of (week, day) cells indexed by date. Cells without an
  // ingest event get intensity 0.
  const grid = useMemo(() => {
    if (!data) return { cells: [], byDate: new Map<string, number>() }
    const byDate = new Map<string, number>()
    for (const b of data.buckets) byDate.set(b.date, b.intensity)

    // Last cell = today (UTC). Work backwards.
    const today = new Date()
    const cells: Array<{ date: string; intensity: number; count: number }> = []
    for (let i = 0; i < WEEKS * DAYS; i++) {
      const offset = WEEKS * DAYS - 1 - i
      const d = new Date(today.getTime() - offset * 86400 * 1000)
      const iso = d.toISOString().slice(0, 10)
      const intensity = byDate.get(iso) ?? 0
      const bucket = data.buckets.find((b) => b.date === iso)
      cells.push({ date: iso, intensity, count: bucket?.count ?? 0 })
    }
    return { cells, byDate }
  }, [data])

  if (loading) {
    return (
      <Card className="p-6 flex items-center justify-center text-muted-foreground h-48">
        <Loader2 className="w-4 h-4 animate-spin mr-2" />
        Loading ingest activity…
      </Card>
    )
  }

  if (error) {
    return (
      <Card className="p-4 border-amber-500/30 bg-amber-500/5">
        <div className="text-sm text-amber-600" role="alert">{error}</div>
      </Card>
    )
  }

  return (
    <Card className="p-4" data-testid="growth-heatmap">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-base font-semibold flex items-center gap-2">
            <Activity className="w-4 h-4" />
            Knowledge Growth
          </h3>
          <p className="text-xs text-muted-foreground">
            {data?.total ?? 0} artifacts ingested · peak day: {data?.peak_count ?? 0}
          </p>
        </div>
      </div>

      <div className="overflow-x-auto">
        <svg
          width={WEEKS * 12 + 24}
          height={DAYS * 12 + 8}
          className="block"
          role="img"
          aria-label="Knowledge growth heatmap"
        >
          {grid.cells.map((cell, i) => {
            const week = Math.floor(i / DAYS)
            const day = i % DAYS
            // Color: linear interpolation between muted and the
            // brand's teal/amber gradient. Five buckets feels closest
            // to the GitHub commit grid.
            const bucket = intensityBucket(cell.intensity)
            return (
              <rect
                key={i}
                x={week * 12 + 24}
                y={day * 12}
                width={10}
                height={10}
                rx={2}
                fill={bucket.fill}
                className={cn(
                  "transition-opacity",
                  cell.count > 0 && "cursor-pointer hover:opacity-70",
                )}
                onClick={() => cell.count > 0 && onCellClick?.(cell.date, cell.count)}
                data-testid={cell.count > 0 ? `heatmap-cell-${cell.date}` : undefined}
              >
                <title>
                  {cell.date}: {cell.count} artifact{cell.count !== 1 && "s"}
                </title>
              </rect>
            )
          })}
        </svg>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-1.5 pt-2 text-xs text-muted-foreground">
        <span>Less</span>
        {INTENSITY_BUCKETS.map((b, i) => (
          <span
            key={i}
            className="inline-block w-3 h-3 rounded-sm"
            style={{ backgroundColor: b.fill }}
          />
        ))}
        <span>More</span>
      </div>
    </Card>
  )
}

const INTENSITY_BUCKETS = [
  { threshold: 0, fill: "#1e293b" },   // slate-800 (cells with no data)
  { threshold: 0.01, fill: "#0e7490" }, // cyan-700 (very low)
  { threshold: 0.25, fill: "#0891b2" }, // cyan-600
  { threshold: 0.5, fill: "#06b6d4" },  // cyan-500
  { threshold: 0.75, fill: "#22d3ee" }, // cyan-400 (high)
]

function intensityBucket(intensity: number): typeof INTENSITY_BUCKETS[0] {
  // Walk thresholds in reverse — first one we exceed wins.
  for (let i = INTENSITY_BUCKETS.length - 1; i >= 0; i--) {
    if (intensity >= INTENSITY_BUCKETS[i].threshold) return INTENSITY_BUCKETS[i]
  }
  return INTENSITY_BUCKETS[0]
}
