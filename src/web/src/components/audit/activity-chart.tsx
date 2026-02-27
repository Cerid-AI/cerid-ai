// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/empty-state"
import { Activity } from "lucide-react"
import type { AuditActivity } from "@/lib/types"

interface ActivityChartProps {
  activity: AuditActivity | undefined
}

export function ActivityChart({ activity }: ActivityChartProps) {
  if (!activity) return <EmptyState icon={Activity} title="No activity data" description="Activity appears as events are logged" />

  const data = Object.entries(activity.hourly_timeline)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([hour, count]) => ({
      hour: hour.slice(-5), // "HH:MM" or last 5 chars of key
      count,
    }))

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
            <span key={event}>
              <span className="font-medium">{count}</span> {event}
            </span>
          ))}
        </div>
      </CardHeader>
      <CardContent className="p-3">
        {data.length > 0 ? (
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={data}>
              <XAxis dataKey="hour" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip
                contentStyle={{ fontSize: 12, borderRadius: 8 }}
                formatter={(value) => [value ?? 0, "Events"]}
              />
              <Area
                type="monotone"
                dataKey="count"
                stroke="hsl(var(--chart-1))"
                fill="hsl(var(--chart-1))"
                fillOpacity={0.2}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-4 text-center text-xs text-muted-foreground">No activity data</p>
        )}
      </CardContent>
    </Card>
  )
}