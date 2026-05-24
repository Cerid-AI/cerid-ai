// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Knowledge panel — Phase K6.2.
//
// Surfaces the six metrics from the redesign §9 so operators (and
// users) can see at a glance whether the knowledge architecture is
// compounding as intended:
//   1. Wiki coverage
//   2. Active-entity coverage
//   3. Unresolved contradictions
//   4. Knowledge log activity (24h)
//   5. Wiki page count
//   6. Stale entity count (derived)
//
// Reads from /health.wiki_freshness which K6.1 populated. Lightweight
// component — pure data display, no animation.

import { useEffect, useRef, useState } from "react"
import { Card } from "@/components/ui/card"
import { AlertTriangle, BookOpen, Clock, GitMerge, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

interface WikiFreshness {
  available: boolean
  total_entities?: number
  entities_with_summary?: number
  coverage_pct?: number
  active_entities?: number
  active_entities_with_summary?: number
  active_coverage_pct?: number
  unresolved_contradictions?: number
  log_activity_24h?: number
  reason?: string
}

interface HealthResponse {
  wiki_freshness?: WikiFreshness
}

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
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
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
        <div className="text-[10px] text-muted-foreground leading-tight">{hint}</div>
      )}
    </Card>
  )
}

export function KnowledgePanel() {
  const [data, setData] = useState<WikiFreshness | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    setLoading(true)
    fetch("/health", { credentials: "include" })
      .then((r) => r.json() as Promise<HealthResponse>)
      .then((body) => {
        if (cancelled) return
        if (body.wiki_freshness) {
          setData(body.wiki_freshness)
        } else {
          setError("Wiki freshness metrics not exposed yet")
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
  }, [])

  if (loading) {
    return (
      <Card className="p-6 flex items-center justify-center text-muted-foreground text-sm">
        <Loader2 className="h-4 w-4 animate-spin mr-2" />
        Loading knowledge metrics…
      </Card>
    )
  }

  if (error || !data || !data.available) {
    return (
      <Card className="p-4 border-amber-500/40 bg-amber-500/5" data-testid="knowledge-panel-degraded">
        <div className="flex items-center gap-2 text-amber-700 dark:text-amber-400 text-sm">
          <AlertTriangle className="w-4 h-4" />
          Knowledge metrics unavailable: {error || data?.reason || "unknown"}
        </div>
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
        <span className="ml-auto text-[10px] text-muted-foreground">
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
