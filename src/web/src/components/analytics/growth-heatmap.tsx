// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Knowledge growth heatmap — Phase L Day 2 + Tier A T4b four-state.

import { useMemo } from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Activity, Loader2 } from "lucide-react"
import { useIngestionByDay } from "@/hooks/use-analytics"
import { cn } from "@/lib/utils"

const WEEKS = 53
const DAYS = 7

interface GrowthHeatmapProps {
  windowDays?: number
  onCellClick?: (date: string, count: number) => void
}

export function GrowthHeatmap({ windowDays = 365, onCellClick }: GrowthHeatmapProps) {
  const { data, isLoading, isError, error, refetch } = useIngestionByDay(windowDays)

  const grid = useMemo(() => {
    if (!data) return { cells: [] as Array<{ date: string; intensity: number; count: number }> }
    const byDate = new Map<string, number>()
    for (const b of data.buckets) byDate.set(b.date, b.intensity)

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
    return { cells }
  }, [data])

  if (isLoading) {
    return (
      <Card className="p-6 flex items-center justify-center text-muted-foreground h-48" role="status">
        <Loader2 className="w-4 h-4 animate-spin mr-2" aria-hidden="true" />
        Loading ingest activity…
      </Card>
    )
  }

  if (isError) {
    return (
      <Card className="p-4 border-amber-500/30 bg-amber-500/5">
        <div className="text-sm text-amber-700 dark:text-amber-400" role="alert">
          {error instanceof Error ? error.message : "Failed to load"}
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="mt-2"
          onClick={() => void refetch()}
        >
          Retry
        </Button>
      </Card>
    )
  }

  return (
    <Card className="p-4" data-testid="growth-heatmap">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-base font-semibold flex items-center gap-2">
            <Activity className="w-4 h-4" aria-hidden="true" />
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
            const bucket = intensityBucket(cell.intensity)
            const interactive = cell.count > 0
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
                  interactive && "cursor-pointer hover:opacity-70",
                )}
                tabIndex={interactive ? 0 : undefined}
                role={interactive ? "button" : undefined}
                aria-label={
                  interactive
                    ? `${cell.date}: ${cell.count} artifact${cell.count !== 1 ? "s" : ""}`
                    : undefined
                }
                onClick={() => interactive && onCellClick?.(cell.date, cell.count)}
                onKeyDown={(e) => {
                  if (!interactive) return
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    onCellClick?.(cell.date, cell.count)
                  }
                }}
                data-testid={interactive ? `heatmap-cell-${cell.date}` : undefined}
              >
                <title>
                  {cell.date}: {cell.count} artifact{cell.count !== 1 && "s"}
                </title>
              </rect>
            )
          })}
        </svg>
      </div>

      <div className="flex items-center gap-1.5 pt-2 text-xs text-muted-foreground">
        <span>Less</span>
        {INTENSITY_BUCKETS.map((b, i) => (
          <span
            key={i}
            className="inline-block w-3 h-3 rounded-sm"
            style={{ backgroundColor: b.fill }} // drift-allowed: runtime chart-series color swatch
          />
        ))}
        <span>More</span>
      </div>
    </Card>
  )
}

const INTENSITY_BUCKETS = [
  { threshold: 0, fill: "var(--chart-heat-0)" },
  { threshold: 0.01, fill: "var(--chart-heat-1)" },
  { threshold: 0.25, fill: "var(--chart-heat-2)" },
  { threshold: 0.5, fill: "var(--chart-heat-3)" },
  { threshold: 0.75, fill: "var(--chart-heat-4)" },
]

function intensityBucket(intensity: number): (typeof INTENSITY_BUCKETS)[0] {
  for (let i = INTENSITY_BUCKETS.length - 1; i >= 0; i--) {
    if (intensity >= INTENSITY_BUCKETS[i].threshold) return INTENSITY_BUCKETS[i]
  }
  return INTENSITY_BUCKETS[0]
}
