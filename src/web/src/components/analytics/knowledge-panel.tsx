// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Knowledge panel — Phase K6.2 + Tier A T4b four-state.

import { useEffect, useRef, useState } from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { AlertTriangle, BookOpen, Clock, GitMerge, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { useWikiFreshness } from "@/hooks/use-analytics"

interface MetricCardProps {
  label: string
  value: string | number
  hint?: string
  icon: React.ReactNode
  warn?: boolean
}

function MetricCard({ label, value, hint, icon, warn }: MetricCardProps) {
  const prevRef = useRef<string | number>(value)
  const [pulsing, setPulsing] = useState(false)

  useEffect(() => {
    if (prevRef.current === value) return
    prevRef.current = value
    setPulsing(true)
    const t = window.setTimeout(() => setPulsing(false), 900)
    return () => window.clearTimeout(t)
  }, [value])

  return (
    <Card
      className={cn(
        "p-3 flex flex-col gap-1 transition-shadow",
        warn && "border-amber-500/40 bg-amber-500/5",
        pulsing && "metric-pulse",
      )}
      data-testid={`knowledge-metric-${label.toLowerCase().replace(/\s+/g, "-")}`}
    >
      <div className="flex items-center gap-1.5 text-label-xxs uppercase tracking-wider text-muted-foreground">
        {icon}
        {label}
      </div>
      <div
        className={cn(
          "text-2xl font-mono tabular-nums",
          pulsing && "metric-value-pulse",
        )}
      >
        {value}
      </div>
      {hint && (
        <div className="text-label-xxs text-muted-foreground leading-tight">{hint}</div>
      )}
    </Card>
  )
}

export function KnowledgePanel() {
  const { data, isLoading, isError, error, refetch } = useWikiFreshness()

  if (isLoading) {
    return (
      <Card className="p-6 flex items-center justify-center text-muted-foreground text-sm" role="status">
        <Loader2 className="h-4 w-4 animate-spin mr-2" aria-hidden="true" />
        Loading knowledge metrics…
      </Card>
    )
  }

  if (isError) {
    return (
      <Card className="p-4 border-amber-500/40 bg-amber-500/5" data-testid="knowledge-panel-degraded">
        <div className="flex items-center gap-2 text-amber-700 dark:text-amber-400 text-sm" role="alert">
          <AlertTriangle className="w-4 h-4" aria-hidden="true" />
          Knowledge metrics unavailable:{" "}
          {error instanceof Error ? error.message : "unknown"}
        </div>
        <Button type="button" variant="outline" size="sm" className="mt-2" onClick={() => void refetch()}>
          Retry
        </Button>
      </Card>
    )
  }

  if (!data || !data.available) {
    return (
      <Card className="p-4 border-amber-500/40 bg-amber-500/5" data-testid="knowledge-panel-degraded">
        <div className="flex items-center gap-2 text-amber-700 dark:text-amber-400 text-sm" role="alert">
          <AlertTriangle className="w-4 h-4" aria-hidden="true" />
          Knowledge metrics unavailable: {data?.reason || "unknown"}
        </div>
        <Button type="button" variant="outline" size="sm" className="mt-2" onClick={() => void refetch()}>
          Retry
        </Button>
      </Card>
    )
  }

  const totalEntities = data.total_entities ?? 0
  const withSummary = data.entities_with_summary ?? 0
  const coveragePct = data.coverage_pct ?? 0
  const activeCovPct = data.active_coverage_pct ?? 0
  const unresolved = data.unresolved_contradictions ?? 0
  const log24h = data.log_activity_24h ?? 0
  const stale = (data.active_entities ?? 0) - (data.active_entities_with_summary ?? 0)

  return (
    <section
      aria-labelledby="knowledge-panel-heading"
      className="space-y-2"
      data-testid="knowledge-panel"
    >
      <header className="flex items-center gap-2">
        <BookOpen className="w-4 h-4" aria-hidden="true" />
        <h3
          id="knowledge-panel-heading"
          className="text-xs font-semibold uppercase tracking-wider"
        >
          Knowledge architecture
        </h3>
        <span className="ml-auto text-label-xxs text-muted-foreground">
          Phase K6 metrics
        </span>
      </header>
      <div className="grid gap-2 md:grid-cols-3">
        <MetricCard
          label="Active coverage"
          value={`${activeCovPct}%`}
          hint={`${data.active_entities_with_summary ?? 0} of ${data.active_entities ?? 0} active entities`}
          icon={<BookOpen className="w-3 h-3" />}
          warn={activeCovPct < 80}
        />
        <MetricCard
          label="Total coverage"
          value={`${coveragePct}%`}
          hint={`${withSummary} of ${totalEntities} all entities`}
          icon={<BookOpen className="w-3 h-3" />}
        />
        <MetricCard
          label="Wiki log activity"
          value={log24h}
          hint="refreshes + enrichments in 24h"
          icon={<Clock className="w-3 h-3" />}
        />
        <MetricCard
          label="Unresolved contradictions"
          value={unresolved}
          hint="entities with stale summary"
          icon={<GitMerge className="w-3 h-3" />}
          warn={unresolved > 0}
        />
        <MetricCard
          label="Stale active entities"
          value={Math.max(0, stale)}
          hint="active without a summary"
          icon={<AlertTriangle className="w-3 h-3" />}
          warn={stale > 5}
        />
        <MetricCard
          label="Wiki pages"
          value={withSummary}
          hint="entities with a compiled summary"
          icon={<BookOpen className="w-3 h-3" />}
        />
      </div>
    </section>
  )
}
