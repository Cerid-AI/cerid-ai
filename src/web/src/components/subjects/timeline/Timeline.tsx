// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Subjects → Timeline — Stratigraph orchestrator (v2).
//
// 4-state matrix: Skeleton loading / destructive Alert error /
// EmptyState empty / populated StratigraphCanvas.
// Chrome parity with Constellation map mode: lens radiogroup, type
// filter chips, config popover, pinned shelf (amendment 4), community
// card, entity pin card, re-rank badge (amendment 5).
//
// recharts is intentionally absent from this file.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { AlertCircle, Clock, Settings2, X } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { resolveMapTokens, type MapTokens } from "@/components/subjects/constellation/map/community-layer"
import { useNavigation } from "@/contexts/navigation-context"
import { useTimelineStrata } from "./stratigraph/use-timeline-strata"
import {
  loadTimelineConfig,
  saveTimelineConfig,
  type TimelineConfig,
  type TrackBudget,
} from "./stratigraph/timeline-config"
import {
  StratigraphCanvas,
  type TimelineLens,
  type PinnedCommunity,
  type PinnedTrack,
} from "./stratigraph/StratigraphCanvas"
import { communitySlot } from "./stratigraph/strata-layout"

// ---------------------------------------------------------------------------
// Lens definitions (parity with Constellation)
// ---------------------------------------------------------------------------

const LENSES: { id: TimelineLens; label: string; hint: string }[] = [
  { id: "cluster", label: "Clusters", hint: "Color strata by knowledge community" },
  { id: "trust", label: "Trust", hint: "Severity-priority trust bands; unverified dominates" },
  { id: "type", label: "Types", hint: "Re-partition stack by entity type (no refetch)" },
]

const PERIODS = [
  { label: "7d", value: "7d" as const },
  { label: "30d", value: "30d" as const },
  { label: "90d", value: "90d" as const },
  { label: "1y", value: "365d" as const },
]

// ---------------------------------------------------------------------------
// Config popover
// ---------------------------------------------------------------------------

function TimelineConfigPanel({
  config,
  onChange,
}: {
  config: TimelineConfig
  onChange: (patch: Partial<TimelineConfig>) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Timeline settings"
        aria-expanded={open}
        className="rounded-lg border border-border/60 bg-card/80 px-2 py-1 text-label-xs text-muted-foreground backdrop-blur hover:bg-accent/40"
      >
        <Settings2 className="h-3 w-3" />
      </button>
      {open && (
        <div className="absolute bottom-full right-0 mb-1 w-56 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
          <div className="flex flex-col gap-2">
            {/* Track budget */}
            <div>
              <div className="mb-1 text-label-xs font-medium text-muted-foreground">Track budget</div>
              <div className="flex gap-0.5" role="radiogroup" aria-label="Track budget">
                {([8, 16, 24] as TrackBudget[]).map((v) => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={config.trackBudget === v}
                    onClick={() => onChange({ trackBudget: v })}
                    className={`rounded px-1.5 py-0.5 text-label-xs transition-colors ${
                      config.trackBudget === v
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/30"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>

            {/* Markers toggle */}
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.markersVisible}
                onChange={(e) => onChange({ markersVisible: e.target.checked })}
                className="rounded border-border/60"
              />
              Show event markers
            </label>

            {/* Ingest hatch toggle */}
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.ingestHatch}
                onChange={(e) => onChange({ ingestHatch: e.target.checked })}
                className="rounded border-border/60"
              />
              Hatch ingest bursts
            </label>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface TimelineProps {
  focalEntity?: string | null
  onEntityPick?: (id: string) => void
}

export function Timeline({ onEntityPick }: TimelineProps) {
  const { goTo } = useNavigation()

  // Config
  const [config, setConfig] = useState<TimelineConfig>(loadTimelineConfig)
  const handleConfig = useCallback((patch: Partial<TimelineConfig>) => {
    setConfig((prev) => {
      const next = { ...prev, ...patch }
      saveTimelineConfig(next)
      return next
    })
  }, [])

  // Period tab is mirrored from config so that period tabs write back to config
  const period = config.period
  const handlePeriod = useCallback((p: TimelineConfig["period"]) => {
    handleConfig({ period: p })
  }, [handleConfig])

  // Lens + type filter
  const [lens, setLens] = useState<TimelineLens>("cluster")
  const [typeFilter, setTypeFilter] = useState<Set<string>>(new Set())
  const toggleType = useCallback((t: string) => {
    setTypeFilter((prev) => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next
    })
  }, [])

  // Pinned entities (amendment 4: shelf above the stack)
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set())
  const togglePin = useCallback((id: string) => {
    setPinnedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // Frozen stratum order (amendment 5)
  const [frozenOrder, setFrozenOrder] = useState<string[] | null>(null)
  const [reRankAvailable, setReRankAvailable] = useState(false)
  // Adopt the brushed-window ranking as the new frozen order; null re-freezes
  // from the next full-window data pass.
  const windowRankingRef = useRef<string[] | null>(null)
  const handleReRank = useCallback(() => {
    setFrozenOrder(windowRankingRef.current?.length ? windowRankingRef.current : null)
    setReRankAvailable(false)
  }, [])

  // Cards
  const [pinnedCommunity, setPinnedCommunity] = useState<PinnedCommunity | null>(null)
  const [pinnedTrack, setPinnedTrack] = useState<PinnedTrack | null>(null)

  // LOD state
  const [lodLevel, setLodLevel] = useState<"era" | "bucket" | "track">("era")
  const handleLODChange = useCallback((level: "era" | "bucket" | "track") => {
    setLodLevel(level)
  }, [])

  // Brush window — pure client viewport state. It must never feed the query
  // (refetching on brush re-creates the brush, which would re-emit and loop);
  // it only drives the re-rank-for-this-window computation below.
  const [brushWindow, setBrushWindow] = useState<{ from: string; to: string } | null>(null)
  const handleBrushChange = useCallback((from: string, to: string) => {
    setBrushWindow({ from, to })
  }, [])

  // Tokens
  const [tokens, setTokens] = useState<MapTokens>(() => {
    if (typeof document !== "undefined") return resolveMapTokens(document.documentElement)
    return {
      clusters: Array(8).fill("#999"), // drift-allowed: SSR fallback only, never reaches browser
      clusterOther: "#999", // drift-allowed: SSR fallback only, never reaches browser
      edge: "#ccc", // drift-allowed: SSR fallback only, never reaches browser
      dim: "#eee", // drift-allowed: SSR fallback only, never reaches browser
      interaction: "#00c8b4", // drift-allowed: SSR fallback only, never reaches browser
      foreground: "#111", // drift-allowed: SSR fallback only, never reaches browser
      background: "#f5f5f5", // drift-allowed: SSR fallback only, never reaches browser
      trustVerified: "#333", // drift-allowed: SSR fallback only, never reaches browser
      trustPartial: "#555", // drift-allowed: SSR fallback only, never reaches browser
      trustUnverified: "#888", // drift-allowed: SSR fallback only, never reaches browser
      grid: "#eee", // drift-allowed: SSR fallback only, never reaches browser
    }
  })

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setTokens(resolveMapTokens(document.documentElement))
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [])

  // Reduced motion
  const reducedMotion = useMemo(
    () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
    [],
  )

  // Data fetch — keyed by period only; the brush zooms within this window.
  const { data, isLoading, isError, error } = useTimelineStrata({ period })

  // Freeze stratum order on first load
  useEffect(() => {
    if (!data || frozenOrder !== null) return
    const ranked = data.communities.filter((c) => !c.is_other)
      .sort((a, b) => b.total_mentions - a.total_mentions)
      .map((c) => c.community_id)
    setFrozenOrder(ranked)
  }, [data, frozenOrder])

  // Re-rank availability (amendment 5): rank communities by mention volume
  // inside the brushed window (full window when no brush) and flag when the
  // top-8 membership/order drifts from the frozen session order.
  const windowRanking = useMemo(() => {
    if (!data) return null
    const lo = brushWindow?.from ?? null
    const hi = brushWindow?.to ?? null
    const sums = new Map<string, number>()
    for (const s of data.series) {
      if (s.community_id === "other") continue
      let total = 0
      for (let i = 0; i < s.buckets.length; i++) {
        const d = data.bucket_dates[i]
        if ((lo && d < lo) || (hi && d > hi)) continue
        total += s.buckets[i] ?? 0
      }
      if (total > 0) sums.set(s.community_id, (sums.get(s.community_id) ?? 0) + total)
    }
    return [...sums.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([id]) => id)
      .slice(0, 8)
  }, [data, brushWindow])
  windowRankingRef.current = windowRanking

  useEffect(() => {
    if (!frozenOrder || !windowRanking) return
    const frozenTop = frozenOrder.slice(0, 8)
    setReRankAvailable(
      windowRanking.some((id, i) => frozenTop[i] !== id),
    )
  }, [frozenOrder, windowRanking])

  // Entity type chips from series
  const typeChips = useMemo(() => {
    if (!data) return []
    const counts = new Map<string, number>()
    for (const s of data.series) {
      const total = s.buckets.reduce((a, b) => a + b, 0)
      counts.set(s.entity_type, (counts.get(s.entity_type) ?? 0) + total)
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
  }, [data])

  // ---------------------------------------------------------------------------
  // 4-state matrix
  // ---------------------------------------------------------------------------

  if (isLoading) {
    return (
      <div className="flex h-full w-full flex-col gap-3 p-4" aria-busy="true" aria-label="Loading timeline">
        <div className="flex items-center gap-2">
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-5 w-20" />
          <Skeleton className="ml-auto h-5 w-32" />
        </div>
        <Skeleton className="h-8 w-full" />
        <Skeleton className="flex-1 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex h-full w-full items-center justify-center p-6">
        <Alert variant="destructive" className="max-w-md" role="alert">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            {error instanceof Error ? error.message : "Failed to load timeline strata."}
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  if (!data || data.bucket_dates.length === 0) {
    return (
      <div className="flex h-full w-full items-center justify-center p-12" data-testid="timeline-empty">
        <div className="max-w-md rounded-xl border border-dashed border-border bg-card/40 p-8 text-center">
          <Clock className="mx-auto mb-2 h-8 w-8 opacity-40" />
          <h2 className="text-lg font-semibold text-foreground">No timeline data yet</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            The stratigraph builds after your first ingestion. Ingest a document
            and activity will appear here.
          </p>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Populated state
  // ---------------------------------------------------------------------------

  const pinnedTrackEntries = data.tracks.filter((t) => pinnedIds.has(t.canonical_id))

  return (
    <div
      className="relative flex h-full w-full flex-col bg-background"
      data-testid="timeline-mode"
    >
      {/* Header toolbar */}
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-border/40 px-3 py-1.5">
        {/* Lens radiogroup — parity with Constellation */}
        <div
          className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
          role="radiogroup"
          aria-label="Color lens"
        >
          {LENSES.map((l) => (
            <button
              key={l.id}
              type="button"
              role="radio"
              aria-checked={lens === l.id}
              onClick={() => setLens(l.id)}
              title={l.hint}
              className={`rounded-md px-2 py-1 text-label-xs transition-colors ${
                lens === l.id
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/40"
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>

        {/* Type filter chips */}
        {typeChips.length > 1 && (
          <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Filter by entity type">
            {typeChips.map(([t, n]) => (
              <button
                key={t}
                type="button"
                aria-pressed={typeFilter.has(t)}
                onClick={() => toggleType(t)}
                className={`rounded-full border px-2 py-0.5 text-label-xs transition-colors ${
                  typeFilter.has(t)
                    ? "border-accent bg-accent/30 text-accent-foreground"
                    : "border-border/60 bg-card/70 text-muted-foreground hover:bg-accent/20"
                }`}
              >
                {t} <span className="opacity-60">{n}</span>
              </button>
            ))}
            {typeFilter.size > 0 && (
              <button
                type="button"
                onClick={() => setTypeFilter(new Set())}
                className="rounded-full px-2 py-0.5 text-label-xs text-muted-foreground underline-offset-2 hover:underline"
              >
                clear
              </button>
            )}
          </div>
        )}

        <div className="ml-auto flex items-center gap-1.5">
          {/* Re-rank badge (amendment 5) */}
          {reRankAvailable && (
            <button
              type="button"
              onClick={handleReRank}
              className="rounded-lg border border-border/60 bg-card/80 px-2 py-1 text-label-xs text-muted-foreground backdrop-blur hover:bg-accent/40"
              title="Community ranking has shifted; click to re-rank for this window"
            >
              Re-rank
            </button>
          )}

          {/* Period tabs */}
          <div role="tablist" aria-label="Time period" className="flex gap-0.5">
            {PERIODS.map((p) => (
              <button
                key={p.value}
                role="tab"
                aria-selected={p.value === period}
                onClick={() => handlePeriod(p.value)}
                className={`rounded px-2 py-0.5 text-label-xs transition-colors ${
                  p.value === period
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/40"
                }`}
                data-testid={`timeline-period-${p.value}`}
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Config popover */}
          <TimelineConfigPanel config={config} onChange={handleConfig} />
        </div>
      </div>

      {/* Pinned shelf — amendment 4 */}
      {pinnedTrackEntries.length > 0 && (
        <div
          className="flex shrink-0 items-center gap-2 overflow-x-auto border-b border-border/40 px-3 py-1"
          role="list"
          aria-label="Pinned entities"
        >
          {pinnedTrackEntries.map((t) => {
            const slot = communitySlot(t.community_id)
            const hueColor = tokens.clusters[slot] ?? tokens.clusterOther
            return (
              <div
                key={t.canonical_id}
                role="listitem"
                className="flex shrink-0 items-center gap-1.5 rounded-md border border-border/60 bg-card/80 px-2 py-1 text-label-xs"
                style={{ borderLeftColor: hueColor, borderLeftWidth: 3 }} // drift-allowed: runtime stratum hue tint
              >
                <span className="text-foreground">{t.name}</span>
                <button
                  type="button"
                  aria-label={`Unpin ${t.name}`}
                  onClick={() => togglePin(t.canonical_id)}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              </div>
            )
          })}
        </div>
      )}

      {/* Canvas — fills remaining space */}
      <div className="relative min-h-0 flex-1">
        <StratigraphCanvas
          data={data}
          lens={lens}
          typeFilter={typeFilter}
          pinnedIds={pinnedIds}
          frozenOrder={frozenOrder}
          trackBudget={config.trackBudget}
          markersVisible={config.markersVisible}
          ingestHatch={config.ingestHatch}
          lodLevel={lodLevel}
          onLODChange={handleLODChange}
          tokens={tokens}
          onCommunityClick={(community) => {
            setPinnedCommunity((prev) =>
              prev?.communityId === community.communityId ? null : community
            )
            setPinnedTrack(null)
          }}
          onTrackClick={(track) => {
            setPinnedTrack((prev) =>
              prev?.canonicalId === track.canonicalId ? null : track
            )
            togglePin(track.canonicalId)
            setPinnedCommunity(null)
          }}
          onBrushChange={handleBrushChange}
          reducedMotion={reducedMotion}
        />

        {/* Community card (Open in Atlas) */}
        {pinnedCommunity && (
          <div className="absolute bottom-12 left-3 w-72 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-foreground">
                  {pinnedCommunity.label}
                </div>
                <div className="mt-0.5 text-label-xs text-muted-foreground">
                  {pinnedCommunity.totalMentions.toLocaleString()} mentions
                </div>
              </div>
              <button
                type="button"
                onClick={() => setPinnedCommunity(null)}
                aria-label="Close community card"
                className="rounded p-1 text-muted-foreground hover:bg-accent/40"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            {pinnedCommunity.topHubs.length > 0 && (
              <div className="mt-2">
                <div className="text-label-xs font-medium text-muted-foreground">Top entities</div>
                <div className="mt-1 flex flex-col gap-0.5">
                  {pinnedCommunity.topHubs.slice(0, 5).map((hub) => (
                    <button
                      key={hub.canonical_id}
                      type="button"
                      onClick={() => onEntityPick?.(hub.canonical_id)}
                      className="rounded px-1.5 py-0.5 text-left text-label-xs text-foreground hover:bg-accent/30"
                    >
                      {hub.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <button
              type="button"
              onClick={() => goTo("subjects", { mode: "atlas" })}
              className="mt-2 w-full rounded-md bg-accent px-2 py-1.5 text-label-xs font-medium text-accent-foreground hover:bg-accent/80"
            >
              Open in Atlas
            </button>
          </div>
        )}

        {/* Entity pin card (Open in Wiki) */}
        {pinnedTrack && (
          <div className="absolute bottom-12 right-3 w-72 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-foreground">
                  {pinnedTrack.name}
                </div>
                <div className="mt-0.5 text-label-xs text-muted-foreground">
                  <span className="uppercase">{pinnedTrack.trustState}</span>
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  setPinnedTrack(null)
                  togglePin(pinnedTrack.canonicalId)
                }}
                aria-label="Close entity card"
                className="rounded p-1 text-muted-foreground hover:bg-accent/40"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            <button
              type="button"
              onClick={() => {
                if (onEntityPick) onEntityPick(pinnedTrack.canonicalId)
                goTo("subjects", { mode: "wiki", entity: pinnedTrack.canonicalId })
              }}
              className="mt-2 w-full rounded-md bg-accent px-2 py-1.5 text-label-xs font-medium text-accent-foreground hover:bg-accent/80"
            >
              Open in Wiki
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

export default Timeline
