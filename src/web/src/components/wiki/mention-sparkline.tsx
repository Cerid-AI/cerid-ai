// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Wiki mention sparkline — Phase M Day 5.
//
// Reuses the `/graph/timeline` endpoint (Phase M Day 1-2) to render
// a compact per-day mention count chart inside the Wiki entity page.
// Lazy-loaded; collapsed by default to keep the wiki render light.

import { useCallback, useEffect, useState } from "react"
import { Activity, ChevronDown, ChevronRight, Loader2 } from "lucide-react"
import { Area, AreaChart, ResponsiveContainer, Tooltip } from "recharts"
import { Button } from "@/components/ui/button"
import { fetchTimeline, type TimelineResponse } from "@/lib/api/graph"
import { cn } from "@/lib/utils"

interface MentionSparklineProps {
  entitySlug: string
  entityName?: string
  /** Click handler — opens the entity in the Subjects → Timeline mode. */
  onOpenTimeline?: (slug: string) => void
}

export function MentionSparkline({
  entitySlug,
  entityName,
  onOpenTimeline,
}: MentionSparklineProps) {
  const [expanded, setExpanded] = useState(false)
  const [data, setData] = useState<TimelineResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Fetch only once when first expanded — cuts wiki-page bandwidth when
  // the user doesn't engage with the timeline.
  useEffect(() => {
    if (!expanded || data || loading) return
    setLoading(true)
    fetchTimeline({ entity: entitySlug, period: "90d" })
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false))
  }, [expanded, entitySlug, data, loading])

  const handleOpenTimeline = useCallback(() => {
    onOpenTimeline?.(entitySlug)
  }, [entitySlug, onOpenTimeline])

  return (
    <section aria-labelledby="wiki-sparkline-heading" data-testid="mention-sparkline">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors"
        aria-expanded={expanded}
        aria-controls="wiki-sparkline-body"
        id="wiki-sparkline-heading"
      >
        {expanded ? (
          <ChevronDown className="w-3 h-3" aria-hidden="true" />
        ) : (
          <ChevronRight className="w-3 h-3" aria-hidden="true" />
        )}
        <Activity className="w-3 h-3" aria-hidden="true" />
        Mention Trend
      </button>

      {expanded && (
        <div
          id="wiki-sparkline-body"
          className="mt-2 rounded-md border border-border bg-card/50 p-3"
        >
          {loading && (
            <div className="flex items-center justify-center py-6 text-muted-foreground text-xs">
              <Loader2 className="w-3 h-3 animate-spin mr-1.5" />
              Loading mentions…
            </div>
          )}
          {error && (
            <div className="text-xs text-amber-600" role="alert">{error}</div>
          )}
          {data && data.buckets.length === 0 && (
            <p className="text-xs text-muted-foreground py-2">
              No mentions in the last 90 days for {entityName ?? entitySlug}.
            </p>
          )}
          {data && data.buckets.length > 0 && (
            <>
              <div className="h-16">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={data.buckets}>
                    <Area
                      type="monotone"
                      dataKey="mention_count"
                      stroke="#06b6d4"
                      fill="#06b6d4"
                      fillOpacity={0.25}
                      strokeWidth={1.5}
                      dot={false}
                      isAnimationActive={false}
                    />
                    <Tooltip
                      formatter={((v: number) => [v, "mentions"]) as never}
                      labelFormatter={(d) => d}
                      contentStyle={{ fontSize: 11 }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <div className="flex items-center justify-between pt-1 text-xs text-muted-foreground">
                <span>{data.total_mentions} mentions in {data.buckets.length} {data.granularity}s</span>
                <Button
                  variant="link"
                  size="sm"
                  onClick={handleOpenTimeline}
                  data-testid="mention-sparkline-open-timeline"
                  className={cn("h-auto py-0 text-xs")}
                >
                  Open in Timeline →
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  )
}
