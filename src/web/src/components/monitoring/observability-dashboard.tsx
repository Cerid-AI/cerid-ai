// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { InfoTip } from "@/components/ui/info-tip"
import { cn } from "@/lib/utils"
import {
  Activity,
  Clock,
  DollarSign,
  Gauge,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  Zap,
  Database,
  Shield,
  Cpu,
} from "lucide-react"
import {
  fetchObservabilityMetrics,
  fetchObservabilityHealthScore,
  fetchHealthStatus,
} from "@/lib/api"
import type { MetricAggregation, PipelineStage } from "@/lib/types"

// ---------------------------------------------------------------------------
// Time window options
// ---------------------------------------------------------------------------

const WINDOWS = [
  { label: "1h", minutes: 60 },
  { label: "6h", minutes: 360 },
  { label: "24h", minutes: 1440 },
  { label: "7d", minutes: 10080 },
] as const

// ---------------------------------------------------------------------------
// Sparkline — tiny inline SVG trend line
// ---------------------------------------------------------------------------

function Sparkline({
  points,
  width = 80,
  height = 24,
  className,
}: {
  points: number[]
  width?: number
  height?: number
  className?: string
}) {
  if (points.length < 2) return null

  const min = Math.min(...points)
  const max = Math.max(...points)
  const range = max - min || 1

  const pathData = points
    .map((v, i) => {
      const x = (i / (points.length - 1)) * width
      const y = height - ((v - min) / range) * (height - 2) - 1
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(" ")

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("inline-block", className)}
      aria-hidden="true"
    >
      <path d={pathData} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Trend indicator
// ---------------------------------------------------------------------------

function TrendIndicator({ current, previous }: { current: number | null; previous: number | null }) {
  if (current === null || previous === null || previous === 0) return null
  const pctChange = ((current - previous) / Math.abs(previous)) * 100
  if (Math.abs(pctChange) < 1) return null

  const isUp = pctChange > 0
  const Icon = isUp ? TrendingUp : TrendingDown

  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 text-[10px] font-medium",
        isUp ? "text-red-500" : "text-green-500",
      )}
      title={`${isUp ? "+" : ""}${pctChange.toFixed(1)}% vs previous period`}
    >
      <Icon className="h-3 w-3" />
      {Math.abs(pctChange).toFixed(0)}%
    </span>
  )
}

// ---------------------------------------------------------------------------
// Metric card
// ---------------------------------------------------------------------------

interface MetricCardProps {
  title: string
  icon: React.ElementType
  value: string
  subtitle?: string
  sparklineData?: number[]
  trend?: { current: number | null; previous: number | null }
  accentColor?: string
  /** Glossary key for an InfoTip next to the title. See lib/glossary.ts. */
  infoTerm?: string
}

function MetricCard({ title, icon: Icon, value, subtitle, sparklineData, trend, accentColor = "text-teal-500", infoTerm }: MetricCardProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 p-3 pb-1">
        <CardTitle className="text-xs font-medium text-muted-foreground">
          {title}
          {infoTerm && <> <InfoTip term={infoTerm} /></>}
        </CardTitle>
        <Icon className={cn("h-4 w-4", accentColor)} />
      </CardHeader>
      <CardContent className="p-3 pt-0">
        <div className="flex items-end justify-between gap-2">
          <div className="min-w-0">
            <div className="text-xl font-bold tabular-nums leading-tight">{value}</div>
            {subtitle && (
              <p className="mt-0.5 text-[10px] text-muted-foreground">{subtitle}</p>
            )}
            {trend && (
              <TrendIndicator current={trend.current} previous={trend.previous} />
            )}
          </div>
          {sparklineData && sparklineData.length >= 2 && (
            <Sparkline points={sparklineData} className={accentColor} />
          )}
        </div>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Grade badge
// ---------------------------------------------------------------------------

function GradeBadge({ grade, score, noData }: { grade: string; score: number; noData?: boolean }) {
  const gradeColors: Record<string, string> = {
    A: "bg-green-500/15 text-green-700 dark:text-green-400",
    B: "bg-teal-500/15 text-teal-700 dark:text-teal-400",
    C: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
    D: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
    F: "bg-red-500/15 text-red-700 dark:text-red-400",
  }

  if (noData) {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-xs font-semibold text-muted-foreground"
        title="Insufficient data — send some queries to build a health baseline"
      >
        — awaiting data
      </span>
    )
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold",
        gradeColors[grade] ?? "bg-muted text-muted-foreground",
      )}
      title={`Health score: ${score}/100`}
    >
      {grade} ({score})
    </span>
  )
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatMs(agg: MetricAggregation | undefined, field: "p50" | "p95" = "p50"): string {
  const val = agg?.[field]
  if (val === null || val === undefined) return "—"
  if (val < 1000) return `${Math.round(val)}ms`
  return `${(val / 1000).toFixed(1)}s`
}

function formatCost(val: number | null | undefined): string {
  if (val === null || val === undefined) return "—"
  if (val < 0.01) return `$${val.toFixed(4)}`
  if (val < 1) return `$${val.toFixed(3)}`
  return `$${val.toFixed(2)}`
}

function formatPct(val: number | null | undefined): string {
  if (val === null || val === undefined) return "—"
  return `${(val * 100).toFixed(1)}%`
}

function formatCount(val: number | null | undefined): string {
  if (val === null || val === undefined) return "—"
  return String(Math.round(val))
}

// ---------------------------------------------------------------------------
// Canonical pipeline stages — iterate this instead of API keys to guarantee order & completeness
// ---------------------------------------------------------------------------

const ALL_STAGES: PipelineStage[] = [
  "claim_extraction", "query_decomposition", "topic_extraction",
  "memory_resolution", "verification_simple", "verification_complex",
  "reranking", "chat_generation",
]

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ObservabilityDashboard() {
  const [windowMinutes, setWindowMinutes] = useState(60)

  const { data: metricsData, isLoading: metricsLoading } = useQuery({
    queryKey: ["observability-metrics", windowMinutes],
    queryFn: () => fetchObservabilityMetrics(windowMinutes),
    refetchInterval: 10_000,
  })

  const { data: healthData } = useQuery({
    queryKey: ["observability-health", windowMinutes],
    queryFn: () => fetchObservabilityHealthScore(windowMinutes),
    refetchInterval: 10_000,
  })

  const { data: healthStatus } = useQuery({
    queryKey: ["health-status"],
    queryFn: fetchHealthStatus,
    refetchInterval: 15_000,
  })

  const metrics = metricsData?.metrics

  // Build sparkline data from aggregation counts (we show the metric averages as
  // single-value sparklines when we don't have full time series).
  // In a production setting you'd query the time-series endpoint; here we keep
  // it lightweight with aggregated data.
  const sparklines = useMemo(() => {
    if (!metrics) return {}
    const result: Record<string, number[]> = {}
    for (const [name, agg] of Object.entries(metrics)) {
      const vals: number[] = []
      if (agg.min !== null) vals.push(agg.min)
      if (agg.p50 !== null) vals.push(agg.p50)
      if (agg.avg !== null) vals.push(agg.avg)
      if (agg.p95 !== null) vals.push(agg.p95)
      if (agg.max !== null) vals.push(agg.max)
      result[name] = vals
    }
    return result
  }, [metrics])

  // Pipeline routing stats
  const pipelineStats = useMemo(() => {
    const providers = healthStatus?.pipeline_providers
    if (!providers) return null
    const total = ALL_STAGES.length
    const ollamaCount = ALL_STAGES.filter((s) => providers[s] === "ollama").length
    return { total, ollamaCount, providers }
  }, [healthStatus?.pipeline_providers])

  // Cost: sum from the llm_cost_usd metric
  const totalCost = metrics?.llm_cost_usd
  const costValue = totalCost?.avg !== null && totalCost?.avg !== undefined && totalCost?.count
    ? totalCost.avg * totalCost.count
    : null

  return (
    <div className="space-y-3">
      {/* Header with time window selector */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Observability</h3>
        <div className="flex items-center gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w.minutes}
              onClick={() => setWindowMinutes(w.minutes)}
              className={cn(
                "rounded-md px-2 py-0.5 text-xs font-medium transition-colors",
                windowMinutes === w.minutes
                  ? "bg-teal-500/15 text-teal-700 dark:text-teal-400"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {/* Health score */}
      {healthData && (
        <div className="flex items-center gap-2">
          <Gauge className="h-4 w-4 text-teal-500" />
          <span className="text-xs font-medium">System Health:</span>
          <GradeBadge grade={healthData.grade} score={healthData.score} noData={healthData.factors && Object.values(healthData.factors as Record<string, { status?: string }>).filter(f => f?.status === "no_data").length >= 2} />
        </div>
      )}

      {/* Degradation Tier + Pipeline Routing cards */}
      {healthStatus && (
        <div className="grid grid-cols-2 gap-3">
          {/* Degradation Tier Card */}
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 p-3 pb-1">
              <CardTitle className="text-xs font-medium text-muted-foreground">
                Degradation Tier <InfoTip term="degradation-tier" />
              </CardTitle>
              <Shield className="h-4 w-4 text-teal-500" />
            </CardHeader>
            <CardContent className="p-3 pt-0">
              {(() => {
                const TIER_DISPLAY: Record<string, string> = {
                  full: "Healthy",
                  lite: "Lite Mode",
                  direct: "Direct",
                  cached: "Cache Only",
                  offline: "Offline",
                }
                return (
                  <span className={cn(
                    "inline-block rounded-md px-2 py-0.5 text-lg font-bold",
                    healthStatus.degradation_tier === "full" && "bg-green-500/15 text-green-700 dark:text-green-400",
                    healthStatus.degradation_tier === "lite" && "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
                    healthStatus.degradation_tier === "direct" && "bg-blue-500/15 text-blue-700 dark:text-blue-400",
                    healthStatus.degradation_tier === "cached" && "bg-orange-500/15 text-orange-700 dark:text-orange-400",
                    healthStatus.degradation_tier === "offline" && "bg-red-500/15 text-red-700 dark:text-red-400",
                  )}>
                    {TIER_DISPLAY[healthStatus.degradation_tier ?? ""] ?? healthStatus.degradation_tier ?? "Unknown"}
                  </span>
                )
              })()}
              <div className="mt-1.5 flex items-center gap-2 text-xs">
                <span className="flex items-center gap-1">
                  Retrieve
                  <span className={healthStatus.can_retrieve ? "text-green-500" : "text-red-500"}>●</span>
                </span>
                <span className="flex items-center gap-1">
                  Verify
                  <span className={healthStatus.can_verify ? "text-green-500" : "text-red-500"}>●</span>
                </span>
                <span className="flex items-center gap-1">
                  Generate
                  <span className={healthStatus.can_generate ? "text-green-500" : "text-red-500"}>●</span>
                </span>
              </div>
            </CardContent>
          </Card>

          {/* Pipeline Routing Card */}
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 p-3 pb-1">
              <CardTitle className="text-xs font-medium text-muted-foreground">
                Pipeline Routing <InfoTip term="pipeline-routing" />
              </CardTitle>
              <Cpu className="h-4 w-4 text-purple-500" />
            </CardHeader>
            <CardContent className="p-3 pt-0">
              <div className="text-xl font-bold tabular-nums leading-tight">
                {pipelineStats ? `${pipelineStats.ollamaCount}/${pipelineStats.total} local` : "—"}
              </div>
              {pipelineStats && (
                <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
                  {ALL_STAGES.map((stage) => {
                    const provider = pipelineStats.providers[stage] ?? "\u2014"
                    return (
                      <span key={stage}>
                        <span className="font-medium">{stage.replace(/_/g, " ")}</span>
                        {" "}
                        <span className={provider === "ollama" ? "text-green-500" : "text-blue-500"}>{provider}</span>
                      </span>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Metric cards grid */}
      {metricsLoading && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 p-3 pb-1">
                <div className="h-3 w-20 animate-pulse rounded bg-muted" />
                <div className="h-4 w-4 animate-pulse rounded bg-muted" />
              </CardHeader>
              <CardContent className="p-3 pt-0">
                <div className="h-7 w-16 animate-pulse rounded bg-muted" />
                <div className="mt-1.5 h-2.5 w-24 animate-pulse rounded bg-muted" />
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {!metricsLoading && !metrics && (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-muted-foreground/25 py-12 text-center">
          <Activity className="mb-3 h-8 w-8 text-muted-foreground/50" />
          <p className="text-sm font-medium text-muted-foreground">No metrics data available yet</p>
          <p className="mt-1 max-w-xs text-xs text-muted-foreground/70">
            Metrics are collected as queries are processed.
          </p>
        </div>
      )}

      {!metricsLoading && metrics && (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <MetricCard
          title="Query Latency (p50)"
          icon={Clock}
          value={formatMs(metrics?.query_latency_ms, "p50")}
          subtitle={`p95: ${formatMs(metrics?.query_latency_ms, "p95")}`}
          sparklineData={sparklines.query_latency_ms}
          accentColor="text-teal-500"
        />

        <MetricCard
          title="LLM Cost"
          icon={DollarSign}
          value={formatCost(costValue)}
          subtitle={`${formatCount(metrics?.llm_cost_usd?.count)} queries`}
          sparklineData={sparklines.llm_cost_usd}
          accentColor="text-amber-500"
        />

        <MetricCard
          title="Cache Hit Rate"
          icon={Database}
          value={formatPct(metrics?.cache_hit_rate?.avg)}
          subtitle={`${formatCount(metrics?.cache_hit_rate?.count)} lookups`}
          sparklineData={sparklines.cache_hit_rate}
          accentColor="text-blue-500"
        />

        <MetricCard
          title="Verification Accuracy"
          icon={ShieldCheck}
          value={formatPct(metrics?.verification_accuracy?.avg)}
          subtitle={`${formatCount(metrics?.verification_accuracy?.count)} checks`}
          sparklineData={sparklines.verification_accuracy}
          accentColor="text-green-500"
          infoTerm="response-verification"
        />

        <MetricCard
          title="Throughput (QPM)"
          icon={Activity}
          value={formatCount(metrics?.queries_per_minute?.count)}
          subtitle={`${windowMinutes >= 1440 ? Math.round(windowMinutes / 1440) + "d" : windowMinutes >= 60 ? Math.round(windowMinutes / 60) + "h" : windowMinutes + "m"} window`}
          sparklineData={sparklines.queries_per_minute}
          accentColor="text-purple-500"
        />

        <MetricCard
          title="Retrieval NDCG@5"
          icon={Zap}
          value={metrics?.retrieval_ndcg?.avg !== null && metrics?.retrieval_ndcg?.avg !== undefined
            ? metrics.retrieval_ndcg.avg.toFixed(3)
            : "—"}
          subtitle={`${formatCount(metrics?.retrieval_ndcg?.count)} evals`}
          sparklineData={sparklines.retrieval_ndcg}
          accentColor="text-teal-500"
          infoTerm="ndcg-at-5"
        />
      </div>
      )}
    </div>
  )
}
