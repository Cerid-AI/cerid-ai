// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useMemo } from "react"
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { AuditVerification } from "@/lib/types"
import { CHART_TOOLTIP_STYLE } from "@/lib/constants"

interface ModelAccuracyChartProps {
  verification: AuditVerification | undefined
}

function accuracyBarFill(accuracy: number): string {
  if (accuracy >= 0.8) return "hsl(142, 71%, 45%)"  // green
  if (accuracy >= 0.5) return "hsl(48, 96%, 53%)"   // yellow
  return "hsl(0, 84%, 60%)"                          // red
}

export function ModelAccuracyChart({ verification }: ModelAccuracyChartProps) {
  const data = useMemo(() => {
    if (!verification?.by_model) return []
    return Object.entries(verification.by_model)
      .map(([model, stats]) => ({
        model: model.split("/").pop() ?? model, // Short name
        accuracy: Math.round(stats.accuracy * 100),
        checks: stats.checks,
        rawAccuracy: stats.accuracy,
      }))
      .sort((a, b) => b.accuracy - a.accuracy)
  }, [verification])

  if (data.length < 2) return null

  return (
    <Card>
      <CardHeader className="p-3 pb-0">
        <CardTitle className="text-sm">Model Accuracy Comparison</CardTitle>
      </CardHeader>
      <CardContent className="p-3">
        <ResponsiveContainer width="100%" height={Math.max(120, data.length * 40)}>
          <BarChart data={data} layout="vertical" margin={{ left: 0, right: 30 }}>
            <XAxis
              type="number"
              domain={[0, 100]}
              tick={{ fontSize: 10 }}
              tickFormatter={(v) => `${v}%`}
            />
            <YAxis
              type="category"
              dataKey="model"
              width={100}
              tick={{ fontSize: 10 }}
            />
            <Tooltip
              contentStyle={CHART_TOOLTIP_STYLE}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              formatter={(value: any, _name: any, entry: any) => [
                `${value}% (${entry?.payload?.checks ?? 0} checks)`,
                "Accuracy",
              ]}
            />
            <Bar dataKey="accuracy" radius={[0, 4, 4, 0]} barSize={20}>
              {data.map((entry, i) => (
                <Cell key={i} fill={accuracyBarFill(entry.rawAccuracy)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
