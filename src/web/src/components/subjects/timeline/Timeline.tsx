// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Subjects → Timeline mode (Phase M Day 3).
//
// The chronological scrubber. Backend `GET /graph/timeline` returns
// time-bucketed mention + birth counts; the UI renders:
//   - a horizontal bar chart of activity per bucket
//   - a draggable scrub cursor
//   - a play button with 1×/5×/10× speed selector
//
// Clicking a bucket pins the cursor; the recharts-driven mini-line
// below shows "entities introduced" as a separate trend so the user
// can read mention volume + new-entity events independently.

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { AlertCircle, Clock, Loader2, Pause, Play, SkipBack } from "lucide-react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { cn } from "@/lib/utils"
import { fetchTimeline, type TimelineResponse } from "@/lib/api/graph"

const PERIODS = [
  { label: "7d", value: "7d" as const },
  { label: "30d", value: "30d" as const },
  { label: "90d", value: "90d" as const },
  { label: "1y", value: "365d" as const },
]

const SPEEDS = [1, 5, 10] as const

interface TimelineProps {
  focalEntity?: string | null
  onEntityPick?: (id: string) => void
}

export function Timeline({ focalEntity }: TimelineProps) {
  const [data, setData] = useState<TimelineResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [period, setPeriod] = useState<typeof PERIODS[number]["value"]>("30d")
  const [cursor, setCursor] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<typeof SPEEDS[number]>(1)
  const playRef = useRef<number | null>(null)

  // Fetch on period / entity change
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchTimeline({ entity: focalEntity, period })
      .then((d) => {
        if (!cancelled) {
          setData(d)
          setCursor(Math.max(0, d.buckets.length - 1))
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
  }, [focalEntity, period])

  // Playback loop — advances cursor at SPEED× real-time (one bucket
  // per 600ms / speed). Loops back to start at the end.
  useEffect(() => {
    if (!playing || !data || data.buckets.length === 0) return
    const interval = Math.max(60, Math.floor(600 / speed))
    const tick = () => {
      setCursor((c) => {
        if (c >= data.buckets.length - 1) {
          setPlaying(false)
          return c
        }
        return c + 1
      })
    }
    playRef.current = window.setInterval(tick, interval)
    return () => {
      if (playRef.current) {
        window.clearInterval(playRef.current)
        playRef.current = null
      }
    }
  }, [playing, speed, data])

  const handleScrub = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setCursor(parseInt(e.target.value, 10))
  }, [])

  const handleBarClick = useCallback((entry: unknown) => {
    if (entry && typeof entry === "object" && "index" in entry) {
      const idx = (entry as { index: number }).index
      if (Number.isInteger(idx)) {
        setCursor(idx)
        setPlaying(false)
      }
    }
  }, [])

  // Active slice — buckets up to and including the cursor.
  const slice = useMemo(() => {
    if (!data) return []
    return data.buckets.slice(0, cursor + 1)
  }, [data, cursor])

  const cursorBucket = data?.buckets[cursor]

  if (loading) {
    return (
      <Card className="p-6 flex items-center justify-center text-muted-foreground h-72">
        <Loader2 className="w-4 h-4 animate-spin mr-2" />
        Loading timeline…
      </Card>
    )
  }

  if (error) {
    return (
      <Card className="p-4 border-amber-500/30 bg-amber-500/5">
        <div className="text-sm text-amber-600 flex items-center gap-2" role="alert">
          <AlertCircle className="w-4 h-4" />
          {error}
        </div>
      </Card>
    )
  }

  if (!data || data.buckets.length === 0) {
    return (
      <Card className="p-6 text-center text-muted-foreground" data-testid="timeline-empty">
        <Clock className="w-8 h-8 mx-auto mb-2 opacity-50" />
        <p className="text-sm">No activity in the last {period}.</p>
        <p className="text-xs mt-1">Try a longer period or ingest some artifacts first.</p>
      </Card>
    )
  }

  return (
    <Card className="p-4 space-y-3" data-testid="timeline-mode">
      {/* Header */}
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold flex items-center gap-2">
          <Clock className="w-4 h-4" />
          Timeline
          {focalEntity && (
            <span className="text-xs text-muted-foreground font-normal">
              · focused on {focalEntity}
            </span>
          )}
        </h2>
        <div className="grow" />
        <div role="tablist" aria-label="Time period" className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              role="tab"
              aria-selected={p.value === period}
              onClick={() => setPeriod(p.value)}
              className={cn(
                "px-2 py-0.5 rounded text-xs transition-colors",
                p.value === period
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/40",
              )}
              data-testid={`timeline-period-${p.value}`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Activity bars */}
      <div className="h-32" data-testid="timeline-bars">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data.buckets.map((b, i) => ({ ...b, index: i }))}
            onClick={(state) => {
              const idx = (state as { activeTooltipIndex?: number })?.activeTooltipIndex
              if (typeof idx === "number") handleBarClick({ index: idx })
            }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
            <XAxis dataKey="date" tick={{ fontSize: 10 }} />
            <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
            <Tooltip
              labelFormatter={((d: string) => d) as never}
              formatter={((v: number) => [v, "mentions"]) as never}
            />
            <Bar dataKey="mention_count" radius={[2, 2, 0, 0]}>
              {data.buckets.map((_, i) => (
                <Cell
                  key={i}
                  fill={i <= cursor ? "#06b6d4" : "#1e293b"}
                  fillOpacity={i === cursor ? 1.0 : 0.7}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Scrub control */}
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setCursor(0)
            setPlaying(false)
          }}
          aria-label="Rewind"
          data-testid="timeline-rewind"
        >
          <SkipBack className="w-3.5 h-3.5" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setPlaying((p) => !p)}
          aria-label={playing ? "Pause" : "Play"}
          data-testid="timeline-play"
        >
          {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
        </Button>
        <div role="group" aria-label="Playback speed" className="flex gap-0.5">
          {SPEEDS.map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              className={cn(
                "px-1.5 py-0.5 rounded text-[10px] font-mono tabular-nums",
                s === speed
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/40",
              )}
              data-testid={`timeline-speed-${s}`}
            >
              {s}×
            </button>
          ))}
        </div>
        <input
          type="range"
          min={0}
          max={data.buckets.length - 1}
          value={cursor}
          onChange={handleScrub}
          className="flex-1 accent-cyan-500"
          aria-label="Time cursor"
          data-testid="timeline-scrubber"
        />
        <span className="text-xs font-mono tabular-nums text-muted-foreground w-24 text-right">
          {cursorBucket?.date ?? "—"}
        </span>
      </div>

      {/* Cumulative entity-birth trend (only when there's at least one) */}
      {data.total_entities_introduced > 0 && (
        <div className="h-20" data-testid="timeline-birth-trend">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={data.buckets.map((b, i) => ({
                ...b,
                index: i,
                cumulative: data.buckets
                  .slice(0, i + 1)
                  .reduce((acc, x) => acc + x.entities_introduced, 0),
              }))}
            >
              <Line
                type="monotone"
                dataKey="cumulative"
                stroke="#a78bfa"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
                name="entities introduced"
              />
              <YAxis tick={{ fontSize: 9 }} allowDecimals={false} />
              <Tooltip
                labelFormatter={(_l, payload) => {
                  const idx = payload?.[0]?.payload?.index
                  return data.buckets[idx]?.date ?? ""
                }}
                formatter={((v: number) => [v, "entities introduced"]) as never}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Stats summary */}
      <div className="text-xs text-muted-foreground flex items-center gap-3 pt-1">
        <span>{slice.reduce((a, b) => a + b.mention_count, 0)} mentions up to cursor</span>
        <span>·</span>
        <span>
          {slice.reduce((a, b) => a + b.entities_introduced, 0)} entities introduced
        </span>
        <span>·</span>
        <span>granularity: {data.granularity}</span>
      </div>
    </Card>
  )
}

export default Timeline
