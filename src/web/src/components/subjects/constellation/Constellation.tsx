// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Constellation — dual-mode knowledge-graph view.
//   "map" (default): flat 2D Cartographer map (sigma.js v3, no physics)
//   "live": cosmos.gl self-organizing GPU force layout
// (The R3F "3d" mode was cut 2026-08-13 — same layout as the map with
// recency as z, it added a 1.2 MB vendor chunk for an occluded sphere
// cloud. The guided tour now runs on the 2D map: map/map-tour.tsx.)
//
// View mode persists in localStorage "cerid-constellation-mode", default "map".
// Both modes share the same server x/y layout so the mental map is stable.

import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Loader2, Minimize2, Maximize2, Play, Pause, Shuffle, GitMerge } from "lucide-react"
import { PaneError } from "@/components/ui/pane-error"
import { Slider } from "@/components/ui/slider"
import { fetchEmbeddings3D } from "@/lib/api/embeddings-3d"
import { communityRgb, trustRgb, typeRgb, domainRgb } from "./palette"
import { CartographerMap } from "./map/CartographerMap"
import { useGraphMap } from "./map/use-graph-map"
import { loadMapConfig, saveMapConfig, type MapConfig } from "./map/map-config"
import { Timebar } from "./map/timebar"
import { buildTimeHistogram } from "./map/time-window"
import { StructuralGapsPanel } from "./map/structural-gaps-panel"
import { fetchStructuralGaps, type StructuralGap } from "@/lib/api/graph-structural-gaps"
import { MapTourPanel, useMapTour } from "./map/map-tour"
import type { CommunityHull } from "@/lib/api/graph-map"
import { useNavigation } from "@/contexts/navigation-context"
import { resolveMapTokens, type MapTokens } from "./map/community-layer"
import type { MapLayoutV2 as MapLayout } from "@/lib/graph/cycle4-contracts"

// ---------------------------------------------------------------------------
// View-mode persistence
// ---------------------------------------------------------------------------

type ViewMode = "map" | "live"
const VIEW_MODE_KEY = "cerid-constellation-mode"
const VIEW_MODE_LABEL: Record<ViewMode, string> = { map: "Map", live: "Live" }

function loadViewMode(): ViewMode {
  try {
    const stored = localStorage.getItem(VIEW_MODE_KEY)
    // Migration: persisted "3d" (retired mode) lands on the default map.
    if (stored === "map" || stored === "live") return stored
  } catch {
    // storage unavailable
  }
  return "map"
}

function saveViewMode(mode: ViewMode): void {
  try {
    localStorage.setItem(VIEW_MODE_KEY, mode)
  } catch {
    // storage unavailable
  }
}

type ColorLens = "cluster" | "trust" | "type" | "domain" | "bridges"
// mapOnly lenses need the 2D graph structure (e.g. betweenness); the Live
// scene has no graphology graph to compute over.
const COLOR_LENSES: { id: ColorLens; label: string; hint: string; mapOnly?: boolean }[] = [
  { id: "cluster", label: "Clusters", hint: "Color by knowledge community" },
  { id: "trust", label: "Trust", hint: "Verification bands: green verified · amber partial · red unverified" },
  { id: "type", label: "Types", hint: "Color by entity type" },
  { id: "domain", label: "Domains", hint: "Color by primary knowledge domain (hash-stable; icon + label identify collisions)" },
  { id: "bridges", label: "Bridges", hint: "Betweenness centrality — highlights the connector entities that bridge otherwise-separate clusters", mapOnly: true },
]

// cosmos.gl "Live" mode (B8) is its own lazy chunk (vendor-cosmos) — only
// loaded when the user switches to the self-organizing scene.
const CosmosLive = lazy(() => import("./cosmos-live"))

export interface ConstellationProps {
  /** Initial focal entity (optional — UMAP shows global view by default) */
  focalEntity?: string
  /** Optional entity-type filter */
  filter?: string | null
  /** Click handler — fires when user clicks a node */
  onNodeClick?: (entityId: string) => void
}

// ---------------------------------------------------------------------------
// MapConfigPanel — small chip-row popover for edge budget / label density / hulls.
// Matches existing overlay chip styling in Constellation.tsx.
// ---------------------------------------------------------------------------

function MapConfigPanel({
  config,
  onChange,
  includeIsolated,
  onIncludeIsolatedChange,
  isolatedCount,
}: {
  config: MapConfig
  onChange: (patch: Partial<MapConfig>) => void
  includeIsolated: boolean
  onIncludeIsolatedChange: (v: boolean) => void
  isolatedCount: number
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Map settings"
        aria-expanded={open}
        className="rounded-lg border border-border/60 bg-card/80 px-2 py-1 text-label-xs text-muted-foreground backdrop-blur hover:bg-accent/40"
      >
        Map settings
      </button>
      {open && (
        <div className="absolute bottom-full right-0 mb-1 w-52 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
          <div className="flex flex-col gap-2">
            {/* Edge budget */}
            <div>
              <div className="mb-1 text-label-xs font-medium text-muted-foreground">Edges</div>
              <div className="flex gap-0.5" role="radiogroup" aria-label="Edge budget">
                {(["off", "2k", "8k", "all"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={config.edgeBudget === v}
                    onClick={() => onChange({ edgeBudget: v })}
                    className={`rounded px-1.5 py-0.5 text-label-xs transition-colors ${
                      config.edgeBudget === v
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/30"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>

            {/* Label density */}
            <div>
              <div className="mb-1 text-label-xs font-medium text-muted-foreground">Labels</div>
              <div className="flex gap-0.5" role="radiogroup" aria-label="Label density">
                {(["sparse", "normal", "rich"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={config.labelDensity === v}
                    onClick={() => onChange({ labelDensity: v })}
                    className={`rounded px-1.5 py-0.5 text-label-xs capitalize transition-colors ${
                      config.labelDensity === v
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/30"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>

            {/* Territories mode (A4): nebula = canvas hulls, contours = GPU metaballs */}
            <div>
              <div className="mb-1 text-label-xs font-medium text-muted-foreground">Regions</div>
              <div className="flex gap-0.5" role="radiogroup" aria-label="Region rendering">
                {(["off", "nebula", "contours"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={config.territories === v}
                    onClick={() => onChange({ territories: v })}
                    className={`rounded px-1.5 py-0.5 text-label-xs capitalize transition-colors ${
                      config.territories === v
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/30"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>

            {/* Live layout toggle */}
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.liveLayout}
                onChange={(e) => onChange({ liveLayout: e.target.checked })}
                className="rounded border-border/60"
              />
              Live motion
            </label>

            {/* Hide orphans toggle */}
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.hideOrphans}
                onChange={(e) => onChange({ hideOrphans: e.target.checked })}
                className="rounded border-border/60"
              />
              Hide orphans
            </label>

            {/* Collapse communities toggle */}
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.collapseCommunities}
                onChange={(e) => onChange({ collapseCommunities: e.target.checked })}
                className="rounded border-border/60"
              />
              Collapse communities
            </label>

            {/* Periphery toggle — rim shell + singleton tail; hidden when count is 0 */}
            {isolatedCount > 0 && (
              <label
                className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground"
                title="Entities the map can’t integrate yet — unlinked or in sub-scale clusters; rendered on the outer rim when shown"
              >
                <input
                  type="checkbox"
                  checked={includeIsolated}
                  onChange={(e) => onIncludeIsolatedChange(e.target.checked)}
                  className="rounded border-border/60"
                />
                Periphery ({isolatedCount})
              </label>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function Constellation({ focalEntity, filter, onNodeClick }: ConstellationProps) {
  const { composeChat } = useNavigation()

  // ---------------------------------------------------------------------------
  // View mode: "map" (Cartographer 2D) | "live" (cosmos.gl)
  // ---------------------------------------------------------------------------
  const [viewMode, setViewMode] = useState<ViewMode>(loadViewMode)
  const handleViewMode = useCallback((mode: ViewMode) => {
    saveViewMode(mode)
    setViewMode(mode)
  }, [])

  // ---------------------------------------------------------------------------
  // Map config (edge budget, label density, hulls on/off)
  // ---------------------------------------------------------------------------
  const [mapConfig, setMapConfig] = useState<MapConfig>(loadMapConfig)
  const handleMapConfig = useCallback((patch: Partial<MapConfig>) => {
    setMapConfig((prev) => {
      const next = { ...prev, ...patch }
      saveMapConfig(next)
      return next
    })
  }, [])

  // ---------------------------------------------------------------------------
  // Layout state — drives per-layout query key + presets
  // ---------------------------------------------------------------------------
  const [layout, setLayout] = useState<MapLayout>("force")
  const handleLayout = useCallback((next: MapLayout) => setLayout(next), [])

  // ---------------------------------------------------------------------------
  // Isolated-node toggle — off by default (graph shows connected core)
  // ---------------------------------------------------------------------------
  const [includeIsolated, setIncludeIsolated] = useState(false)

  // ---------------------------------------------------------------------------
  // Cartographer map data (only fetches when in "map" mode to save bandwidth)
  // ---------------------------------------------------------------------------
  const {
    data: mapData,
    isLoading: mapLoading,
    isError: mapError,
    error: mapErrorObj,
    refetch: refetchMap,
    drainNewIds,
  } = useGraphMap(layout, includeIsolated)

  // ---------------------------------------------------------------------------
  // Community card state (shown when a hull/community label is clicked)
  // ---------------------------------------------------------------------------
  const [pinnedCommunity, setPinnedCommunity] = useState<CommunityHull | null>(null)
  // Per-community combos (A10): level-0 community ids collapsed into a disc.
  const [manualCollapsed, setManualCollapsed] = useState<ReadonlySet<string>>(() => new Set())
  // Timebar (A9) window + timelapse (A8) cursor. The cursor advances in
  // discrete steps (not per-frame) so playback never churns React.
  const [timeWindow, setTimeWindow] = useState<[number, number] | null>(null)
  const [playing, setPlaying] = useState(false)
  const [playCursor, setPlayCursor] = useState<number | null>(null)
  const toggleManualCollapsed = useCallback((id: string) => {
    setManualCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // Effective time filter read by the map's node reducer: brush window +
  // playback cursor (cursor only while playing).
  const timeFilter = useMemo(
    () =>
      timeWindow || playing
        ? { window: timeWindow, cursor: playing ? playCursor : null }
        : null,
    [timeWindow, playing, playCursor],
  )

  const handleCommunityClick = useCallback((community: CommunityHull) => {
    setPinnedCommunity((prev) => (prev?.id === community.id ? null : community))
  }, [])

  // ---------------------------------------------------------------------------
  // Structural gaps (C2): the advisory panel. Fetches only when open + in map
  // mode. Hovering a gap highlights its two hulls; "Explore in chat" seeds the
  // composer to reason about bridging them.
  // ---------------------------------------------------------------------------
  const [showGaps, setShowGaps] = useState(false)
  const [gapHighlight, setGapHighlight] = useState<ReadonlySet<string> | undefined>(undefined)
  const gapsQuery = useQuery({
    queryKey: ["graph-structural-gaps"],
    queryFn: ({ signal }) => fetchStructuralGaps(8, signal),
    enabled: viewMode === "map" && showGaps,
    staleTime: 10 * 60 * 1000,
  })
  const handleGapHover = useCallback((gap: StructuralGap | null) => {
    setGapHighlight(gap ? new Set([gap.community_a.id, gap.community_b.id]) : undefined)
  }, [])
  const handleGapExplore = useCallback(
    (gap: StructuralGap) => {
      const bridges = gap.bridging_candidates.map((c) => c.name).join(", ")
      composeChat({
        text:
          `My knowledge graph shows "${gap.community_a.label}" and "${gap.community_b.label}" are closely related in meaning ` +
          `but barely connected. How are they linked, and what should I capture to bridge them?` +
          (bridges ? ` Possible bridging entities: ${bridges}.` : ""),
      })
    },
    [composeChat],
  )

  // ---------------------------------------------------------------------------
  // Embeddings payload for the cosmos "Live" scene.
  // ---------------------------------------------------------------------------
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["constellation-embeddings-3d", focalEntity ?? null, filter ?? null, includeIsolated],
    queryFn: ({ signal }) => fetchEmbeddings3D({ filter, includeIsolated, signal }),
    // The corpus is alive: the on-ingest subscriber + manual Run-now both
    // recompute the projection and bust the server cache. Poll cheaply
    // (Redis cache hit when nothing changed) and keep the previous frame
    // on screen while refetching so growth animates in without a flash.
    staleTime: 60 * 1000,
    refetchInterval: 75 * 1000,
    placeholderData: (prev) => prev,
    enabled: viewMode === "live",
  })

  // Guided tour on the 2D map (Pro): generates the backend arc and frames
  // each stop by animating the sigma camera via CartographerMap.tourFocus.
  const mapTour = useMapTour()

  // Resolved tokens for the Live scene (domainRgb needs hex resolved from CSS vars).
  // Lazy-init from DOM; re-resolves on theme change (same pattern as CartographerMap/Atlas).
  const [tokens3D, setTokens3D] = useState<MapTokens>(() =>
    typeof document !== "undefined" ? resolveMapTokens(document.documentElement) : {
      clusters: Array(8).fill("#888888") as string[], // drift-allowed: SSR fallback only
      clusterOther: "#888888", // drift-allowed: SSR fallback only
      domains: Array(12).fill("#888888") as string[], // drift-allowed: SSR fallback only
      domainOther: "#666666", // drift-allowed: SSR fallback only
      edge: "#888888", // drift-allowed: SSR fallback only
      dim: "#888888", // drift-allowed: SSR fallback only
      interaction: "#00C8B4", // drift-allowed: SSR fallback only
      foreground: "#111111", // drift-allowed: SSR fallback only
      background: "#f5f5f5", // drift-allowed: SSR fallback only
      trustVerified: "#555555", // drift-allowed: SSR fallback only
      trustPartial: "#777777", // drift-allowed: SSR fallback only
      trustUnverified: "#999999", // drift-allowed: SSR fallback only
      graphite: "#6b7080", // drift-allowed: SSR fallback only
      grid: "#eeeeee", // drift-allowed: SSR fallback only
    }
  )

  // Re-resolve domain tokens when theme changes (Live mode only — map mode
  // has its own token observer inside CartographerMap/Atlas).
  useEffect(() => {
    if (viewMode !== "live") return
    const observer = new MutationObserver(() => {
      setTokens3D(resolveMapTokens(document.documentElement))
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [viewMode])

  // --- Exploration tools: lens recoloring, type filter ---
  const [lens, setLens] = useState<ColorLens>("cluster")
  const [typeFilter, setTypeFilter] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState("")

  // Top entity types by frequency (map toolbar chips).
  const typeChips = useMemo(() => {
    const counts = new Map<string, number>()
    for (const e of mapData?.entities ?? []) {
      counts.set(e.type, (counts.get(e.type) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6)
  }, [mapData?.entities])

  // Lens colors for the Live scene: cluster = community palette, trust =
  // verification bands, type = per-type hue, domain = token-routed domain hue.
  // One Float32Array upload, GPU does the rest.
  const nodeColors = useMemo(() => {
    const ents = data?.entities ?? []
    const arr = new Float32Array(ents.length * 3)
    for (let i = 0; i < ents.length; i++) {
      const rgb = lens === "trust"
        ? trustRgb(ents[i].trust_state)
        : lens === "type"
          ? typeRgb(ents[i].type)
          : lens === "domain"
            ? domainRgb(tokens3D, ents[i].primary_domain ?? null)
            : communityRgb(ents[i].community)
      arr[i * 3] = rgb[0]; arr[i * 3 + 1] = rgb[1]; arr[i * 3 + 2] = rgb[2]
    }
    return arr
  // tokens3D is included so theme swaps re-derive colors for the domain lens.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.entities, lens, tokens3D])

  const toggleType = useCallback((t: string) => {
    setTypeFilter((prev) => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next
    })
  }, [])

  // Reduced motion collapses growth, pulses, breathing, and auto-rotate.
  const animate = useMemo(
    () => !(typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches),
    [],
  )

  // cosmos.gl "Live" mode controls (B8). Starts frozen under reduced motion.
  const [liveRunning, setLiveRunning] = useState(() => animate)
  const [liveRepulsion, setLiveRepulsion] = useState(1.0)
  const [liveBigBang, setLiveBigBang] = useState(0)

  // Timelapse playback (A8): step a growth cursor over the entity birth span
  // in discrete frames (~48 over 12s) so nodes appear in creation order.
  // Discrete steps, not per-frame rAF, keep React churn negligible.
  const togglePlay = useCallback(() => setPlaying((p) => !p), [])
  useEffect(() => {
    if (!playing) {
      setPlayCursor(null)
      return
    }
    const hist = buildTimeHistogram(mapData?.entities ?? [], 48)
    if (!hist) {
      setPlaying(false)
      return
    }
    const STEPS = 48
    const STEP_MS = 250
    let step = 0
    setPlayCursor(hist.minMs)
    const id = setInterval(() => {
      step += 1
      const t = hist.minMs + ((hist.maxMs - hist.minMs) * step) / STEPS
      setPlayCursor(t)
      if (step >= STEPS) {
        clearInterval(id)
        // Hold the full corpus for a beat, then stop.
        setTimeout(() => setPlaying(false), 600)
      }
    }, STEP_MS)
    return () => clearInterval(id)
  }, [playing, mapData])

  // ---------------------------------------------------------------------------
  // Map mode renders CartographerMap — return early with the full map layout.
  // ---------------------------------------------------------------------------
  if (viewMode === "map") {
    // Drain newly ingested entity ids on each render so pulse rings fire once.
    const newIds = drainNewIds()
    return (
      <div
        className="relative h-full w-full bg-background"
        role="application"
        aria-roledescription="knowledge map"
        aria-label="Cartographer knowledge map"
      >
        {/* Exploration toolbar — lens + type filter */}
        <div className="absolute left-3 top-3 z-10 flex flex-col gap-1.5">
          <div
            className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
            role="radiogroup"
            aria-label="Color lens"
          >
            {COLOR_LENSES.map((l) => (
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
          {typeChips.length > 1 && (
            <div className="flex max-w-md flex-wrap items-center gap-1" role="group" aria-label="Filter by entity type">
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
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search nodes…"
            aria-label="Search knowledge map"
            className="rounded-md border border-border bg-card/60 px-2 py-1 text-label-xs text-foreground placeholder:text-muted-foreground"
          />
        </div>

        {/* Map scene */}
        <CartographerMap
          focalEntity={focalEntity}
          filter={filter}
          lens={lens}
          typeFilter={typeFilter}
          config={mapConfig}
          data={mapData}
          isLoading={mapLoading}
          isError={mapError}
          errorMessage={mapErrorObj instanceof Error ? mapErrorObj.message : undefined}
          onRetry={() => void refetchMap()}
          newEntityIds={newIds.size > 0 ? newIds : undefined}
          onInspect={onNodeClick}
          onCommunityClick={handleCommunityClick}
          manualCollapsed={manualCollapsed}
          onManualExpand={toggleManualCollapsed}
          timeFilter={timeFilter}
          layout={layout}
          layoutFallback={mapData?.layout_fallback}
          search={search}
          highlightCommunities={gapHighlight}
          tourFocus={mapTour.focus}
        />

        {/* Guided tour (Pro) — button top-center; narration panel while playing */}
        <div className="pointer-events-none absolute left-1/2 top-3 z-10 flex -translate-x-1/2 justify-center">
          <div className="pointer-events-auto">
            <MapTourPanel tour={mapTour} />
          </div>
        </div>

        {/* Structural-gaps advisory panel (C2) — top-left, over the map */}
        {showGaps && (
          <StructuralGapsPanel
            gaps={gapsQuery.data?.gaps ?? []}
            isLoading={gapsQuery.isLoading}
            isError={gapsQuery.isError}
            errorMessage={gapsQuery.error instanceof Error ? gapsQuery.error.message : undefined}
            onClose={() => {
              setShowGaps(false)
              setGapHighlight(undefined)
            }}
            onHoverGap={handleGapHover}
            onExplore={handleGapExplore}
          />
        )}

        {/* Timebar (A9 filter + A8 timelapse) — bottom-center over the map */}
        <div className="pointer-events-none absolute bottom-3 left-1/2 z-10 -translate-x-1/2">
          <Timebar
            entities={mapData?.entities ?? []}
            window={timeWindow}
            onWindowChange={setTimeWindow}
            cursor={playing ? playCursor : null}
            playing={playing}
            onTogglePlay={togglePlay}
            canPlay={animate}
          />
        </div>

        {/* Community card (shown when a hull is clicked) */}
        {pinnedCommunity && (
          <div className="absolute bottom-3 left-3 w-72 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-foreground">
                  {pinnedCommunity.label}
                </div>
                <div className="mt-0.5 text-label-xs text-muted-foreground">
                  {pinnedCommunity.count} entities
                </div>
              </div>
              <button
                type="button"
                onClick={() => setPinnedCommunity(null)}
                aria-label="Close community card"
                className="rounded p-1 text-muted-foreground hover:bg-accent/40"
              >
                ✕
              </button>
            </div>
            {pinnedCommunity.top_hubs.length > 0 && (
              <div className="mt-2">
                <div className="text-label-xs font-medium text-muted-foreground">Top hubs</div>
                <div className="mt-1 flex flex-col gap-0.5">
                  {pinnedCommunity.top_hubs.slice(0, 5).map((hub) => (
                    <button
                      key={hub.id}
                      type="button"
                      onClick={() => onNodeClick?.(hub.id)}
                      className="rounded px-1.5 py-0.5 text-left text-label-xs text-foreground hover:bg-accent/30"
                    >
                      {hub.name}
                      <span className="ml-1 opacity-50">{hub.degree}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div className="mt-2 flex gap-1.5">
              <button
                type="button"
                onClick={() => toggleManualCollapsed(pinnedCommunity.id)}
                className="flex flex-1 items-center justify-center gap-1 rounded-md border border-border/60 px-2 py-1.5 text-label-xs font-medium text-foreground hover:bg-accent/40"
              >
                {manualCollapsed.has(pinnedCommunity.id) ? (
                  <>
                    <Maximize2 className="size-3" aria-hidden="true" />
                    Expand
                  </>
                ) : (
                  <>
                    <Minimize2 className="size-3" aria-hidden="true" />
                    Collapse
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (pinnedCommunity.top_hubs[0]) onNodeClick?.(pinnedCommunity.top_hubs[0].id)
                }}
                className="flex-1 rounded-md bg-accent px-2 py-1.5 text-label-xs font-medium text-accent-foreground hover:bg-accent/80"
              >
                Open in Atlas
              </button>
            </div>
          </div>
        )}

        {/* Bottom-right controls: layout presets + map config + view mode toggle */}
        <div className="absolute bottom-3 right-3 z-10 flex items-center gap-1.5">
          {/* Layout presets — client-side, no Redis rows (free-tier cap respected) */}
          <div
            className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
            role="radiogroup"
            aria-label="Layout preset"
          >
            {(
              [
                { id: "force", label: "Default map", hint: "Force-directed layout (default)" },
                { id: "wells", label: "Tight clusters", hint: "Well-separated cluster layout" },
                { id: "domain", label: "Domains apart", hint: "Domain-separated layout" },
                { id: "semantic", label: "Semantics", hint: "Embedding-space layout — position reflects meaning" },
              ] as { id: MapLayout; label: string; hint: string }[]
            ).map((preset) => (
              <button
                key={preset.id}
                type="button"
                role="radio"
                aria-checked={layout === preset.id}
                title={preset.hint}
                onClick={() => handleLayout(preset.id)}
                className={`rounded-md px-2 py-1 text-label-xs transition-colors ${
                  layout === preset.id
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/40"
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>

          {/* Structural gaps toggle (C2) */}
          <button
            type="button"
            onClick={() => {
              setShowGaps((v) => !v)
              if (showGaps) setGapHighlight(undefined)
            }}
            aria-pressed={showGaps}
            title="Structural gaps — topics that should connect but don't yet"
            className={`flex items-center gap-1 rounded-lg border px-2 py-1 text-label-xs backdrop-blur transition-colors ${
              showGaps
                ? "border-accent bg-accent/30 text-accent-foreground"
                : "border-border/60 bg-card/80 text-muted-foreground hover:bg-accent/40"
            }`}
          >
            <GitMerge className="h-3 w-3" aria-hidden="true" />
            Gaps
          </button>

          {/* Map config popover */}
          <MapConfigPanel
            config={mapConfig}
            onChange={handleMapConfig}
            includeIsolated={includeIsolated}
            onIncludeIsolatedChange={setIncludeIsolated}
            isolatedCount={mapData?.isolated_count ?? 0}
          />

          {/* View mode toggle */}
          <div
            className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
            role="radiogroup"
            aria-label="View mode"
          >
            {(["map", "live"] as ViewMode[]).map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={viewMode === m}
                onClick={() => handleViewMode(m)}
                className={`rounded-md px-2 py-1 text-label-xs transition-colors ${
                  viewMode === m
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/40"
                }`}
              >
                {VIEW_MODE_LABEL[m]}
              </button>
            ))}
          </div>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Live mode (B8) — cosmos.gl self-organizing GPU force layout. Its own
  // canvas/WebGL context (never shared with sigma).
  // ---------------------------------------------------------------------------
  return (
      <div
        className="relative h-full w-full"
        style={{ background: tokens3D.background }} // drift-allowed: token-routed runtime value
        role="application"
        aria-roledescription="Live self-organizing knowledge graph"
        aria-label={`Live constellation of ${data?.count ?? 0} entities`}
      >
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading live graph…
          </div>
        ) : isError ? (
          <PaneError
            fullPage
            title="Failed to load the live graph"
            description={error instanceof Error ? error.message : undefined}
            onRetry={() => void refetch()}
          />
        ) : !data || data.entities.length === 0 ? (
          <div className="flex h-full items-center justify-center p-12">
            <div className="max-w-md rounded-xl border border-dashed border-border bg-card/40 p-8 text-center">
              <h2 className="text-lg font-semibold text-foreground">No graph to simulate yet</h2>
              <p className="mt-2 text-sm text-muted-foreground">
                Live mode self-organizes your entities in real time. Ingest a
                document and the projection recomputes within a few minutes.
              </p>
            </div>
          </div>
        ) : (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Warming up the simulation…
              </div>
            }
          >
            <CosmosLive
              entities={data.entities}
              links={data.links ?? []}
              colors={nodeColors}
              playing={liveRunning}
              repulsion={liveRepulsion}
              bigBangNonce={liveBigBang}
              reducedMotion={!animate}
              background={tokens3D.background}
              onNodeClick={(id) => onNodeClick?.(id)}
            />
            {/* Live controls — play/pause, re-run big bang, repulsion. */}
            <div className="pointer-events-none absolute inset-x-0 bottom-4 z-20 flex justify-center">
              <div className="liquid-glass pointer-events-auto flex items-center gap-3 rounded-full px-3 py-1.5">
                <button
                  type="button"
                  onClick={() => setLiveRunning((v) => !v)}
                  aria-pressed={liveRunning}
                  aria-label={liveRunning ? "Pause simulation" : "Run simulation"}
                  className="flex items-center gap-1 rounded-full px-2 py-0.5 text-label-xs text-foreground hover:bg-accent/40"
                >
                  {liveRunning ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                  {liveRunning ? "Pause" : "Run"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setLiveBigBang((n) => n + 1)
                    setLiveRunning(true)
                  }}
                  aria-label="Re-run big bang"
                  className="flex items-center gap-1 rounded-full px-2 py-0.5 text-label-xs text-muted-foreground hover:bg-accent/40"
                >
                  <Shuffle className="h-3.5 w-3.5" />
                  Big Bang
                </button>
                <div className="flex items-center gap-1.5">
                  <span className="text-label-xs text-muted-foreground">Repulsion</span>
                  <Slider
                    aria-label="Simulation repulsion"
                    value={[liveRepulsion]}
                    onValueChange={([v]) => setLiveRepulsion(v)}
                    min={0.1}
                    max={2}
                    step={0.1}
                    className="w-24"
                  />
                </div>
              </div>
            </div>
          </Suspense>
        )}

        {/* View mode toggle (bottom-right) */}
        <div className="absolute bottom-3 right-3">
          <div
            className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
            role="radiogroup"
            aria-label="View mode"
          >
            {(["map", "live"] as ViewMode[]).map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={viewMode === m}
                onClick={() => handleViewMode(m)}
                className={`rounded-md px-2 py-1 text-label-xs transition-colors ${
                  viewMode === m ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/40"
                }`}
              >
                {VIEW_MODE_LABEL[m]}
              </button>
            ))}
          </div>
        </div>
      </div>
  )
}
