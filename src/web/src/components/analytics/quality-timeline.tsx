// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Quality rolling timeline — Phase L Day 3.
//
// Pro-only. Multi-line chart showing 90-day rolling values of:
//   - NDCG@10            (retrieval quality)
//   - Faithfulness       (RAGAS — generation grounded in retrieval)
//   - Memory recall      (LongMemEval — long-term memory accuracy)
//   - Verification accuracy (NLI claim verification)
//
// Days without samples carry null so the line renders an honest gap.

import { useEffect, useMemo, useState } from "react"
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { Card } from "@/components/ui/card"
import { Activity, Loader2, Sparkles } from "lucide-react"
import {
  fetchQualityTimeline,
  type QualityTimelinePoint,
  type QualityTimelineResponse,
} from "@/lib/api/analytics"
import { cn } from "@/lib/utils"

const METRIC_META = {
  ndcg: { label: "NDCG@10", color: "var(--chart-1)" },
  faithfulness: { label: "Faithfulness", color: "var(--chart-2)" },
  memory_recall: { label: "Memory recall", color: "var(--chart-3)" },
  verification_accuracy: { label: "Verification", color: "var(--chart-4)" },
} as const

interface QualityTimelineProps {
  windowDays?: number
  tier?: string
}

export function QualityTimeline({ windowDays = 90, tier = "community" }: QualityTimelineProps) {
  const [data, setData] = useState<QualityTimelineResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const isPro = tier !== "community"

  useEffect(() => {
    if (!isPro) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    fetchQualityTimeline(windowDays)
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
  }, [windowDays, isPro])

  // Trim leading rows that are all-null so the chart starts from the
  // first real data point. Without this the X axis stretches across
  // months of nothingness for a fresh install.
  const trimmed = useMemo<QualityTimelinePoint[]>(() => {
    if (!data) return []
    let start = 0
    while (
      start < data.points.length &&
      data.points[start].ndcg == null &&
      data.points[start].faithfulness == null &&
      data.points[start].memory_recall == null &&
      data.points[start].verification_accuracy == null
    ) {
      start++
    }
    return data.points.slice(start)
  }, [data])

  if (!isPro) {
    return (
      <Card className="p-4 relative" data-testid="quality-timeline">
        <div className="opacity-30 pointer-events-none">
          <Header windowDays={windowDays} latest={{}} />
          <div className="h-64 flex items-center justify-center text-muted-foreground">
            Timeline preview
          </div>
        </div>
        <div className="absolute inset-0 flex items-center justify-center backdrop-blur-sm">
          <div className="text-center p-4">
            <Sparkles className="w-8 h-8 mx-auto text-amber-500 mb-2" />
            <h4 className="text-sm font-semibold">Quality timeline is Pro-tier</h4>
            <p className="text-xs text-muted-foreground mt-1 max-w-xs">
              Watch retrieval, faithfulness, memory, and verification accuracy roll over the last 90 days.
            </p>
          </div>
        </div>
      </Card>
    )
  }

  if (loading) {
    return (
      <Card className="p-6 flex items-center justify-center text-muted-foreground h-72">
        <Loader2 className="w-4 h-4 animate-spin mr-2" />
        Loading quality history…
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

  if (!data || trimmed.length === 0) {
    return (
      <Card className="p-4" data-testid="quality-timeline">
        <Header windowDays={windowDays} latest={{}} />
        <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">
          No quality metrics recorded in the last {windowDays} days.
        </div>
      </Card>
    )
  }

  return (
    <Card className="p-4" data-testid="quality-timeline">
      <Header windowDays={windowDays} latest={data.latest} />
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={trimmed}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10 }}
              tickFormatter={(d: string) => d.slice(5)}  // MM-DD
            />
            <YAxis
              domain={[0, 1]}
              tick={{ fontSize: 10 }}
              tickFormatter={(v: number) => v.toFixed(1)}
            />
            <Tooltip
              labelFormatter={((label: string) => label) as never}
              formatter={((value: number | null, name: string) => {
                if (value == null) return ["—", name]
                return [value.toFixed(3), name]
              }) as never}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {Object.entries(METRIC_META).map(([key, meta]) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                stroke={meta.color}
                strokeWidth={1.5}
                dot={false}
                connectNulls={false}
                name={meta.label}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  )
}

function Header({ windowDays, latest }: { windowDays: number; latest: Record<string, number | null> }) {
  return (
    <div className="flex items-start justify-between mb-3">
      <div>
        <h3 className="text-base font-semibold flex items-center gap-2">
          <Activity className="w-4 h-4" />
          Quality Timeline
        </h3>
        <p className="text-xs text-muted-foreground">
          {windowDays}-day rolling
        </p>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
        {Object.entries(METRIC_META).map(([key, meta]) => {
          const v = latest[key]
          return (
            <span key={key} className="flex items-center gap-1">
              <span
                className="inline-block w-2 h-2 rounded-full"
                style={{ backgroundColor: meta.color }} // drift-allowed: runtime chart-series color swatch
              />
              <span className={cn(
                "font-mono tabular-nums",
                v == null && "text-muted-foreground",
              )}>
                {v == null ? "—" : v.toFixed(2)}
              </span>
            </span>
          )
        })}
      </div>
    </div>
  )
}
