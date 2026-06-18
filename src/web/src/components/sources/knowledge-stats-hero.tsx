// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { cn } from "@/lib/utils"
import { Sparkline } from "@/components/ui/sparkline"
import {
  fetchKnowledgeStats,
  fetchKnowledgeStatsHistory,
  type KnowledgeStats,
  type KnowledgeStatsHistorySnapshot,
} from "@/lib/api/knowledge-stats"

/**
 * F9 — Knowledge Stats hero card. The trophy case in numeric form.
 * Pinned at the top of every Sources sub-tab.
 *
 * Five orthogonal corpus dimensions, each with a 7d / 30d sparkline.
 * Click-through to filtered destinations per the plan:
 *   * artifacts → Library tab
 *   * chunks    → Library tab with chunk-breakdown view
 *   * entities  → Subjects pane (Atlas mode)
 *   * diversity → Sources Constellation toggle
 *   * age       → first-ever artifact
 *
 * Numbers pulse via `.metric-pulse` on update. Diversity bar uses
 * the gold→teal gradient from the opening sequence and fills
 * segment-by-segment as new source kinds are connected (22 total).
 */

const TOTAL_SOURCE_KINDS = 22 // 11 Core + 11 Pro per core.ingest.sources.kinds

interface MetricCardProps {
  label: string
  value: number
  delta24h?: number
  sparkValues: number[]
  onClick?: () => void
  ariaLabel?: string
}

function MetricCard({
  label,
  value,
  delta24h,
  sparkValues,
  onClick,
  ariaLabel,
}: MetricCardProps) {
  const formatted = value.toLocaleString()
  // metric-pulse animates when value changes — re-key the value span
  // on every value change so the CSS animation restarts. Value is
  // already an integer, so cheap to use as the key directly.
  const pulseKey = value

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel ?? `${label}: ${formatted}`}
      className={cn(
        "cerid-press group flex flex-col items-start gap-1 rounded-lg px-3 py-2 text-left transition-colors",
        "hover:bg-accent/40",
        !onClick && "cursor-default hover:bg-transparent",
      )}
      disabled={!onClick}
    >
      <span
        key={pulseKey}
        className="metric-value-pulse text-2xl font-medium tabular-nums text-foreground"
      >
        {formatted}
      </span>
      <span className="text-label-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {typeof delta24h === "number" && delta24h > 0 && (
        <span className="text-label-xs text-brand">
          ↑ {delta24h.toLocaleString()} today
        </span>
      )}
      <Sparkline values={sparkValues} className="mt-0.5 opacity-90" />
    </button>
  )
}

interface KnowledgeStatsHeroProps {
  /**
   * Click handlers per metric. Optional — when omitted the cards
   * render non-interactive. Callers wire navigation via the existing
   * NavigationContext.
   */
  onArtifactsClick?: () => void
  onChunksClick?: () => void
  onEntitiesClick?: () => void
  onDiversityClick?: () => void
  onAgeClick?: () => void
  /** Window for sparkline data. Defaults to 7d. */
  defaultWindow?: 7 | 30
}

export function KnowledgeStatsHero({
  onArtifactsClick,
  onChunksClick,
  onEntitiesClick,
  onDiversityClick,
  onAgeClick,
  defaultWindow = 7,
}: KnowledgeStatsHeroProps) {
  const [window, setWindow] = useState<7 | 30>(defaultWindow)

  const { data: stats } = useQuery<KnowledgeStats>({
    queryKey: ["knowledge-stats"],
    queryFn: fetchKnowledgeStats,
    refetchInterval: 30_000,
    staleTime: 25_000,
  })

  const { data: history } = useQuery<KnowledgeStatsHistorySnapshot[]>({
    queryKey: ["knowledge-stats-history", window],
    queryFn: async () => (await fetchKnowledgeStatsHistory(window)).snapshots,
    refetchInterval: 60_000,
    staleTime: 55_000,
  })

  if (!stats) {
    return (
      <div className="liquid-glass rounded-xl px-5 py-4">
        <div className="h-24 animate-pulse rounded-md bg-muted/30" />
      </div>
    )
  }

  // Build per-metric sparkline values. Falls back to a flat
  // current-value line when the history endpoint hasn't accumulated
  // yet (fresh deploy, first day of corpus).
  const fallback = (current: number) => [current]
  const series = (
    pick: (s: KnowledgeStatsHistorySnapshot) => number,
    current: number,
  ) => {
    if (!history || history.length === 0) return fallback(current)
    return history.map(pick).concat([current])
  }

  const totalEdges =
    stats.edges.mentions +
    stats.edges.relates_to +
    stats.edges.wikilinks +
    stats.edges.from_source +
    stats.edges.has_contradiction

  const diversityPct =
    TOTAL_SOURCE_KINDS > 0
      ? Math.round((stats.diversity.source_kinds / TOTAL_SOURCE_KINDS) * 100)
      : 0

  return (
    <div className="liquid-glass rounded-xl px-5 py-4">
      {/* Header row: title + age + window toggle */}
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-baseline gap-3">
          <h2 className="text-sm font-medium text-foreground">Your knowledge</h2>
          {stats.growth.first_artifact_at && (
            <button
              type="button"
              onClick={onAgeClick}
              className="cerid-press text-label-xs text-muted-foreground hover:text-foreground"
              aria-label={`Corpus age: ${stats.growth.corpus_age_days} days`}
            >
              age {stats.growth.corpus_age_days} day
              {stats.growth.corpus_age_days === 1 ? "" : "s"}
            </button>
          )}
        </div>
        {/* 7d / 30d window pill */}
        <div className="flex items-center gap-1 rounded-full border border-border/40 bg-background/30 p-0.5 text-label-xs">
          <button
            type="button"
            onClick={() => setWindow(7)}
            className={cn(
              "cerid-press rounded-full px-2 py-0.5 transition-colors",
              window === 7
                ? "bg-foreground/10 text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
            aria-pressed={window === 7}
            aria-label="7-day sparkline window"
          >
            7d
          </button>
          <button
            type="button"
            onClick={() => setWindow(30)}
            className={cn(
              "cerid-press rounded-full px-2 py-0.5 transition-colors",
              window === 30
                ? "bg-foreground/10 text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
            aria-pressed={window === 30}
            aria-label="30-day sparkline window"
          >
            30d
          </button>
        </div>
      </div>

      {/* Metric row — 5 cards, evenly spaced */}
      <div className="grid grid-cols-5 gap-2">
        <MetricCard
          label="Artifacts"
          value={stats.nodes.artifacts}
          delta24h={stats.growth.artifacts_24h}
          sparkValues={series((s) => s.nodes.artifacts, stats.nodes.artifacts)}
          onClick={onArtifactsClick}
        />
        <MetricCard
          label="Chunks"
          value={stats.chunks}
          sparkValues={series((s) => s.chunks, stats.chunks)}
          onClick={onChunksClick}
        />
        <MetricCard
          label="Entities"
          value={stats.nodes.entities}
          sparkValues={series((s) => s.nodes.entities, stats.nodes.entities)}
          onClick={onEntitiesClick}
        />
        <MetricCard
          label="Edges"
          value={totalEdges}
          sparkValues={series(
            (s) =>
              s.edges.mentions +
              s.edges.relates_to +
              s.edges.wikilinks +
              s.edges.from_source +
              s.edges.has_contradiction,
            totalEdges,
          )}
        />
        <MetricCard
          label="Diversity"
          value={stats.diversity.source_kinds}
          sparkValues={series(
            (s) => s.diversity.source_kinds,
            stats.diversity.source_kinds,
          )}
          onClick={onDiversityClick}
          ariaLabel={`${stats.diversity.source_kinds} of ${TOTAL_SOURCE_KINDS} source kinds connected`}
        />
      </div>

      {/* Diversity segmented bar — 22 segments, gold→teal */}
      <div className="mt-3">
        <div className="mb-1 flex items-center justify-between text-label-xs text-muted-foreground">
          <span>Source kinds</span>
          <span>
            {stats.diversity.source_kinds} / {TOTAL_SOURCE_KINDS} ({diversityPct}%)
          </span>
        </div>
        <div className="flex h-1.5 gap-0.5">
          {Array.from({ length: TOTAL_SOURCE_KINDS }).map((_, i) => {
            const filled = i < stats.diversity.source_kinds
            // gold→teal gradient: leftmost = gold (newest filled),
            // rightmost = teal (longest filled). Use t = i / total.
            const t = i / Math.max(TOTAL_SOURCE_KINDS - 1, 1)
            const fillColor = `color-mix(in oklch, oklch(0.78 0.12 85) ${
              (1 - t) * 100
            }%, oklch(0.82 0.16 178))`
            return (
              <div
                key={i}
                className={cn(
                  "h-full flex-1 rounded-full transition-colors",
                  !filled && "bg-muted/30",
                )}
                style={filled ? { backgroundColor: fillColor } : undefined}
              />
            )
          })}
        </div>
      </div>
    </div>
  )
}
