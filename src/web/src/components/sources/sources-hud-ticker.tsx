// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F6 — Live ingestion HUD ticker.
 *
 * A thin strip mounted above the F9 Knowledge Stats hero. Shows:
 *  - Total artifacts (mirrors F9 but lives compact)
 *  - 60s rolling ingestion rate (from SSE delta)
 *  - Median connection time across all sources (gamification headline)
 *  - Source diversity (kinds connected / 22)
 *
 * Values pulse via .metric-value-pulse on update. The HUD is
 * intentionally narrow so it never competes with the F9 hero for
 * visual weight — F9 is the trophy case, F6 is the speedometer.
 */

import { AlertCircle, Loader2 } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { fetchKnowledgeStats, type KnowledgeStats } from "@/lib/api/knowledge-stats"
import { listSources, type SourceRecord } from "@/lib/api/sources"
import {
  anySyncActive,
  fetchSyncStates,
  liveRatePerMin,
  type ConnectorSyncState,
} from "@/lib/api/sync-state"
import { cn } from "@/lib/utils"

const TOTAL_SOURCE_KINDS = 22

export function SourcesHudTicker() {
  const { data: stats, isError: statsError } = useQuery<KnowledgeStats>({
    queryKey: ["knowledge-stats"],
    queryFn: fetchKnowledgeStats,
    refetchInterval: 30_000,
    staleTime: 25_000,
  })
  const { data: sources } = useQuery<SourceRecord[]>({
    queryKey: ["sources"],
    queryFn: () => listSources(),
    refetchInterval: 60_000,
    staleTime: 55_000,
  })
  // Shared sync state (sf-1): during a live connector sync the 24h-average
  // rate below reads ~0.0/min while ingest runs at full tilt (UX-22). Poll
  // fast while a sync is active, lazily otherwise.
  // eslint-disable-next-line cerid/no-query-error-as-empty -- deliberate: on fetch failure the ticker falls back to the 24h-average rate it always showed
  const { data: syncStates } = useQuery<ConnectorSyncState[]>({
    queryKey: ["ingestion-sync-state"],
    queryFn: fetchSyncStates,
    refetchInterval: (query) =>
      anySyncActive(query.state.data ?? []) ? 5_000 : 30_000,
  })

  // A bare `if (!stats) return null` made a failed fetch look identical to a
  // still-loading one: the whole strip silently disappeared and the numbers it
  // had been showing simply stopped existing, with nothing to indicate the
  // figures were stale rather than zero.
  if (statsError) {
    return (
      <div
        role="status"
        className="flex items-center gap-2 px-3 py-1.5 text-xs text-muted-foreground"
      >
        <AlertCircle className="h-3.5 w-3.5 text-amber-500" aria-hidden="true" />
        <span>Live stats unavailable — figures may be out of date.</span>
      </div>
    )
  }

  if (!stats) return null

  const medianConnectMs = medianConnectTime(sources ?? [])
  const syncActive = anySyncActive(syncStates ?? [])
  // While a sync is live, show its actual window rate from the shared sync
  // state instead of the 24h average that reads ~0 mid-ingest (UX-22).
  const ingestionRatePerMin = syncActive
    ? liveRatePerMin(syncStates ?? [])
    : typeof stats.growth.artifacts_24h === "number"
      ? Math.round((stats.growth.artifacts_24h / (24 * 60)) * 10) / 10
      : 0
  const activeSyncs = (syncStates ?? []).filter(
    (s) => s.state === "syncing" || s.state === "ingesting" || s.state === "stalled",
  )

  return (
    <div className="flex items-center justify-between border-b border-border/40 bg-card/20 px-4 py-1.5 text-label-xs text-muted-foreground">
      <div className="flex items-center gap-4">
        <Stat label="artifacts" value={stats.nodes.artifacts.toLocaleString()} />
        <Stat label="/min" value={ingestionRatePerMin.toFixed(1)} />
        <Stat
          label="median connect"
          value={medianConnectMs !== null ? `${medianConnectMs} ms` : "—"}
        />
        {activeSyncs.map((s) => (
          <span
            key={s.connector}
            className={cn(
              "flex items-center gap-1",
              s.state === "stalled" ? "text-amber-500" : "text-foreground",
            )}
            data-testid={`hud-sync-${s.connector}`}
          >
            {s.state !== "stalled" && (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            )}
            {s.state === "stalled" ? "stalled" : "syncing"} {s.connector.replace(/_/g, " ")}
            {s.total > 0 && (
              <span className="tabular-nums">
                {Math.min(Math.max(s.posted, s.window_ingested), s.total)}/{s.total}
              </span>
            )}
          </span>
        ))}
      </div>
      <Stat
        label="kinds"
        value={`${stats.diversity.source_kinds} / ${TOTAL_SOURCE_KINDS}`}
      />
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex items-baseline gap-1">
      <span
        key={value}
        className={cn(
          "metric-value-pulse tabular-nums text-foreground",
        )}
      >
        {value}
      </span>
      <span className="text-label-xs text-muted-foreground">{label}</span>
    </span>
  )
}

function medianConnectTime(sources: SourceRecord[]): number | null {
  const values = sources
    .map((s) => s.connection_time_ms)
    .filter((v): v is number => typeof v === "number" && v > 0)
    .sort((a, b) => a - b)
  if (values.length === 0) return null
  const mid = Math.floor(values.length / 2)
  return values.length % 2 === 0
    ? Math.round((values[mid - 1] + values[mid]) / 2)
    : values[mid]
}
