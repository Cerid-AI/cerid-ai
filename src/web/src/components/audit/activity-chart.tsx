// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useMemo } from "react"
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/empty-state"
import { Activity } from "lucide-react"
import type { AuditActivity } from "@/lib/types"
import { CHART_TOOLTIP_STYLE } from "@/lib/constants"

/** Color palette for event types (stacked areas). */
const EVENT_COLORS: Record<string, string> = {
  ingest: "hsl(var(--chart-1))",
  query: "hsl(var(--chart-2))",
  duplicate: "hsl(var(--chart-3))",
  recategorize: "hsl(var(--chart-4))",
  memory_extraction: "hsl(var(--chart-5))",
}
const FALLBACK_COLOR = "hsl(var(--muted-foreground))"
const ERROR_COLOR = "hsl(var(--destructive))"

function eventColor(event: string): string {
  if (event.includes("error")) return ERROR_COLOR
  return EVENT_COLORS[event] ?? FALLBACK_COLOR
}

function formatHourLabel(isoHour: string, windowHours: number): string {
  try {
    const date = new Date(isoHour + ":00:00Z")
    if (isNaN(date.getTime())) return isoHour
    if (windowHours <= 24) {
      return date.toLocaleTimeString(undefined, { hour: "numeric", hour12: true })
    }
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "numeric", hour12: true })
  } catch {
    return isoHour
  }
}

interface ActivityChartProps {
  activity: AuditActivity | undefined
}

export function ActivityChart({ activity }: ActivityChartProps) {
  const hasTypedData = !!activity?.hourly_by_type && Object.keys(activity.hourly_by_type).length > 0

  const { data, eventTypes } = useMemo(() => {
    if (!activity) return { data: [], eventTypes: [] }
    const types = new Set<string>()
    if (hasTypedData) {
      Object.values(activity.hourly_by_type!).forEach((evts) =>
        Object.keys(evts).forEach((e) => types.add(e)),
      )
    }
    const typeList = Array.from(types).sort()

    const chartData = Object.entries(activity.hourly_timeline)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([hour, count]) => {
        const label = formatHourLabel(hour, activity.time_window_hours)
        const byType = activity.hourly_by_type?.[hour] ?? {}
        return {
          hour: label,
          count,
          ...Object.fromEntries(typeList.map((t) => [t, byType[t] ?? 0])),
        }
      })

    return { data: chartData, eventTypes: typeList }
  }, [activity, hasTypedData])

  if (!activity) return <EmptyState icon={Activity} title="No activity data" description="Activity appears as events are logged" />

  return (
    <Card>
      <CardHeader className="p-3 pb-0">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">Activity Timeline</CardTitle>
          <span className="text-xs text-muted-foreground">
            {activity.total_events.toLocaleString()} events in {activity.time_window_hours}h
          </span>
        </div>
        {/* Summary badges */}
        <div className="flex flex-wrap gap-3 pt-1 text-xs text-muted-foreground">
          {Object.entries(activity.event_breakdown).map(([event, count]) => (
            <span key={event} className="flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: eventColor(event) }} />
              <span className="font-medium">{count}</span> {event}
            </span>
          ))}
        </div>
      </CardHeader>
      <CardContent className="p-3">
        {data.length > 0 ? (
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={data}>
              <XAxis dataKey="hour" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
              <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
              {hasTypedData ? (
                eventTypes.map((evt) => (
                  <Area
                    key={evt}
                    type="monotone"
                    dataKey={evt}
                    stackId="events"
                    stroke={eventColor(evt)}
                    fill={eventColor(evt)}
                    fillOpacity={0.3}
                  />
                ))
              ) : (
                <Area
                  type="monotone"
                  dataKey="count"
                  stroke="hsl(var(--chart-1))"
                  fill="hsl(var(--chart-1))"
                  fillOpacity={0.2}
                />
              )}
              {hasTypedData && <Legend wrapperStyle={{ fontSize: 10 }} />}
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-4 text-center text-xs text-muted-foreground">No activity data</p>
        )}
      </CardContent>
    </Card>
  )
}
