// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/empty-state"
import { Search } from "lucide-react"
import type { AuditQueries } from "@/lib/types"
import { CHART_TOOLTIP_STYLE } from "@/lib/constants"

interface QueryStatsProps {
  queries: AuditQueries | undefined
}

export function QueryStats({ queries }: QueryStatsProps) {
  if (!queries) return <EmptyState icon={Search} title="No query data" description="Query patterns appear after KB searches" />

  const data = Object.entries(queries.domain_frequency)
    .map(([domain, count]) => ({ domain, count }))
    .sort((a, b) => b.count - a.count)

  return (
    <Card>
      <CardHeader className="p-3 pb-0">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">Query Patterns</CardTitle>
          <span className="text-xs text-muted-foreground">
            {queries.total_queries} queries, avg {queries.avg_results_per_query.toFixed(1)} results
          </span>
        </div>
      </CardHeader>
      <CardContent className="p-3">
        {data.length > 0 ? (
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={data} layout="vertical">
              <XAxis type="number" tick={{ fontSize: 10 }} />
              <YAxis dataKey="domain" type="category" tick={{ fontSize: 11 }} width={70} />
              <Tooltip
                contentStyle={CHART_TOOLTIP_STYLE}
                formatter={(value) => [value ?? 0, "Queries"]}
              />
              <Bar dataKey="count" fill="hsl(var(--chart-2))" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-4 text-center text-xs text-muted-foreground">No query data</p>
        )}
      </CardContent>
    </Card>
  )
}